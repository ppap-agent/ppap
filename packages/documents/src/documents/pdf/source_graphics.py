"""Source-cell graphic overlap shared by native extraction and audit readers."""

from __future__ import annotations

from typing import Any


def annotate_native_cells(source: bytes, page_index: int, grid: Any, *, tolerance: float) -> None:
    """Keep native values; mark only cells with uninterpreted original ink."""
    import fitz

    rectangles = [[grid.cols[cell.col_start], grid.rows[cell.row_start], grid.cols[cell.col_end + 1], grid.rows[cell.row_end + 1]] for cell in grid.cells]
    with fitz.open(stream=source, filetype="pdf") as document:
        page = document[page_index]
        visual = native_visual_overlaps(page, rectangles, tolerance=tolerance, grid_axes=(grid.cols, grid.rows))
        readable = native_text_visibility(page, rectangles, [cell.content for cell in grid.cells], tolerance=tolerance, visuals=visual)
        transformed = bool(page.rotation or page.cropbox_position.x or page.cropbox_position.y)
        for cell, bounds, unknown, text_readable in zip(grid.cells, rectangles, visual, readable, strict=True):
            if (unknown and text_readable is not True) or text_readable is False or transformed:
                cell.is_unknown = True
                cell.source_page = page_index + 1
                cell.source_bbox = None if transformed else tuple(bounds)
                cell.source_value = cell.content


def native_text_visibility(page: Any, cells: list[list[float]], values: list[str], *, tolerance: float = 1.0, visuals: list[bool] | None = None) -> list[bool | None]:
    """Confirm native scalar text without interpreting neighbouring graphics.

    A cell can contain a separate mark while its native text remains readable.
    A later fill / image over that text cannot be ignored as a background.
    Empty cells make no scalar claim and retain the full-cell visual guard.
    """
    import fitz

    if not any(value.strip() for value in values):
        return [None] * len(values)
    try:
        traces = page.get_texttrace()
        textpage = page.get_textpage()
        drawings = page.get_drawings()
        images = [(index, bounds) for index, (kind, bounds) in enumerate(page.get_bboxlog()) if kind == "fill-image"]
        overlays = [list(item.rect) for item in (page.annots() or ())] + [list(item.rect) for item in (page.widgets() or ())]
        underlines = native_text_underlines(page, drawings, tolerance=tolerance)
    except (AttributeError, TypeError, ValueError):
        return [None] * len(values)

    def compact(value: str) -> str:
        return "".join(value.split())

    result: list[bool | None] = []
    first_text = min((trace["seqno"] for trace in traces), default=float("inf"))
    covering_fills = [tuple(drawing["rect"]) for drawing in drawings if drawing.get("fill") is not None and drawing.get("fill_opacity", 1) > 0 and drawing.get("seqno", 0) > first_text]
    drawing_bounds = [(index, drawing, _expanded(drawing["rect"], float(drawing.get("width") or 0) / 2)) for index, drawing in enumerate(drawings) if index not in underlines]
    for position, (cell, value) in enumerate(zip(cells, values, strict=True)):
        # The existing grid guard already proves ordinary border/background
        # cells safe. Only recheck graphic cells and later whole-cell masks.
        if (
            visuals is not None
            and not visuals[position]
            and not any(bounds[0] <= cell[0] + tolerance and bounds[1] <= cell[1] + tolerance and bounds[2] >= cell[2] - tolerance and bounds[3] >= cell[3] - tolerance for bounds in covering_fills)
        ):
            result.append(None)
            continue
        required = compact(value)
        if not required or required not in compact(textpage.extractTextbox(fitz.Rect(cell))):
            result.append(None)
            continue
        characters = [
            (chr(character[0]), character[3], trace["seqno"])
            for trace in traces
            if trace.get("type") != 3 and trace.get("opacity", 1) > 0 and _intersects(cell, trace["bbox"])
            for character in trace["chars"]
            if not chr(character[0]).isspace() and cell[0] <= (character[3][0] + character[3][2]) / 2 <= cell[2] and cell[1] <= (character[3][1] + character[3][3]) / 2 <= cell[3]
        ]
        start = "".join(character for character, _bounds, _sequence in characters).find(required)
        if start < 0:
            characters.sort(key=lambda item: (round((item[1][1] + item[1][3]) / 2, 1), item[1][0]))
            start = "".join(character for character, _bounds, _sequence in characters).find(required)
        if start < 0:
            result.append(None)
            continue
        characters = characters[start : start + len(required)]
        spans: dict[tuple[int, float], list[float]] = {}
        for _character, bounds, sequence in characters:
            key = (sequence, round((bounds[1] + bounds[3]) / 2, 1))
            if key not in spans:
                spans[key] = list(bounds)
            else:
                previous = spans[key]
                spans[key] = [min(previous[0], bounds[0]), min(previous[1], bounds[1]), max(previous[2], bounds[2]), max(previous[3], bounds[3])]
        local_drawings = [(index, drawing, bounds) for index, drawing, bounds in drawing_bounds if _intersects(cell, bounds)]
        obscured = False
        for (sequence, _line), bounds in spans.items():
            if any(_intersects(bounds, overlay) for overlay in overlays) or any(index > sequence and _intersects(bounds, image) for index, image in images):
                obscured = True
                break
            for _index, drawing, drawing_box in local_drawings:
                if not _intersects(bounds, drawing_box):
                    continue
                if drawing.get("fill") is not None and drawing.get("fill_opacity", 1) > 0 and drawing.get("seqno", sequence + 1) > sequence:
                    obscured = True
                    break
                if drawing.get("color") is not None and drawing.get("stroke_opacity", 1) > 0:
                    stroke = {**drawing, "fill": None}
                    if _vector_inside(stroke, _expanded(bounds, float(drawing.get("width") or 0) / 2), 0, set()):
                        obscured = True
                        break
            if obscured:
                break
        result.append(not obscured)
    return result


def _expanded(bounds: Any, amount: float) -> list[float]:
    return [bounds[0] - amount, bounds[1] - amount, bounds[2] + amount, bounds[3] + amount]


def native_visual_overlaps(page: Any, cells: list[list[float]], *, tolerance: float = 1.0, grid_axes: tuple[list[float], list[float]] | None = None) -> list[bool]:
    """Report unexplained source ink without assigning its business meaning."""
    images = [image["bbox"] for image in page.get_image_info()]
    images.extend(list(annotation.rect) for annotation in (page.annots() or ()))
    images.extend(list(widget.rect) for widget in (page.widgets() or ()))
    drawings = page.get_drawings()
    underlines = native_text_underlines(page, drawings, tolerance=tolerance)
    xs = grid_axes[0] if grid_axes is not None else [value for cell in cells for value in (cell[0], cell[2])]
    ys = grid_axes[1] if grid_axes is not None else [value for cell in cells for value in (cell[1], cell[3])]
    # A merged cell can span several source background tiles. Retain the raw
    # grid axes; a complete row/column band proves a neighbouring tile only
    # for the boundary sliver exception in _rectangle_inside. Interior fills
    # and source marks still remain unknown.
    aligned_fills = {
        tuple(item[1])
        for drawing in drawings
        if drawing.get("fill") is not None
        for item in drawing["items"]
        if item[0] == "re"
        if all(item[1][axis + 2] - item[1][axis] > 2 * tolerance for axis in (0, 1))
        and any(all(any(abs(item[1][edge] - value) <= tolerance for value in bounds) for edge in edges) for edges, bounds in (((0, 2), xs), ((1, 3), ys)))
    }
    return [
        any(_intersects(cell, image) for image in images)
        or any(
            _vector_inside(drawing, cell, tolerance, aligned_fills)
            for index, drawing in enumerate(drawings)
            if index not in underlines
            if max(cell[0], drawing["rect"][0]) <= min(cell[2], drawing["rect"][2]) and max(cell[1], drawing["rect"][1]) <= min(cell[3], drawing["rect"][3])
        )
        for cell in cells
    ]


def native_text_underlines(page: Any, drawings: list[dict[str, Any]], *, tolerance: float) -> set[int]:
    """Recognize only a single thin stroke aligned below a whole native span."""
    if not drawings:
        return set()
    spans = [span for block in page.get_text("dict")["blocks"] for line in block.get("lines", []) for span in line["spans"] if span["text"].strip()]
    result = set()
    for index, drawing in enumerate(drawings):
        if drawing.get("fill") is not None or drawing.get("closePath") or len(drawing["items"]) != 1 or not 0 < (drawing.get("width") or 0) <= tolerance:
            continue
        item = drawing["items"][0]
        if item[0] != "l" or abs(item[1].y - item[2].y) > 0.01:
            continue
        left, right = sorted((item[1].x, item[2].x))
        y = item[1].y
        if any(
            abs(left - span["bbox"][0]) <= tolerance and abs(right - span["bbox"][2]) <= tolerance and span["origin"][1] < y <= min(span["bbox"][3], span["origin"][1] + span["size"] * 0.15)
            for span in spans
        ):
            result.add(index)
    return result


def _intersects(a: Any, b: Any) -> bool:
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def _line_inside(start: Any, end: Any, cell: list[float], tolerance: float) -> bool:
    """Intersect a segment with the open cell, excluding grid-boundary strokes."""
    for axis in (0, 1):
        along = 1 - axis
        if (
            start[axis] == end[axis]
            and any(abs(start[axis] - cell[edge]) <= tolerance for edge in (axis, axis + 2))
            and min(start[along], end[along]) <= cell[along]
            and max(start[along], end[along]) >= cell[along + 2]
        ):
            return False
    low, high = 0.0, 1.0
    for axis in (0, 1):
        delta = end[axis] - start[axis]
        if delta == 0:
            if not cell[axis] < start[axis] < cell[axis + 2]:
                return False
        else:
            first, last = sorted(((cell[axis] - start[axis]) / delta, (cell[axis + 2] - start[axis]) / delta))
            low, high = max(low, first), min(high, last)
    return low < high


def _rectangle_inside(rect: Any, cell: list[float], tolerance: float, *, filled: bool, grid_aligned: bool) -> bool:
    # A rectangle surrounding the entire cell is a border / uniform background.
    # This is a geometric distinction, independent of colour and field meaning.
    if rect[0] <= cell[0] + tolerance and rect[1] <= cell[1] + tolerance and rect[2] >= cell[2] - tolerance and rect[3] >= cell[3] - tolerance:
        return False
    # A background aligned with another grid region can spill slightly over a
    # reconstructed neighbour edge. Do not mistake that sliver for cell content.
    if filled and grid_aligned and min(min(rect[axis + 2], cell[axis + 2]) - max(rect[axis], cell[axis]) for axis in (0, 1)) <= tolerance:
        return False
    # Neighbour tiles and borders can extend beyond the reconstructed grid.
    # Ignore only a boundary sliver whose rectangle lies outside the cell and
    # either spans the other axis or also grazes its outside corner. Interior
    # marks remain unknown; this does not depend on fill colour or field text.
    if filled:
        outside = [(rect[axis] < cell[axis] and rect[axis + 2] <= cell[axis] + tolerance) or (rect[axis + 2] > cell[axis + 2] and rect[axis] >= cell[axis + 2] - tolerance) for axis in (0, 1)]
        if any(outside) and all(outside[axis] or (rect[axis] <= cell[axis] + tolerance and rect[axis + 2] >= cell[axis + 2] - tolerance) for axis in (0, 1)):
            return False
    # Exported table rules can be filled thin rectangles rather than strokes.
    # Require a full edge for a rectangle not proved to be outside the cell.
    for axis in (0, 1):
        along = 1 - axis
        if (
            rect[axis + 2] - rect[axis] <= 2 * tolerance
            and any(abs((rect[axis] + rect[axis + 2]) / 2 - cell[edge]) <= tolerance for edge in (axis, axis + 2))
            and rect[along] <= cell[along] + tolerance
            and rect[along + 2] >= cell[along + 2] - tolerance
        ):
            return False
    if filled and _intersects(rect, cell):
        return True
    corners = ((rect[0], rect[1]), (rect[2], rect[1]), (rect[2], rect[3]), (rect[0], rect[3]))
    return any(_line_inside(corners[index], corners[(index + 1) % 4], cell, tolerance) for index in range(4))


def _vector_inside(path: dict[str, Any], cell: list[float], tolerance: float, aligned_fills: set[tuple[float, ...]]) -> bool:
    """Keep unexplained path geometry; never infer a graphic's business meaning."""
    items, filled = path["items"], path.get("fill") is not None
    # General fills can cover a cell even when every contour is outside it.
    # Only axis-aligned rectangle fills have the background exception above.
    if filled and any(item[0] != "re" for item in items) and _intersects(path["rect"], cell):
        return True
    first = last = None
    for item in items:
        kind = item[0]
        if kind == "re":
            if _rectangle_inside(item[1], cell, tolerance, filled=filled, grid_aligned=tuple(item[1]) in aligned_fills):
                return True
        elif kind == "l":
            if _line_inside(item[1], item[2], cell, tolerance):
                return True
            if last != item[1]:
                first = item[1]
            last = item[2]
        elif kind == "c":
            # The control hull contains the curve. Ambiguous intersections stay
            # unknown rather than treating an unparsed curve as blank evidence.
            points = item[1:]
            bounds = [min(point.x for point in points), min(point.y for point in points), max(point.x for point in points), max(point.y for point in points)]
            if _intersects(bounds, cell):
                return True
            if last != points[0]:
                first = points[0]
            last = points[-1]
        elif kind == "qu":
            quad = item[1]
            points = (quad.ul, quad.ur, quad.lr, quad.ll)
            if any(_line_inside(points[index], points[(index + 1) % 4], cell, tolerance) for index in range(4)):
                return True
        elif _intersects(path["rect"], cell):
            return True
    return bool(path.get("closePath") and first is not None and last is not None and _line_inside(last, first, cell, tolerance))
