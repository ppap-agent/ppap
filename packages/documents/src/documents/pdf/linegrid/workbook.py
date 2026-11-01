"""Workbook-related side effects for linegrid extraction."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from openpyxl.worksheet.worksheet import Worksheet

from ...excel.writer import (
    build_workbook_from_table_grid,
    save_workbook,
)
from ...excel.writer import (
    fill_worksheet_from_table_grid as _fill_worksheet_from_table_grid,
)
from ...table.models import TableGrid

if TYPE_CHECKING:
    from .extractor import TableExtractionResult


def save_workbook_from_extraction(
    extraction_result: "TableExtractionResult",
    output_path: str,
    logger: logging.Logger,
) -> bool:
    """Save an Excel workbook from a successful extraction result."""
    try:
        if not extraction_result.success:
            logger.error("提取失败，无法创建Excel: %s", extraction_result.error_message)
            return False

        logger.debug("开始创建Excel文件: %s", output_path)
        workbook = build_workbook_from_table_grid(extraction_result.table_grid, sheet_name="重建表格")
        success = save_workbook(workbook, output_path)
        if not success:
            return False

        logger.info("Excel文件创建成功: %s", output_path)
        return True
    except Exception as exc:
        logger.error("创建Excel文件失败: %s", exc)
        return False


def fill_worksheet_from_table_grid(worksheet: Worksheet, table_grid: TableGrid, logger: logging.Logger) -> None:
    """Fill worksheet from logical table grid."""
    try:
        _fill_worksheet_from_table_grid(worksheet, table_grid)
    except Exception as exc:
        logger.error("填充工作表失败: %s", exc)


def save_workbook_with_report(workbook: Any, result_info: Dict[str, Any], output_path: str) -> Dict[str, Any]:
    """Save workbook and update result report."""
    result_info = result_info.copy()
    success = save_workbook(workbook, output_path)

    if success:
        result_info["output_path"] = output_path
    else:
        result_info["success"] = False
        result_info["error_message"] = "保存失败"

    return result_info


__all__ = [
    "fill_worksheet_from_table_grid",
    "save_workbook_from_extraction",
    "save_workbook_with_report",
]
