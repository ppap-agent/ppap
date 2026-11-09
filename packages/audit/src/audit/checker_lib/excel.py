"""Workbook loading and cell coordinates. Business checks stay in the leaf."""

from __future__ import annotations

import hashlib
import math
import re
from io import BytesIO
from pathlib import Path

from documents.excel import ExcelLoader, ExcelLoadOptions, load_excel_workbook
from openpyxl import Workbook
from openpyxl import load_workbook as open_xlsx
from openpyxl.utils import get_column_letter


def load_workbook(path: str, *, formulas: bool = False) -> Workbook:
    """Open a workbook. ``formulas=True`` keeps formula text instead of cached values."""
    if formulas:
        return ExcelLoader.load_workbook(path, ExcelLoadOptions(data_only=False))
    return load_excel_workbook(path)


def column_letter(index: int) -> str:
    return get_column_letter(index)


def load_frozen_workbook(path: str, expected_sha256: str) -> Workbook:
    """Read original XLSX cell formats from the same bytes as native evidence."""
    content = Path(path).read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError("Frozen source identity changed")
    return open_xlsx(BytesIO(content), data_only=False, keep_links=False)


def displayed_decimal_places(cell) -> int | None:
    """Read literal decimal text or a simple fixed Excel number format.

    None means unsupported/unknown display, including formulas and General
    numeric cells. ValueError means a known nonnumeric literal. No business
    precision threshold or float-based display inference belongs here.
    """
    if cell.data_type in {"f", "e"}:
        return None
    value = cell.value
    if isinstance(value, str):
        literal = re.fullmatch(r"[+-]?[0-9]+(?:\.([0-9]+))?", value.strip())
        if literal is None:
            raise ValueError("Not a literal decimal number")
        return len(literal[1] or "")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Not a finite decimal number")
    fixed = re.fullmatch(r"(?:0|#,##0)(?:\.(0+))?", cell.number_format)
    return len(fixed[1] or "") if fixed else None
