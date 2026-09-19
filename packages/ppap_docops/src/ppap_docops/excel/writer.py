"""Excel workbook writing helpers for logical table grids."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Callable

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU
from openpyxl.worksheet.worksheet import Worksheet
from PIL import Image as PILImage

from ..table.models import TableGrid

logger = logging.getLogger(__name__)


def build_workbook_from_table_grid(
    table_grid: TableGrid,
    *,
    sheet_name: str = "Table_1",
) -> Workbook:
    """Build a workbook with a single sheet from one table grid."""
    workbook = Workbook()
    worksheet = workbook.active
    if worksheet is None:
        return workbook

    worksheet.title = _sanitize_sheet_name(sheet_name, fallback="Table_1")
    fill_worksheet_from_table_grid(worksheet, table_grid)
    return workbook


def write_table_grids(output_path: str, sheets: list[tuple[str, TableGrid]]) -> bool:
    """Write multiple logical table grids into one workbook."""
    if not sheets:
        return False

    workbook = build_workbook_from_table_grids(sheets)
    return save_workbook(workbook, output_path)


def build_workbook_from_table_grids(sheets: list[tuple[str, TableGrid]]) -> Workbook:
    """Build a workbook from multiple logical table grids."""
    workbook = Workbook()
    default_sheet = workbook.active
    if default_sheet is not None:
        workbook.remove(default_sheet)

    used_sheet_names: set[str] = set()
    for index, (sheet_name, table_grid) in enumerate(sheets, start=1):
        base_name = _sanitize_sheet_name(sheet_name, fallback=f"Table_{index}")
        unique_name = _ensure_unique_sheet_name(base_name, used_sheet_names)
        used_sheet_names.add(unique_name)
        worksheet = workbook.create_sheet(title=unique_name)
        fill_worksheet_from_table_grid(worksheet, table_grid)
    return workbook


def fill_worksheet_from_table_grid(worksheet: Worksheet, table_grid: TableGrid) -> None:
    """Fill worksheet from table grid cells and merge spans."""
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    for cell_region in table_grid.cells:
        excel_row_start = cell_region.row_start + 1
        excel_row_end = cell_region.row_end + 1
        excel_col_start = cell_region.col_start + 1
        excel_col_end = cell_region.col_end + 1

        start_cell = worksheet.cell(row=excel_row_start, column=excel_col_start)
        start_cell.value = cell_region.content
        start_cell.border = thin_border
        start_cell.alignment = Alignment(wrap_text=True, vertical="top")

        if excel_row_start != excel_row_end or excel_col_start != excel_col_end:
            worksheet.merge_cells(
                start_row=excel_row_start,
                end_row=excel_row_end,
                start_column=excel_col_start,
                end_column=excel_col_end,
            )

        for row in range(excel_row_start, excel_row_end + 1):
            for col in range(excel_col_start, excel_col_end + 1):
                worksheet.cell(row=row, column=col).border = thin_border

    _adjust_column_width(worksheet)


def save_workbook(workbook: Workbook, output_path: str) -> bool:
    """Persist workbook to path."""
    try:
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(str(output_file))
        logger.info("Excel file saved: %s", output_path)
        return True
    except Exception as exc:
        logger.error("Failed to save Excel file: %s", exc)
        return False


def append_image_bytes_to_sheet(
    workbook: Workbook,
    *,
    sheet_index: int,
    image_bytes_list: list[bytes],
    max_width: int,
    anchor_column_offset: int = 3,
) -> int:
    """Append binary images into one worksheet and keep buffers alive for workbook save."""
    if not image_bytes_list:
        return 0
    if not (0 <= sheet_index < len(workbook.worksheets)):
        return 0

    worksheet = workbook.worksheets[sheet_index]
    anchor_col = max(1, int(worksheet.max_column) + int(anchor_column_offset))
    next_row = 1
    added_count = 0
    image_buffers = _ensure_workbook_image_buffers(workbook)

    for image_bytes in image_bytes_list:
        normalized = _normalize_image_bytes(image_bytes)
        if normalized is None:
            continue
        normalized_buffer, natural_width, natural_height = normalized

        final_width, final_height = _cap_size_by_max_width(
            natural_width=natural_width,
            natural_height=natural_height,
            max_width=max_width,
        )
        xl_image = XLImage(normalized_buffer)
        xl_image.width = final_width
        xl_image.height = final_height
        worksheet.add_image(xl_image, f"{get_column_letter(anchor_col)}{next_row}")
        image_buffers.append(normalized_buffer)
        next_row += max(2, int(xl_image.height / 18) + 2)
        added_count += 1

    return added_count


def place_image_bytes_to_sheet(
    workbook: Workbook,
    *,
    sheet_index: int,
    placements: list[tuple[int, int, bytes, int, int, int, int]],
    max_width: int,
) -> int:
    """Place binary images with pixel-level anchor + size."""
    if not placements:
        return 0
    if not (0 <= sheet_index < len(workbook.worksheets)):
        return 0

    worksheet = workbook.worksheets[sheet_index]
    added_count = 0
    image_buffers = _ensure_workbook_image_buffers(workbook)

    for row, col, image_bytes, dx_px, dy_px, width_px, height_px in placements:
        normalized = _normalize_image_bytes(image_bytes)
        if normalized is None:
            continue
        normalized_buffer, natural_width, natural_height = normalized
        anchor_row = max(1, int(row))
        anchor_col = max(1, int(col))
        xl_image = XLImage(normalized_buffer)
        box_width_px = max(1, int(width_px))
        box_height_px = max(1, int(height_px))
        final_width_px, final_height_px, extra_dx_px, extra_dy_px = _fit_image_into_box(
            natural_width=natural_width,
            natural_height=natural_height,
            box_width=box_width_px,
            box_height=box_height_px,
            max_width=max_width,
        )

        xl_image.anchor = OneCellAnchor(
            _from=AnchorMarker(
                row=anchor_row - 1,
                col=anchor_col - 1,
                rowOff=pixels_to_EMU(max(0, int(dy_px + extra_dy_px))),
                colOff=pixels_to_EMU(max(0, int(dx_px + extra_dx_px))),
            ),
            ext=XDRPositiveSize2D(
                cx=pixels_to_EMU(final_width_px),
                cy=pixels_to_EMU(final_height_px),
            ),
        )
        worksheet.add_image(xl_image)
        image_buffers.append(normalized_buffer)
        added_count += 1

    return added_count


def _adjust_column_width(worksheet: Worksheet) -> None:
    if not worksheet.max_column or worksheet.max_column <= 0:
        return

    for col_num in range(1, worksheet.max_column + 1):
        max_len = 0
        max_row = worksheet.max_row or 0
        for row_num in range(1, max_row + 1):
            value = worksheet.cell(row=row_num, column=col_num).value
            if value is not None:
                max_len = max(max_len, len(str(value)))

        if max_len > 0:
            worksheet.column_dimensions[get_column_letter(col_num)].width = min(max_len + 2, 50)


def _sanitize_sheet_name(name: str, *, fallback: str) -> str:
    invalid = set("[]:*?/\\")
    clean = "".join(ch for ch in name if ch not in invalid).strip()
    if not clean:
        clean = fallback
    return clean[:31] if len(clean) > 31 else clean


def _ensure_unique_sheet_name(base_name: str, used_names: set[str]) -> str:
    if base_name not in used_names:
        return base_name

    counter = 2
    while True:
        suffix = f"_{counter}"
        trimmed = base_name[: 31 - len(suffix)]
        candidate = f"{trimmed}{suffix}"
        if candidate not in used_names:
            return candidate
        counter += 1


def _normalize_image_bytes(image_bytes: bytes) -> tuple[BytesIO, int, int] | None:
    try:
        image = PILImage.open(BytesIO(image_bytes))
        image.load()
    except Exception:
        return None

    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return (buffer, int(image.width), int(image.height))


def _ensure_workbook_image_buffers(workbook: Workbook) -> list[BytesIO]:
    buffers = getattr(workbook, "_ppap_image_buffers", None)
    if isinstance(buffers, list):
        return buffers
    created: list[BytesIO] = []
    setattr(workbook, "_ppap_image_buffers", created)
    return created


def axis_span_pixels(
    *,
    start_unit: float,
    end_unit: float,
    unit_pixel_getter: Callable[[int], int],
) -> int:
    if end_unit <= start_unit:
        return 1

    start_idx = int(start_unit)
    end_idx = int(end_unit) if end_unit.is_integer() else int(end_unit) + 1
    total = 0.0
    for unit_index in range(start_idx, end_idx):
        left = max(start_unit, float(unit_index))
        right = min(end_unit, float(unit_index + 1))
        overlap = max(0.0, right - left)
        if overlap <= 0.0:
            continue
        total += overlap * float(unit_pixel_getter(unit_index + 1))
    return max(1, int(round(total)))


def _cap_size_by_max_width(
    *,
    natural_width: int,
    natural_height: int,
    max_width: int,
) -> tuple[int, int]:
    width = max(1, int(natural_width))
    height = max(1, int(natural_height))
    if width <= max_width:
        return (width, height)
    ratio = float(max_width) / float(width)
    return (max(1, int(round(width * ratio))), max(1, int(round(height * ratio))))


def _fit_image_into_box(
    *,
    natural_width: int,
    natural_height: int,
    box_width: int,
    box_height: int,
    max_width: int,
) -> tuple[int, int, int, int]:
    target_box_width = max(1, int(box_width))
    target_box_height = max(1, int(box_height))
    if target_box_width > max_width:
        ratio = float(max_width) / float(target_box_width)
        target_box_width = max(1, int(round(target_box_width * ratio)))
        target_box_height = max(1, int(round(target_box_height * ratio)))

    natural_w = max(1, int(natural_width))
    natural_h = max(1, int(natural_height))
    scale = min(
        float(target_box_width) / float(natural_w),
        float(target_box_height) / float(natural_h),
    )
    display_w = max(1, int(round(float(natural_w) * scale)))
    display_h = max(1, int(round(float(natural_h) * scale)))
    extra_dx = max(0, int(round((target_box_width - display_w) / 2.0)))
    extra_dy = max(0, int(round((target_box_height - display_h) / 2.0)))
    return (display_w, display_h, extra_dx, extra_dy)


__all__ = [
    "append_image_bytes_to_sheet",
    "place_image_bytes_to_sheet",
    "build_workbook_from_table_grids",
    "build_workbook_from_table_grid",
    "fill_worksheet_from_table_grid",
    "save_workbook",
    "write_table_grids",
]
