"""Workbook rendering for stitched extraction groups."""

from __future__ import annotations

import logging
from bisect import bisect_right
from pathlib import Path
from typing import Any

from ..excel.writer import (
    append_image_bytes_to_sheet,
    axis_span_pixels,
    build_workbook_from_table_grids,
    place_image_bytes_to_sheet,
    save_workbook,
)
from .result import GroupExtraction
from .spec import OutputMode, OutputSpec, SheetNameMode

logger = logging.getLogger(__name__)
_PDF_POINT_TO_PIXEL = 96.0 / 72.0


def write_workbook(
    groups: list[GroupExtraction],
    output_path: str,
    output: OutputSpec,
    *,
    source: str | Path | bytes | None = None,
) -> bool:
    if output.mode != OutputMode.SHEET_PER_GROUP:
        raise ValueError(f"Unsupported output mode: {output.mode}")

    successful_groups = [group for group in groups if group.success and group.table_grid is not None]
    if not successful_groups:
        return False

    sheets = [(_sheet_name(group, output, index), group.table_grid) for index, group in enumerate(successful_groups, start=1) if group.table_grid is not None]
    workbook = build_workbook_from_table_grids(sheets)

    if output.preserve_pdf_embedded_images and source is not None:
        _attach_pdf_embedded_images(
            workbook,
            successful_groups,
            source=source,
            output=output,
        )

    return save_workbook(workbook, output_path)


def _read_source_bytes(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    with open(Path(source), "rb") as handle:
        return handle.read()


def _attach_pdf_embedded_images(
    workbook: Any,
    groups: list[GroupExtraction],
    *,
    source: str | Path | bytes,
    output: OutputSpec,
) -> None:
    try:
        import fitz
    except Exception as exc:
        logger.warning("PyMuPDF unavailable, skip embedded image preservation: %s", exc)
        return

    max_per_sheet = max(1, int(output.embedded_image_max_per_sheet))
    max_width = max(1, int(output.embedded_image_max_width))
    min_bytes = max(0, int(output.embedded_image_min_bytes))

    try:
        doc = _open_pdf_doc(source, fitz_module=fitz)
    except Exception as exc:
        logger.warning("Failed to open PDF source for embedded image extraction: %s", exc)
        return

    try:
        for sheet_index, group in enumerate(groups):
            if sheet_index >= len(workbook.worksheets):
                break
            worksheet = workbook.worksheets[sheet_index]
            _apply_sheet_grid_layout(worksheet, group)
            unit_placements, fallback_images = _collect_group_image_placements(
                doc,
                group,
                max_per_sheet=max_per_sheet,
                min_bytes=min_bytes,
            )
            if unit_placements:
                placements = _unit_placements_to_pixel_placements(worksheet, unit_placements)
                place_image_bytes_to_sheet(
                    workbook,
                    sheet_index=sheet_index,
                    placements=placements,
                    max_width=max_width,
                )
            if fallback_images:
                append_image_bytes_to_sheet(
                    workbook,
                    sheet_index=sheet_index,
                    image_bytes_list=fallback_images,
                    max_width=max_width,
                )
    finally:
        doc.close()


def _open_pdf_doc(source: str | Path | bytes, *, fitz_module: Any) -> Any:
    if isinstance(source, bytes):
        return fitz_module.open(stream=source, filetype="pdf")

    source_path = Path(source)
    if source_path.exists():
        return fitz_module.open(str(source_path))

    file_content = _read_source_bytes(source)
    return fitz_module.open(stream=file_content, filetype="pdf")


def _collect_group_image_placements(
    doc: Any,
    group: GroupExtraction,
    *,
    max_per_sheet: int,
    min_bytes: int,
) -> tuple[list[tuple[float, float, float, float, bytes]], list[bytes]]:
    page_layouts = _build_group_page_layouts(group)
    if not page_layouts:
        return ([], [])

    placements: list[tuple[float, float, float, float, bytes]] = []
    fallback_images: list[bytes] = []
    extracted_by_xref: dict[int, bytes] = {}
    seen_occurrences: set[tuple[int, int, int, int, int, int]] = set()
    image_count = 0

    for page_index in group.pages:
        if image_count >= max_per_sheet:
            break
        if not (0 <= page_index < len(doc)):
            continue

        page = doc[page_index]
        layout = page_layouts.get(page_index)
        rects_by_xref: dict[int, list[Any]] = {}
        candidates: list[tuple[float, int, Any | None, bytes]] = []
        for image_meta in page.get_images(full=True):
            if not image_meta:
                continue

            xref = int(image_meta[0])
            image_bytes = extracted_by_xref.get(xref)
            if image_bytes is None:
                image_dict = doc.extract_image(xref)
                image_bytes = image_dict.get("image")
                if not image_bytes or len(image_bytes) < min_bytes:
                    continue
                extracted_by_xref[xref] = image_bytes

            rects = rects_by_xref.get(xref)
            if rects is None:
                rects = _safe_get_image_rects(page, xref)
                rects_by_xref[xref] = rects
            if not rects:
                candidates.append((0.0, xref, None, image_bytes))
                continue

            for rect in rects:
                area = max(0.0, (float(rect.x1) - float(rect.x0)) * (float(rect.y1) - float(rect.y0)))
                candidates.append((area, xref, rect, image_bytes))

        candidates.sort(key=lambda item: item[0], reverse=True)
        for _area, xref, rect, image_bytes in candidates:
            if image_count >= max_per_sheet:
                break
            if rect is None or layout is None:
                fallback_images.append(image_bytes)
                image_count += 1
                continue

            occurrence_key = (
                page_index,
                xref,
                int(round(float(rect.x0) * 10)),
                int(round(float(rect.y0) * 10)),
                int(round(float(rect.x1) * 10)),
                int(round(float(rect.y1) * 10)),
            )
            if occurrence_key in seen_occurrences:
                continue
            seen_occurrences.add(occurrence_key)

            geometry = _map_rect_to_sheet_geometry(rect, layout)
            if geometry is None:
                fallback_images.append(image_bytes)
                image_count += 1
                continue
            row_start, col_start, row_end, col_end = geometry
            placements.append((row_start, col_start, row_end, col_end, image_bytes))
            image_count += 1

    placements.sort(key=lambda item: (item[0], item[1]))
    return (placements, fallback_images)


def _build_group_page_layouts(group: GroupExtraction) -> dict[int, dict[str, int | list[float]]]:
    if group.table_grid is None or len(group.table_grid.cols) < 2:
        return {}

    page_order = {page_index: order for order, page_index in enumerate(group.pages)}
    page_results = [
        page_result
        for page_result in group.page_results
        if page_result.success and page_result.page_index in page_order and len(page_result.table_grid.rows) >= 2 and len(page_result.table_grid.cols) >= 2
    ]
    if not page_results:
        return {}
    page_results.sort(key=lambda item: page_order[item.page_index])

    row_counts = [max(0, len(page_result.table_grid.rows) - 1) for page_result in page_results]
    target_rows = max(0, len(group.table_grid.rows) - 1)
    use_header_drop = _infer_header_drop(target_rows=target_rows, row_counts=row_counts)
    target_col_count = max(1, len(group.table_grid.cols) - 1)

    layouts: dict[int, dict[str, int | list[float]]] = {}
    row_offset = 0
    for index, page_result in enumerate(page_results):
        row_count = row_counts[index]
        skip_rows = 1 if (use_header_drop and index > 0 and row_count > 0) else 0
        layouts[page_result.page_index] = {
            "rows": page_result.table_grid.rows,
            "cols": page_result.table_grid.cols,
            "row_offset": row_offset,
            "skip_rows": skip_rows,
            "target_col_count": target_col_count,
        }
        row_offset += max(0, row_count - skip_rows)

    return layouts


def _infer_header_drop(*, target_rows: int, row_counts: list[int]) -> bool:
    if len(row_counts) <= 1:
        return False
    without_drop = sum(row_counts)
    with_drop = without_drop - (len(row_counts) - 1)
    if target_rows == with_drop:
        return True
    if target_rows == without_drop:
        return False
    return target_rows <= with_drop


def _safe_get_image_rects(page: Any, xref: int) -> list[Any]:
    try:
        rects = page.get_image_rects(xref)
    except Exception:
        return []
    valid_rects: list[Any] = []
    for rect in rects:
        if float(rect.x1) <= float(rect.x0) or float(rect.y1) <= float(rect.y0):
            continue
        valid_rects.append(rect)
    return valid_rects


def _map_rect_to_sheet_geometry(rect: Any, layout: dict[str, int | list[float]]) -> tuple[float, float, float, float] | None:
    rows = layout.get("rows")
    cols = layout.get("cols")
    if not isinstance(rows, list) or not isinstance(cols, list):
        return None
    if len(rows) < 2 or len(cols) < 2:
        return None

    row_offset_raw = layout.get("row_offset", 0)
    skip_rows_raw = layout.get("skip_rows", 0)
    row_offset = row_offset_raw if isinstance(row_offset_raw, int) else 0
    skip_rows = skip_rows_raw if isinstance(skip_rows_raw, int) else 0
    target_col_count_raw = layout.get("target_col_count", 1)
    target_col_count = target_col_count_raw if isinstance(target_col_count_raw, int) else 1

    y_start = _project_row_position(
        rows,
        value=float(rect.y0),
        row_offset=row_offset,
        skip_rows=skip_rows,
    )
    y_end = _project_row_position(
        rows,
        value=float(rect.y1),
        row_offset=row_offset,
        skip_rows=skip_rows,
    )
    x_start = _project_col_position(
        cols,
        value=float(rect.x0),
        target_col_count=target_col_count,
    )
    x_end = _project_col_position(
        cols,
        value=float(rect.x1),
        target_col_count=target_col_count,
    )
    if y_start is None or y_end is None or x_start is None or x_end is None:
        return None

    if y_end <= y_start:
        y_end = y_start + 0.1
    if x_end <= x_start:
        x_end = x_start + 0.1
    return (y_start, x_start, y_end, x_end)


def _locate_band(boundaries: list[float], value: float) -> int | None:
    if len(boundaries) < 2:
        return None
    if value <= boundaries[0]:
        return 0
    if value >= boundaries[-1]:
        return len(boundaries) - 2
    idx = bisect_right(boundaries, value) - 1
    if idx < 0:
        return 0
    if idx >= len(boundaries) - 1:
        return len(boundaries) - 2
    return idx


def _project_axis_position(boundaries: list[float], *, value: float) -> float | None:
    band = _locate_band(boundaries, value)
    if band is None:
        return None
    left = float(boundaries[band])
    right = float(boundaries[band + 1])
    span = max(1e-6, right - left)
    clamped = min(max(value, left), right)
    ratio = (clamped - left) / span
    return float(band) + ratio


def _project_row_position(
    rows: list[float],
    *,
    value: float,
    row_offset: int,
    skip_rows: int,
) -> float | None:
    local = _project_axis_position(rows, value=value)
    if local is None:
        return None
    adjusted = max(local, float(skip_rows)) - float(skip_rows)
    max_adjusted = max(0.0, float(len(rows) - 1 - skip_rows))
    adjusted = min(max(0.0, adjusted), max_adjusted)
    return float(row_offset) + adjusted


def _project_col_position(
    cols: list[float],
    *,
    value: float,
    target_col_count: int,
) -> float | None:
    local = _project_axis_position(cols, value=value)
    if local is None:
        return None
    local_count = max(1, len(cols) - 1)
    target_count = max(1, int(target_col_count))
    if local_count != target_count:
        local = local * (float(target_count) / float(local_count))
    return min(max(0.0, local), float(target_count))


def _unit_placements_to_pixel_placements(
    worksheet: Any,
    placements: list[tuple[float, float, float, float, bytes]],
) -> list[tuple[int, int, bytes, int, int, int, int]]:
    converted: list[tuple[int, int, bytes, int, int, int, int]] = []
    col_px_cache: dict[int, int] = {}
    row_px_cache: dict[int, int] = {}

    def _col_px(col_index: int) -> int:
        cached = col_px_cache.get(col_index)
        if cached is not None:
            return cached
        width_px = _column_pixel_width(worksheet, col_index)
        col_px_cache[col_index] = width_px
        return width_px

    def _row_px(row_index: int) -> int:
        cached = row_px_cache.get(row_index)
        if cached is not None:
            return cached
        height_px = _row_pixel_height(worksheet, row_index)
        row_px_cache[row_index] = height_px
        return height_px

    for row_start, col_start, row_end, col_end, image_bytes in placements:
        anchor_row = int(row_start) + 1
        anchor_col = int(col_start) + 1

        row_fraction = max(0.0, row_start - float(int(row_start)))
        col_fraction = max(0.0, col_start - float(int(col_start)))

        row_px = _row_px(anchor_row)
        col_px = _col_px(anchor_col)

        dy_px = int(round(row_fraction * float(row_px)))
        dx_px = int(round(col_fraction * float(col_px)))

        width_px = axis_span_pixels(
            start_unit=col_start,
            end_unit=col_end,
            unit_pixel_getter=_col_px,
        )
        height_px = axis_span_pixels(
            start_unit=row_start,
            end_unit=row_end,
            unit_pixel_getter=_row_px,
        )
        converted.append((anchor_row, anchor_col, image_bytes, dx_px, dy_px, width_px, height_px))
    return converted


def _apply_sheet_grid_layout(worksheet: Any, group: GroupExtraction) -> None:
    col_units, row_units = _build_group_sheet_metrics(group)
    if col_units:
        for col_index, unit in enumerate(col_units, start=1):
            pixels = max(8, int(round(float(unit) * _PDF_POINT_TO_PIXEL)))
            worksheet.column_dimensions[_column_letter(col_index)].width = _pixels_to_excel_width(pixels)
    if row_units:
        for row_index, unit in enumerate(row_units, start=1):
            # Excel row height unit is points; PDF geometry uses points too.
            worksheet.row_dimensions[row_index].height = max(2.0, float(unit))


def _build_group_sheet_metrics(group: GroupExtraction) -> tuple[list[float], list[float]]:
    col_units: list[float] = []
    if group.table_grid is not None and len(group.table_grid.cols) >= 2:
        col_units = [max(0.5, float(group.table_grid.cols[idx + 1] - group.table_grid.cols[idx])) for idx in range(len(group.table_grid.cols) - 1)]

    page_order = {page_index: order for order, page_index in enumerate(group.pages)}
    page_results = [
        page_result
        for page_result in group.page_results
        if page_result.success and page_result.page_index in page_order and len(page_result.table_grid.rows) >= 2 and len(page_result.table_grid.cols) >= 2
    ]
    if not page_results:
        if group.table_grid is None or len(group.table_grid.rows) < 2:
            return (col_units, [])
        fallback_rows = [max(2.0, float(group.table_grid.rows[idx + 1] - group.table_grid.rows[idx])) for idx in range(len(group.table_grid.rows) - 1)]
        return (col_units, fallback_rows)

    page_results.sort(key=lambda item: page_order[item.page_index])
    row_counts = [max(0, len(page_result.table_grid.rows) - 1) for page_result in page_results]
    target_rows = max(0, len(group.table_grid.rows) - 1) if group.table_grid is not None else 0
    use_header_drop = _infer_header_drop(target_rows=target_rows, row_counts=row_counts)

    row_units: list[float] = []
    for idx, page_result in enumerate(page_results):
        rows = page_result.table_grid.rows
        skip_rows = 1 if (use_header_drop and idx > 0) else 0
        for row_idx in range(skip_rows, len(rows) - 1):
            unit = max(2.0, float(rows[row_idx + 1] - rows[row_idx]))
            row_units.append(unit)
    return (col_units, row_units)


def _pixels_to_excel_width(pixels: int) -> float:
    px = max(1, int(pixels))
    if px <= 12:
        return max(0.1, (float(px) - 0.5) / 12.0)
    return max(0.1, (float(px) - 5.0) / 7.0)


def _column_pixel_width(worksheet: Any, col_index: int) -> int:
    col_dim = worksheet.column_dimensions.get(_column_letter(col_index))
    width = getattr(col_dim, "width", None)
    if width is None:
        width = worksheet.sheet_format.defaultColWidth
    if width is None:
        width = 8.43
    width_value = max(0.0, float(width))
    if width_value < 1.0:
        pixels = int(width_value * 12.0 + 0.5)
    else:
        pixels = int(width_value * 7.0 + 5.0)
    return max(1, pixels)


def _row_pixel_height(worksheet: Any, row_index: int) -> int:
    row_dim = worksheet.row_dimensions.get(int(row_index))
    height = getattr(row_dim, "height", None)
    if height is None:
        height = worksheet.sheet_format.defaultRowHeight
    if height is None:
        height = 15.0
    return max(1, int(round(float(height) * (4.0 / 3.0))))


def _column_letter(col_index: int) -> str:
    index = max(1, int(col_index))
    letters = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _sheet_name(group: GroupExtraction, output: OutputSpec, index: int) -> str:
    if group.sheet_name:
        return group.sheet_name
    if output.sheet_name_mode == SheetNameMode.GROUP_LABEL and group.group_label:
        return group.group_label
    return f"{output.sheet_name_prefix}_{index}"
