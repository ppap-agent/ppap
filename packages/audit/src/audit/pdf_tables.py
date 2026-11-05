"""Page-local native PDF grids and their source geometry; no OCR or model calls."""

from __future__ import annotations

import math
import re
from typing import Any

from documents.pdf.source_graphics import (
    _intersects as _intersects,
)
from documents.pdf.source_graphics import (
    _line_inside as _line_inside,
)
from documents.pdf.source_graphics import (
    _rectangle_inside as _rectangle_inside,
)
from documents.pdf.source_graphics import (
    _vector_inside as _vector_inside,
)

from .document_tools import ReadLimits, col_number, ref, within


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


def _table(grid: Any, page: Any, limits: ReadLimits, geometry_tolerance: float) -> dict[str, Any]:
    axes = (grid.rows, grid.cols)
    if any(len(axis) < 2 or not all(_finite(value) for value in axis) or any(a >= b for a, b in zip(axis, axis[1:])) for axis in axes):
        raise _UnresolvedGrid("invalid_grid_boundaries")
    rows, cols = len(grid.rows) - 1, len(grid.cols) - 1
    within(rows, 100000, "PDF rows")
    within(cols, 16384, "PDF columns")
    number = page.number + 1
    images = [image["bbox"] for image in page.get_image_info()]
    annotations = [list(annotation.rect) for annotation in (page.annots() or ())]
    widgets = [list(widget.rect) for widget in (page.widgets() or ())]
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

    def observed(row: int, column: int, last_row: int, last_column: int, value: str | None, *, reconstructed: bool) -> dict[str, Any]:
        bbox = [grid.cols[column], grid.rows[row], grid.cols[last_column + 1], grid.rows[last_row + 1]]
        overlap = any(_intersects(bbox, image) for image in images)
        annotation_overlap = any(_intersects(bbox, bounds) for bounds in annotations)
        widget_overlap = any(_intersects(bbox, bounds) for bounds in widgets)
        vector_paths = [index for index, drawing in enumerate(drawings, 1) if _vector_inside(drawing, bbox, geometry_tolerance, aligned_fills)]
        if not valid_pdf_bbox(bbox, page.rect.width, page.rect.height):
            state = "unknown_geometry"
        elif not reconstructed:
            state = "unknown_uncovered_grid"
        elif overlap or vector_paths or annotation_overlap or widget_overlap:
            state = "unknown_visual_evidence"
        else:
            state = "known"
        cell: dict[str, Any] = {"value": value, "state": state, "source": {"page": number, "bbox": bbox}}
        if overlap:
            cell["image_overlap"] = True
        if annotation_overlap:
            cell["annotation_overlap"] = True
        if widget_overlap:
            cell["widget_overlap"] = True
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
                "Uncovered cells, unread visual content and invalid geometry remain unknown; "
                "native text is retained but cannot confirm cell contents under image, vector, annotation or widget overlays.",
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
            for flag in ("image_overlap", "visual_overlap", "annotation_overlap", "widget_overlap"):
                if flag in cell and type(cell[flag]) is not bool:
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
            visual_overlap = any(cell.get(flag) for flag in ("image_overlap", "visual_overlap", "annotation_overlap", "widget_overlap"))
            if state == "unknown_visual_evidence" and not visual_overlap:
                raise ValueError("Invalid PDF visual unknown")
            if state == "known" and visual_overlap:
                raise ValueError("An uninterpreted visual cannot establish cell contents")
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
