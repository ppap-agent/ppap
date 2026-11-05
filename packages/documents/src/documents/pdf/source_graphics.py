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
        transformed = bool(page.rotation or page.cropbox_position.x or page.cropbox_position.y)
        for cell, bounds, unknown in zip(grid.cells, rectangles, visual, strict=True):
            if unknown or transformed:
                cell.is_unknown = True
                cell.source_page = page_index + 1
                cell.source_bbox = None if transformed else tuple(bounds)
                cell.source_value = cell.content


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
