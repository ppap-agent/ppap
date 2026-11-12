"""Page-local native PDF grids and their source geometry; no OCR or model calls."""

from __future__ import annotations

import functools
import hashlib
import math
import re
from bisect import bisect_left, bisect_right
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .document_tools import ReadLimits, col_number, ref, within


class _LayoutMismatch(ValueError):
    """A candidate page layout did not reproduce the original page grids."""


def _normalize_heading(value: Any) -> str:
    text = re.sub(r"(?m)^\s*\d+\s*/\s*\d+\s*$", "", str(value or ""))
    return re.sub(r"\s+", "", text).casefold()


def _first_row(table: dict[str, Any]) -> tuple[str, ...]:
    values = [_normalize_heading(item.get("value")) for address, item in sorted(table["cells"].items()) if re.fullmatch(r"[A-Z]+1", address) and item.get("state") == "known"]
    return tuple(value for value in values if value)


def _layout_specs(original: dict[str, Any]) -> list[Any]:
    """Page layouts to try, most specific first; the per-page grids remain the basic fallback."""
    from documents.pdf import DEFAULT_SPEC, GroupingMode, GroupingSpec, PdfSpec

    from .kinds import PpapDocumentType

    rows = [_first_row(table) for table in original["tables"]]
    specs = []
    known = {_normalize_heading(alias) for kind in PpapDocumentType if kind != PpapDocumentType.UNKNOWN for alias in kind.iter_aliases()}
    if rows and all(len(set(row)) == 1 for row in rows) and len({row[0] for row in rows}) == 1 and rows[0][0] in known:
        specs.append(DEFAULT_SPEC)  # One titled form printed across every page.
    # Consecutive pages that repeat their first row continue one table.
    ranges, start = [], 0
    for index in range(1, len(rows) + 1):
        if index == len(rows) or not rows[index] or rows[index] != rows[start]:
            ranges.append((start, index - 1))
            start = index
    if any(last > first for first, last in ranges):
        specs.append(PdfSpec(grouping=GroupingSpec(mode=GroupingMode.EXPLICIT_RANGES, explicit_ranges=tuple(ranges))))
    return specs


def prepare_pdf_workbook(path: str, original: dict[str, Any], limits: ReadLimits) -> dict[str, Any]:
    """Consume the existing converter's workbook without losing original cell facts."""
    if [table["page"] for table in original["tables"]] != list(range(1, len(original["tables"]) + 1)):
        return original
    for spec in _layout_specs(original):
        try:
            return _prepare_with(path, original, limits, spec)
        except _LayoutMismatch:
            continue
    return original


def _prepare_with(path: str, original: dict[str, Any], limits: ReadLimits, spec: Any) -> dict[str, Any]:
    from documents.pdf import convert_pdf_to_excel
    from documents.pdf._stitch import _build_boundary_index_map, _remap_cell_columns

    from .document_tools import snapshot

    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != original["source"]["sha256"]:
        raise ValueError("PDF source changed during preparation")
    with TemporaryDirectory(prefix="ppap-uaes-pdf-") as directory:
        target = Path(directory) / "prepared.xlsx"
        converted = convert_pdf_to_excel(raw, str(target), spec=spec)
        prepared = snapshot(target, limits=limits)
    groups = [group for group in converted.extraction.groups if group.success and group.table_grid is not None]
    if len(groups) != len(prepared["tables"]):
        raise _LayoutMismatch("PDF workbook groups do not match their extracted sources")
    originals = {table["page"]: table for table in original["tables"]}
    for group, table in zip(groups, prepared["tables"]):
        grid = group.table_grid
        assert grid is not None
        if table["rows"] != len(grid.rows) - 1 or table["cols"] != len(grid.cols) - 1:
            raise _LayoutMismatch("PDF workbook dimensions do not match extraction")
        for row in range(1, table["rows"] + 1):
            for column in range(1, table["cols"] + 1):
                item = table["cells"].setdefault(ref(row, column), {"value": None, "state": "known"})
                item["conversion_state"] = item["state"]
                item["state"] = "unknown_conversion_source"
        offset = 0
        pages = [page for page in group.page_results if page.success and page.page_index in group.pages]
        for index, page in enumerate(pages):
            source = originals[page.page_index + 1]
            source_grid = page.table_grid
            if source["rows"] != len(source_grid.rows) - 1 or source["cols"] != len(source_grid.cols) - 1:
                raise ValueError("PDF original grid changed during preparation")
            skip = int(converted.extraction.spec.stitch.drop_repeated_header and index > 0)
            boundaries = _build_boundary_index_map(source_grid.cols, grid.cols)
            for cell in source_grid.cells:
                if cell.row_end < skip:
                    continue
                first, _last = _remap_cell_columns(cell, boundaries)
                if first >= table["cols"]:
                    continue
                start_row = max(cell.row_start, skip) - skip + offset + 1
                start_col = first + 1
                item = table["cells"][ref(start_row, start_col)]
                fact = source["cells"][ref(cell.row_start + 1, cell.col_start + 1)]
                same_value = str(item["value"] if item["value"] is not None else "") == str(fact["value"] if fact["value"] is not None else "")
                if not same_value:
                    continue
                state = fact["state"] if item["conversion_state"] == "known" else item["conversion_state"]
                item.update({**fact, "value": item["value"], "state": state})
                item["source"] = {**fact["source"], "row": cell.row_start + 1, "column": cell.col_start + 1}
            offset += source["rows"] - skip
        if offset != table["rows"]:
            raise _LayoutMismatch("PDF workbook rows do not match their source pages")
        for first_row, first_column, last_row, last_column in table["merges"]:
            for row in range(first_row, last_row + 1):
                for column in range(first_column, last_column + 1):
                    if (row, column) != (first_row, first_column):
                        table["cells"].pop(ref(row, column), None)
        for item in table["cells"].values():
            item.pop("conversion_state", None)
    return {**original, "tables": prepared["tables"], "prepared_format": "xlsx", "prepared_sha256": prepared["source"]["sha256"]}


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def valid_pdf_bbox(bbox: Any, width: Any, height: Any) -> bool:
    return (
        isinstance(bbox, list)
        and len(bbox) == 4
        and all(_finite(value) for value in bbox)
        and _finite(width)
        and _finite(height)
        and 0 <= bbox[0] < bbox[2] <= width
        and 0 <= bbox[1] < bbox[3] <= height
    )


class _UnresolvedGrid(ValueError):
    """A page's line reconstruction cannot establish usable cell coordinates."""


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
    # Exported table rules can be filled thin rectangles rather than strokes.
    # Require a full edge; crossing only one corner does not establish a rule.
    for axis in (0, 1):
        along = 1 - axis
        if (
            any(max(abs(rect[axis] - cell[edge]), abs(rect[axis + 2] - cell[edge])) <= tolerance for edge in (axis, axis + 2))
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


def converted_cell_sources(extraction: Any) -> dict[tuple[int, int, int], tuple[int, list[float]]]:
    """(sheet index, row, column) of each converted workbook cell -> (page number, cell bbox) on its original page."""
    from documents.pdf._stitch import _build_boundary_index_map, _remap_cell_columns

    sources: dict[tuple[int, int, int], tuple[int, list[float]]] = {}
    groups = [group for group in extraction.groups if group.success and group.table_grid is not None]
    skip_headers = extraction.spec.stitch.drop_repeated_header
    for sheet, group in enumerate(groups):
        offset = 0
        pages = [page for page in group.page_results if page.success and page.page_index in group.pages]
        for index, page in enumerate(pages):
            grid = page.table_grid
            skip = int(skip_headers and index > 0)
            boundaries = _build_boundary_index_map(grid.cols, group.table_grid.cols)
            for cell in grid.cells:
                if cell.row_end < skip:
                    continue
                first, _last = _remap_cell_columns(cell, boundaries)
                bbox = [grid.cols[cell.col_start], grid.rows[cell.row_start], grid.cols[cell.col_end + 1], grid.rows[cell.row_end + 1]]
                sources[(sheet, max(cell.row_start, skip) - skip + offset + 1, first + 1)] = (page.page_index + 1, bbox)
            offset += len(grid.rows) - 1 - skip
    return sources


def graphic_inside(path: str, page_number: int, bbox: list[float], tolerance: float) -> bool:
    """Whether an image or a non-border stroke lies inside one cell of a PDF page."""
    drawings, images = _page_graphics(path, page_number)
    return any(_intersects(bbox, image) for image in images) or any(_vector_inside(drawing, bbox, tolerance, set()) for drawing in drawings)


@functools.lru_cache(maxsize=32)
def _page_graphics(path: str, page_number: int) -> tuple[list[dict[str, Any]], list[list[float]]]:
    import fitz

    with fitz.open(path) as document:
        page = document[page_number - 1]
        return page.get_drawings(), [list(image["bbox"]) for image in page.get_image_info()]


def _path_bounds(path: dict[str, Any]) -> tuple[float, float, float, float]:
    """Every point a path check can touch, including curve control points."""
    xs, ys = [path["rect"][0], path["rect"][2]], [path["rect"][1], path["rect"][3]]
    for item in path["items"]:
        if item[0] == "re":
            xs += [item[1][0], item[1][2]]
            ys += [item[1][1], item[1][3]]
        elif item[0] == "qu":
            points = (item[1].ul, item[1].ur, item[1].lr, item[1].ll)
            xs += [point.x for point in points]
            ys += [point.y for point in points]
        else:
            xs += [point.x for point in item[1:]]
            ys += [point.y for point in item[1:]]
    return min(xs), min(ys), max(xs), max(ys)


def _table(grid: Any, page: Any, limits: ReadLimits, geometry_tolerance: float) -> dict[str, Any]:
    axes = (grid.rows, grid.cols)
    if any(len(axis) < 2 or not all(_finite(value) for value in axis) or any(a >= b for a, b in zip(axis, axis[1:])) for axis in axes):
        raise _UnresolvedGrid("invalid_grid_boundaries")
    rows, cols = len(grid.rows) - 1, len(grid.cols) - 1
    within(rows, 100000, "PDF rows")
    within(cols, 16384, "PDF columns")
    number = page.number + 1
    images = [image["bbox"] for image in page.get_image_info()]
    drawings = page.get_drawings()
    aligned_fills = {
        tuple(item[1])
        for drawing in drawings
        if drawing.get("fill") is not None
        for item in drawing["items"]
        if item[0] == "re"
        if all(item[1][axis + 2] - item[1][axis] > 2 * geometry_tolerance for axis in (0, 1))
        and all(any(abs(item[1][edge] - bound) <= geometry_tolerance for bound in axes) for edges, axes in (((0, 2), grid.cols), ((1, 3), grid.rows)) for edge in edges)
    }

    # Index paths by grid row band. A path can only reach a cell its bounds overlap,
    # so this narrows the candidates without changing any per-cell decision.
    bounds = [_path_bounds(drawing) for drawing in drawings]
    bands: list[list[int]] = [[] for _ in range(rows)]
    for index, (_x0, y0, _x1, y1) in enumerate(bounds):
        for band in range(max(0, bisect_left(grid.rows, y0) - 1), min(rows, bisect_right(grid.rows, y1))):
            bands[band].append(index)

    def observed(row: int, column: int, last_row: int, last_column: int, value: str | None, *, reconstructed: bool) -> dict[str, Any]:
        bbox = [grid.cols[column], grid.rows[row], grid.cols[last_column + 1], grid.rows[last_row + 1]]
        overlap = any(_intersects(bbox, image) for image in images)
        candidates = sorted({index for band in range(row, last_row + 1) for index in bands[band] if bounds[index][0] <= bbox[2] and bounds[index][2] >= bbox[0]})
        vector_paths = [index + 1 for index in candidates if _vector_inside(drawings[index], bbox, geometry_tolerance, aligned_fills)]
        if not valid_pdf_bbox(bbox, page.rect.width, page.rect.height):
            state = "unknown_geometry"
        elif not reconstructed:
            state = "unknown_uncovered_grid"
        elif (overlap or vector_paths) and not (value or "").strip():
            state = "unknown_visual_evidence"
        else:
            state = "known"
        cell: dict[str, Any] = {"value": value, "state": state, "source": {"page": number, "bbox": bbox}}
        if overlap:
            cell["image_overlap"] = True
        if vector_paths:
            cell["visual_overlap"] = True
            cell["vector_path_indices"] = vector_paths
        return cell

    cells = {ref(row + 1, col + 1): observed(row, col, row, col, None, reconstructed=False) for row in range(rows) for col in range(cols)}
    merges = []
    occupied: set[int] = set()
    for cell in grid.cells:
        r1, r2, c1, c2 = cell.row_start, cell.row_end, cell.col_start, cell.col_end
        if any(type(value) is not int for value in (r1, r2, c1, c2)) or not (0 <= r1 <= r2 < rows and 0 <= c1 <= c2 < cols):
            raise _UnresolvedGrid("invalid_grid_cells")
        if not isinstance(cell.content, str):
            raise ValueError("Invalid PDF native text value")
        for row in range(r1, r2 + 1):
            for col in range(c1, c2 + 1):
                key = row * cols + col
                if key in occupied:
                    raise _UnresolvedGrid("overlapping_grid_cells")
                occupied.add(key)
                del cells[ref(row + 1, col + 1)]
        cells[ref(r1 + 1, c1 + 1)] = observed(r1, c1, r2, c2, cell.content, reconstructed=True)
        if r1 != r2 or c1 != c2:
            merges.append([r1 + 1, c1 + 1, r2 + 1, c2 + 1])
    return {
        "id": f"page-{number}",
        "page": number,
        "rows": rows,
        "cols": cols,
        "cells": cells,
        "merges": merges,
        "coordinate_system": "pdf_points_top_left",
        "vector_path_count": len(drawings),
        "vector_boundary_tolerance": geometry_tolerance,
    }


def snapshot_pdf(raw: bytes, limits: ReadLimits) -> dict[str, Any]:
    import fitz
    from documents.pdf import GroupingMode, GroupingSpec, PdfConversionError, PdfSpec, StitchMode, StitchSpec, extract_pdf_tables

    within(len(raw), limits.source_bytes, "source bytes")
    with fitz.open(stream=raw, filetype="pdf") as document:
        within(len(document), limits.pages, "pages")
        if not len(document):
            raise ValueError("PDF has no pages")
        pages, eligible = [], []
        unresolved: dict[str, str] = {}
        block_count = 0
        for page in document:
            number = page.number + 1
            blocks = [{"bbox": list(block[:4]), "text": block[4]} for block in page.get_text("blocks") if block[6] == 0]
            block_count += len(blocks)
            within(block_count, limits.blocks, "blocks")
            crop_origin = list(page.cropbox_position)
            pages.append(
                {
                    "page": number,
                    "width": page.rect.width,
                    "height": page.rect.height,
                    "rotation": page.rotation,
                    "crop_origin": crop_origin,
                    "text": page.get_text(),
                    "blocks": blocks,
                    "image_count": len(page.get_images()),
                }
            )
            if page.rotation or crop_origin != [0.0, 0.0]:
                unresolved[str(number)] = "unsupported_page_transform"
            elif sum(1 for drawing in page.get_drawings() for item in drawing["items"] if item[0] in {"l", "re"}) > limits.page_lines:
                # Grid reconstruction grows quadratically with segments; past the budget the page is skipped.
                unresolved[str(number)] = "page_line_budget_exceeded"
            else:
                eligible.append(page.number)
        observations: dict[int, Any] = {}
        if eligible:
            spec = PdfSpec(grouping=GroupingSpec(mode=GroupingMode.EXPLICIT_RANGES, explicit_ranges=tuple((index, index) for index in eligible)), stitch=StitchSpec(mode=StitchMode.NO_STITCH))
            try:
                extraction = extract_pdf_tables(raw, spec=spec)
            except PdfConversionError as error:
                if error.code != "no_successful_groups":
                    raise
            else:
                observations = {page.page_index: page for group in extraction.groups for page in group.page_results}
        tables = []
        visited = merged = 0
        for index in eligible:
            observation = observations.get(index)
            if observation is None or not observation.success or not observation.table_grid.cells:
                unresolved[str(index + 1)] = "native_grid_unavailable"
                continue
            grid = observation.table_grid
            visited += max(0, len(grid.rows) - 1) * max(0, len(grid.cols) - 1)
            merged += sum(cell.row_start != cell.row_end or cell.col_start != cell.col_end for cell in grid.cells)
            within(visited, limits.cells, "cell visits")
            within(merged, limits.merges, "merges")
            try:
                table = _table(grid, document[index], limits, spec.extraction.line_tolerance)
            except _UnresolvedGrid as error:
                unresolved[str(index + 1)] = str(error)
                continue
            tables.append(table)
            within(len(tables), limits.tables, "tables")
        return {
            "pages": pages,
            "tables": tables,
            "coverage": {"page_count": len(pages), "grid_pages": [table["page"] for table in tables], "unresolved_pages": sorted(map(int, unresolved)), "unresolved_reasons": unresolved},
            "limitations": [
                "Native page grids preserve PDF points, not workbook coordinates. Grid extraction does not verify business row roles or field meaning.",
                "Uncovered cells, unread visual content and invalid geometry remain unknown; native nonempty text is retained even when an image or vector overlaps it.",
                "Vector path indices use original page.get_drawings() order (1-based). Cell-boundary strokes and whole-cell rectangle backgrounds are excluded; other geometry is uninterpreted.",
                "The native parser line tolerance is used only to match full-edge rules and whole-cell rectangles to reconstructed boundaries, not to interpret internal marks.",
                "A native blank describes the selected grid cell interior. It does not establish absence of graphics in a parent region or the business meaning of an enclosing rectangle.",
                "Pages without a usable native grid remain explicit. Scans, vector-only marks and unsupported page transforms require separate interpretation.",
            ],
        }


def validate_pdf_tables(snapshot: dict[str, Any]) -> None:
    """Validate declared geometry without running a PDF parser on the host."""
    tables, pages = snapshot.get("tables", []), snapshot.get("pages", [])
    if not tables and "coverage" not in snapshot:
        return  # Text-block-only observations do not claim any grid coverage.
    if not pages:
        raise ValueError("PDF grid coverage has no source pages")
    for page in pages:
        if not all(_finite(page.get(key)) and page[key] > 0 for key in ("width", "height")):
            raise ValueError("Invalid PDF page dimensions")
        origin = page.get("crop_origin")
        if type(page.get("page")) is not int or type(page.get("rotation")) is not int or not isinstance(origin, list) or len(origin) != 2 or not all(_finite(value) for value in origin):
            raise ValueError("Invalid PDF page coordinate metadata")
    coverage = snapshot.get("coverage")
    if not isinstance(coverage, dict) or type(coverage.get("page_count")) is not int or coverage["page_count"] != len(pages):
        raise ValueError("Invalid PDF grid coverage")
    grid_pages, unresolved = coverage.get("grid_pages"), coverage.get("unresolved_pages")
    if not isinstance(grid_pages, list) or not isinstance(unresolved, list):
        raise ValueError("Invalid PDF page coverage lists")
    if any(any(type(value) is not int for value in values) or len(values) != len(set(values)) for values in (grid_pages, unresolved)):
        raise ValueError("Invalid PDF page coverage lists")
    if set(grid_pages) & set(unresolved) or set(grid_pages) | set(unresolved) != set(range(1, len(pages) + 1)):
        raise ValueError("PDF grid coverage omits or repeats pages")
    reasons = coverage.get("unresolved_reasons")
    if not isinstance(reasons, dict) or set(reasons) != set(map(str, unresolved)) or any(not isinstance(reason, str) or not reason.strip() for reason in reasons.values()):
        raise ValueError("Unresolved PDF pages need reasons")
    if [table.get("page") for table in tables] != grid_pages:
        raise ValueError("PDF tables contradict page coverage")
    visited = 0
    for table in tables:
        number = table.get("page")
        if type(number) is not int or not 1 <= number <= len(pages) or table["id"] != f"page-{number}" or table.get("coordinate_system") != "pdf_points_top_left":
            raise ValueError("Invalid PDF table identity")
        page = pages[number - 1]
        if page.get("rotation") != 0 or page.get("crop_origin") != [0.0, 0.0]:
            raise ValueError("PDF grid uses an unsupported page transform")
        rows, cols = table["rows"], table["cols"]
        path_count = table.get("vector_path_count", 0)
        if type(path_count) is not int or path_count < 0:
            raise ValueError("Invalid PDF vector path count")
        if "vector_boundary_tolerance" in table and (not _finite(table["vector_boundary_tolerance"]) or table["vector_boundary_tolerance"] < 0):
            raise ValueError("Invalid PDF vector boundary tolerance")
        visited += rows * cols
        if not rows or not cols or visited > 1_000_000:
            raise ValueError("Invalid PDF grid size")
        merges: dict[str, list[int]] = {}
        for span in table.get("merges", []):
            address = ref(span[0], span[1])
            if address in merges or span[:2] == span[2:]:
                raise ValueError("Invalid PDF merged anchor")
            merges[address] = span
        if set(merges) - set(table["cells"]):
            raise ValueError("Missing PDF merged anchor")
        covered: set[int] = set()
        x_bounds: dict[int, float] = {}
        y_bounds: dict[int, float] = {}
        for address, cell in table["cells"].items():
            match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", address)
            if match is None:
                raise ValueError("Invalid PDF cell address")
            row, col = int(match[2]), col_number(match[1])
            r1, c1, r2, c2 = merges.get(address, [row, col, row, col])
            if not 1 <= row <= rows or not 1 <= col <= cols or cell.get("anchor", address) != address:
                raise ValueError("PDF cell is outside the grid or has a second anchor")
            for r in range(r1 - 1, r2):
                for c in range(c1 - 1, c2):
                    key = r * cols + c
                    if key in covered:
                        raise ValueError("PDF cells overlap")
                    covered.add(key)
            source = cell.get("source")
            bbox = source.get("bbox") if isinstance(source, dict) else None
            if (
                not isinstance(source, dict)
                or type(source.get("page")) is not int
                or source["page"] != number
                or not isinstance(bbox, list)
                or len(bbox) != 4
                or not all(_finite(value) for value in bbox)
            ):
                raise ValueError("Invalid PDF cell source")
            state, value = cell.get("state"), cell.get("value")
            if value is not None and not isinstance(value, str):
                raise ValueError("Invalid native PDF cell text")
            if "image_overlap" in cell and type(cell["image_overlap"]) is not bool:
                raise ValueError("Invalid PDF image overlap observation")
            if "visual_overlap" in cell and type(cell["visual_overlap"]) is not bool:
                raise ValueError("Invalid PDF visual overlap observation")
            path_indices = cell.get("vector_path_indices", [])
            if not isinstance(path_indices, list) or any(type(index) is not int or not 1 <= index <= path_count for index in path_indices) or len(path_indices) != len(set(path_indices)):
                raise ValueError("Invalid PDF vector path observation")
            if bool(path_indices) != bool(cell.get("visual_overlap")):
                raise ValueError("PDF vector overlap needs original path references")
            if state == "unknown_geometry":
                continue  # Preserve raw abnormal coordinates, never validate them as evidence.
            if not valid_pdf_bbox(bbox, page["width"], page["height"]):
                raise ValueError("PDF cell geometry exceeds its source page")
            if state == "unknown_uncovered_grid" and (value is not None or address in merges):
                raise ValueError("Uncovered PDF slots cannot claim reconstructed content")
            visual_overlap = cell.get("image_overlap") or cell.get("visual_overlap")
            if state == "unknown_visual_evidence" and (not visual_overlap or (value or "").strip()):
                raise ValueError("Invalid PDF visual unknown")
            if state == "known" and visual_overlap and not (value or "").strip():
                raise ValueError("An uninterpreted visual cannot establish a blank cell")
            for bounds, positions in ((x_bounds, ((c1 - 1, bbox[0]), (c2, bbox[2]))), (y_bounds, ((r1 - 1, bbox[1]), (r2, bbox[3])))):
                for index, value in positions:
                    if index in bounds and bounds[index] != value:
                        raise ValueError("Inconsistent PDF grid boundaries")
                    bounds[index] = value
        if len(covered) != rows * cols:
            raise ValueError("PDF grid has unrepresented slots")
        for bounds in (x_bounds, y_bounds):
            coordinates = [value for _index, value in sorted(bounds.items())]
            if any(a >= b for a, b in zip(coordinates, coordinates[1:])):
                raise ValueError("Non-monotonic PDF cell geometry")
