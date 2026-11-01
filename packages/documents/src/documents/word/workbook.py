"""Lossless workbook view over structured Word table grids."""

from __future__ import annotations

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from .models import WordDocument, WordTableBlock


class WordWorkbookAdapterError(ValueError):
    """Raised when an invalid Word grid cannot produce a trustworthy view."""


def _table_blocks(document: WordDocument) -> list[WordTableBlock]:
    return [block for block in document.blocks if isinstance(block, WordTableBlock)]


def _write_table(sheet: Worksheet, table: WordTableBlock) -> None:
    if not table.valid:
        raise WordWorkbookAdapterError(f"Word 表格结构无效: {table.source.path}")

    for cell in table.cells:
        if not cell.origin:
            continue
        row = cell.row + 1
        column = cell.col + 1
        sheet.cell(row=row, column=column, value=cell.text)
        if cell.rowspan > 1 or cell.colspan > 1:
            sheet.merge_cells(
                start_row=row,
                start_column=column,
                end_row=row + cell.rowspan - 1,
                end_column=column + cell.colspan - 1,
            )


def word_document_to_workbook(document: WordDocument) -> Workbook:
    """Create an in-memory workbook view without reinterpreting Word cells."""
    if not document.valid:
        codes = ", ".join(item.code for item in document.diagnostics) or "unknown"
        raise WordWorkbookAdapterError(f"Word 文档结构无效: {codes}")

    workbook = Workbook()
    default_sheet = workbook.active
    assert isinstance(default_sheet, Worksheet)
    tables = _table_blocks(document)
    if not tables:
        default_sheet.title = "Word表1"
        return workbook

    for index, table in enumerate(tables, start=1):
        sheet = default_sheet if index == 1 else workbook.create_sheet()
        sheet.title = f"Word表{index}"
        _write_table(sheet, table)
    return workbook


__all__ = ["WordWorkbookAdapterError", "word_document_to_workbook"]
