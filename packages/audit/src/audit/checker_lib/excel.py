"""Workbook loading and cell coordinates. Business checks stay in the leaf."""

from __future__ import annotations

import hashlib
import logging
import math
import re
from io import BytesIO
from pathlib import Path

from documents.contracts import FileRef
from documents.conversion import load_workbook_auto as load_workbook_pipeline
from documents.excel import ExcelLoader, ExcelLoadOptions, load_excel_workbook
from openpyxl import Workbook
from openpyxl import load_workbook as open_xlsx
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)


def load_workbook(path: str, *, formulas: bool = False) -> Workbook:
    """Open a workbook. ``formulas=True`` keeps formula text instead of cached values."""
    if formulas:
        return ExcelLoader.load_workbook(path, ExcelLoadOptions(data_only=False))
    return load_excel_workbook(path)


def load_workbook_auto(file_url: str) -> Workbook:
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
