"""Workbook loading and cell coordinates. Business checks stay in the leaf."""

from __future__ import annotations

from documents.excel import ExcelLoader, ExcelLoadOptions, load_excel_workbook
from openpyxl import Workbook
from openpyxl.utils import get_column_letter


def load_workbook(path: str, *, formulas: bool = False) -> Workbook:
    """Open a workbook. ``formulas=True`` keeps formula text instead of cached values."""
    if formulas:
        return ExcelLoader.load_workbook(path, ExcelLoadOptions(data_only=False))
    return load_excel_workbook(path)


def column_letter(index: int) -> str:
    return get_column_letter(index)
