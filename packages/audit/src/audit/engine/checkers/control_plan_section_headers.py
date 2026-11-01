from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_DETAIL_HEADER_SCAN_ROWS = 3
_SECTION_HEADER_MIN_WIDTH = 4
_SECTION_HEADER_MIN_COVERAGE_NUMERATOR = 4
_SECTION_HEADER_MIN_COVERAGE_DENOMINATOR = 5


def has_business_text(value: Any) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() not in {"", "none", "nan"}


def detail_table_bounds(
    ws: Any,
    *,
    header_row: int,
    columns: Iterable[int | None],
) -> tuple[int, int]:
    concrete_columns = tuple(col for col in columns if col is not None)
    left = min(concrete_columns, default=1)
    right = max(concrete_columns, default=1)
    header_end = min(ws.max_row, header_row + _DETAIL_HEADER_SCAN_ROWS - 1)

    for row_index in range(header_row, header_end + 1):
        for col_index in range(1, ws.max_column + 1):
            if has_business_text(ws.cell(row_index, col_index).value):
                left = min(left, col_index)
                right = max(right, col_index)

    for merged_range in getattr(getattr(ws, "merged_cells", None), "ranges", []):
        if merged_range.max_row < header_row or merged_range.min_row > header_end:
            continue
        left = min(left, merged_range.min_col)
        right = max(right, merged_range.max_col)

    return left, right


def _merged_block_has_only_title_text(
    ws: Any,
    merged_range: Any,
    *,
    table_left: int,
    table_right: int,
) -> bool:
    if not has_business_text(ws.cell(merged_range.min_row, merged_range.min_col).value):
        return False

    for row_index in range(merged_range.min_row, merged_range.max_row + 1):
        for col_index in range(table_left, table_right + 1):
            if row_index == merged_range.min_row and col_index == merged_range.min_col:
                continue
            if has_business_text(ws.cell(row_index, col_index).value):
                return False
    return True


def detail_section_header_merge(
    ws: Any,
    row_index: int,
    *,
    table_left: int,
    table_right: int,
) -> Any | None:
    table_width = max(1, table_right - table_left + 1)
    min_width = max(
        _SECTION_HEADER_MIN_WIDTH,
        (table_width * _SECTION_HEADER_MIN_COVERAGE_NUMERATOR + _SECTION_HEADER_MIN_COVERAGE_DENOMINATOR - 1) // _SECTION_HEADER_MIN_COVERAGE_DENOMINATOR,
    )

    for merged_range in getattr(getattr(ws, "merged_cells", None), "ranges", []):
        if not (merged_range.min_row <= row_index <= merged_range.max_row):
            continue
        merged_width = merged_range.max_col - merged_range.min_col + 1
        if merged_width < min_width:
            continue
        if merged_range.min_col > table_left + 1:
            continue
        if merged_range.max_col < table_right - 1:
            continue
        if not _merged_block_has_only_title_text(
            ws,
            merged_range,
            table_left=table_left,
            table_right=table_right,
        ):
            continue
        return merged_range
    return None
