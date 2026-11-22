"""Workbook loading and cell coordinates. Business checks stay in the leaf."""

from __future__ import annotations

import logging

from documents.contracts import FileRef
from documents.conversion import load_workbook_auto as load_workbook_pipeline
from documents.excel import ExcelLoader, ExcelLoadOptions, load_excel_workbook
from openpyxl import Workbook

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
