from __future__ import annotations

import logging
import re
import unicodedata
from typing import Dict, Iterable, List, Optional

from documents.contracts import FileRef
from documents.conversion import load_workbook_auto as load_workbook_pipeline
from openpyxl.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

logger = logging.getLogger(__name__)

_HEADER_SCAN_ROWS = 40
_HEADER_SCAN_COLS = 80
_EMPTY_ROW_BREAK = 40
SOURCE_SHEET_KEY = "__sheet_name"
SOURCE_ROW_KEY = "__row_number"
_PFD_HEADER_OPERATION_NO_KEYS = {
    "工序号",
    "工序编号",
    "序号",
    "operationno",
    "processno",
}
_PFD_HEADER_OPERATION_NAME_KEYS = {
    "工序名称",
    "工序内容",
    "过程名称",
    "操作描述",
    "operationname",
    "processname",
    "operationdescription",
}
_PFD_OPERATION_NO_PATTERN = re.compile(r"^(\d+)\.(\d+)$")


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_key(value: object) -> str:
    text = normalize_text(value).lower()
    return re.sub(r"[\s\-_/,.，。:：;；()（）\[\]{}]+", "", text)


def build_struct_key(*values: object) -> str:
    parts = [normalize_key(value) for value in values]
    parts = [part for part in parts if part]
    return "|".join(parts)


def format_row_location(row: Dict[str, str]) -> str:
    sheet_name = normalize_text(row.get(SOURCE_SHEET_KEY, ""))
    row_number = normalize_text(row.get(SOURCE_ROW_KEY, ""))
    if sheet_name and row_number:
        return f"工作表'{sheet_name}' 第{row_number}行"
    if sheet_name:
        return f"工作表'{sheet_name}'"
    if row_number:
        return f"第{row_number}行"
    return ""


def prepend_row_location(row: Dict[str, str], message: str) -> str:
    location = format_row_location(row)
    if not location:
        return message
    return f"{location} {message}"


def is_pfd_header_row(
    row: Dict[str, str],
    *,
    operation_no_field: str = "operation_no",
    operation_name_field: str = "operation_name",
) -> bool:
    operation_no = normalize_key(row.get(operation_no_field, ""))
    operation_name = normalize_key(row.get(operation_name_field, ""))
    return operation_no in _PFD_HEADER_OPERATION_NO_KEYS or operation_name in _PFD_HEADER_OPERATION_NAME_KEYS


def filter_pfd_process_rows(
    rows: List[Dict[str, str]],
    *,
    operation_no_field: str = "operation_no",
    operation_name_field: str = "operation_name",
) -> List[Dict[str, str]]:
    """Drop PFD header rows and section rows that only group child operations."""

    parent_operation_nos: set[str] = set()
    for row in rows:
        operation_no = normalize_text(row.get(operation_no_field, ""))
        match = _PFD_OPERATION_NO_PATTERN.fullmatch(operation_no)
        if not match or match.group(2) == "0":
            continue
        parent_operation_nos.add(f"{match.group(1)}.0")

    filtered_rows: List[Dict[str, str]] = []
    for row in rows:
        if is_pfd_header_row(
            row,
            operation_no_field=operation_no_field,
            operation_name_field=operation_name_field,
        ):
            continue
        operation_no = normalize_text(row.get(operation_no_field, ""))
        if operation_no in parent_operation_nos:
            continue
        filtered_rows.append(row)
    return filtered_rows


def _load_workbook_auto(file_url: str) -> Workbook:
    try:
        from audit.engine.runtime import get_runtime

        runtime = get_runtime()
        if runtime is not None:
            workbook = runtime.load_workbook_auto(FileRef(location=file_url))
        else:
            workbook = load_workbook_pipeline(
                file_url,
                enable_cache=True,
                cleanup=True,
            )
        if not isinstance(workbook, Workbook):
            raise TypeError(f"Expected openpyxl Workbook, got {type(workbook).__name__}")
        return workbook
    except Exception as exc:
        logger.warning("关联资料加载失败: %s", type(exc).__name__)
        raise OSError("本轮冻结关联资料无法读取，不能完成本项核对。") from None


def _close_workbook(workbook: Workbook) -> None:
    close_method = getattr(workbook, "close", None)
    if callable(close_method):
        try:
            close_method()
        except Exception as exc:
            logger.debug("failed to close workbook: %s", exc)


def extract_rows_from_file(
    file_url: str,
    field_aliases: Dict[str, List[str]],
    required_fields: Optional[Iterable[str]] = None,
) -> List[Dict[str, str]]:
    """Extract structured rows from an Excel **or PDF** file.

    PDF files are automatically converted to Excel via pdfplumber geometry
    extraction before row parsing.
    """
    workbook = _load_workbook_auto(file_url)
    try:
        return extract_rows_from_workbook(
            workbook=workbook,
            field_aliases=field_aliases,
            required_fields=required_fields,
        )
    except Exception as exc:
        logger.warning("关联资料解析失败: %s", type(exc).__name__)
        raise OSError("本轮冻结关联资料无法读取，不能完成本项核对。") from None
    finally:
        _close_workbook(workbook)


def extract_rows_from_workbook(
    workbook: Workbook,
    field_aliases: Dict[str, List[str]],
    required_fields: Optional[Iterable[str]] = None,
) -> List[Dict[str, str]]:
    def _format_numeric_by_cell_pattern(raw_value: object, number_format: object) -> str:
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            return ""
        fmt = normalize_text(number_format)
        if not fmt or fmt.lower() == "general":
            return ""
        # Strip quoted literals and bracketed sections like [Red].
        cleaned_fmt = re.sub(r'"[^"]*"', "", fmt)
        cleaned_fmt = re.sub(r"\[[^\]]*\]", "", cleaned_fmt)
        decimal_match = re.search(r"0\.([0#]+)", cleaned_fmt)
        if decimal_match and decimal_match.group(1) and set(decimal_match.group(1)) == {"0"}:
            decimals = len(decimal_match.group(1))
            return f"{float(raw_value):.{decimals}f}"
        return ""

    def _extract_cell_text(sheet: Worksheet, row: int, col: int, field_name: str) -> str:
        cell = sheet.cell(row=row, column=col)
        if field_name.endswith("_no"):
            formatted_value = _format_numeric_by_cell_pattern(cell.value, cell.number_format)
            if formatted_value:
                return normalize_text(formatted_value)
        return normalize_text(cell.value)

    alias_keys = {field: [normalize_key(alias) for alias in aliases] for field, aliases in field_aliases.items()}
    header_value_keys = {alias for aliases in alias_keys.values() for alias in aliases if alias}
    required = set(required_fields or [])

    best_sheet = None
    best_row = 0
    best_header_span = 1
    best_columns: Dict[str, int] = {}
    best_alias_priority: Dict[str, int] = {}

    for ws in workbook.worksheets:
        row_limit = min(_HEADER_SCAN_ROWS, ws.max_row)
        col_limit = min(_HEADER_SCAN_COLS, ws.max_column)
        for row in range(1, row_limit + 1):
            columns: Dict[str, int] = {}
            alias_priority: Dict[str, int] = {}
            header_span_by_field: Dict[str, int] = {}
            for col in range(1, col_limit + 1):
                header_1 = normalize_key(ws.cell(row=row, column=col).value)
                header_2 = normalize_key(ws.cell(row=row + 1, column=col).value) if row < ws.max_row else ""
                combined = f"{header_1}{header_2}"
                if not combined:
                    continue
                for field, aliases in alias_keys.items():
                    matched = next(
                        ((idx, alias) for idx, alias in enumerate(aliases) if alias and alias in combined),
                        None,
                    )
                    if matched is None:
                        continue
                    matched_priority, matched_alias = matched
                    current_priority = alias_priority.get(field)
                    if current_priority is None or matched_priority < current_priority:
                        columns[field] = col
                        alias_priority[field] = matched_priority
                        header_span_by_field[field] = 2 if header_2 and matched_alias not in header_1 else 1
            if len(columns) > len(best_columns):
                best_sheet = ws
                best_row = row
                best_header_span = max(header_span_by_field.values(), default=1)
                best_columns = columns
                best_alias_priority = alias_priority
            elif len(columns) == len(best_columns) and columns:
                if any(alias_priority.get(field, len(alias_keys[field])) < best_alias_priority.get(field, len(alias_keys[field])) for field in columns.keys() & best_columns.keys()):
                    best_sheet = ws
                    best_row = row
                    best_header_span = max(header_span_by_field.values(), default=1)
                    best_columns = columns
                    best_alias_priority = alias_priority

    if not best_sheet or not best_columns:
        return []
    if required and not required.issubset(best_columns):
        return []

    rows: List[Dict[str, str]] = []
    empty_row_count = 0
    data_start_row = best_row + best_header_span
    for row in range(data_start_row, best_sheet.max_row + 1):
        item: Dict[str, str] = {
            SOURCE_SHEET_KEY: str(best_sheet.title),
            SOURCE_ROW_KEY: str(row),
        }
        has_value = False
        for field, col in best_columns.items():
            value = _extract_cell_text(best_sheet, row, col, field)
            item[field] = value
            has_value = has_value or bool(value)
        if not has_value:
            empty_row_count += 1
            if empty_row_count >= _EMPTY_ROW_BREAK:
                break
            continue
        field_value_keys = [normalized for field, value in item.items() if not field.startswith("__") and (normalized := normalize_key(value))]
        if field_value_keys and all(value in header_value_keys for value in field_value_keys):
            continue
        empty_row_count = 0
        rows.append(item)
    return rows
