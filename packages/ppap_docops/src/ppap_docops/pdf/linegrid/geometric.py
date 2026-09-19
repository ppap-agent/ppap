"""Geometric PDF->Excel conversion flow."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import fitz

from ...excel.writer import write_table_grids
from ...table.models import CellRegion, TableGrid
from .geometric_cluster import CellCoord, find_cell_indices_for_cluster
from .geometric_cluster import cluster_cells as default_cluster_cells

logger = logging.getLogger(__name__)


CellClusterFn = Callable[[list[CellCoord]], list[list[CellCoord]]]


def convert_pdf_geometric_multitable(
    pdf_path: str | Path,
    excel_path: str,
    *,
    _cluster_cells: CellClusterFn = default_cluster_cells,
) -> bool:
    """Convert one PDF (multi-page/multi-table) into one Excel workbook."""
    try:
        with fitz.open(pdf_path) as pdf:
            all_cells: list[CellCoord] = []
            for i in range(len(pdf)):
                page = pdf[i]
                finder = page.find_tables()
                for tab in finder.tables:
                    if tab.cells:
                        extracted_rows = tab.extract() or []
                        flat_texts = [t or "" for row in extracted_rows for t in row]
                        for j, cell in enumerate(tab.cells):
                            text = flat_texts[j] if j < len(flat_texts) else ""
                            all_cells.append(cell + (i, text))

            if not all_cells:
                logger.warning("在整个文档中未找到任何单元格: %s", pdf_path)
                return False

            table_clusters = _cluster_cells(all_cells)
            logger.debug("检测到 %d 个独立的表格: %s", len(table_clusters), pdf_path)
            if not table_clusters:
                logger.warning("单元格聚类后未形成有效表格: %s", pdf_path)
                return False

            sheets: list[tuple[str, TableGrid]] = []
            for i, cluster in enumerate(table_clusters):
                x_boundaries = sorted(list(set(c[0] for c in cluster).union(set(c[2] for c in cluster))))
                y_boundaries = sorted(list(set(c[1] for c in cluster).union(set(c[3] for c in cluster))))
                cells: list[CellRegion] = []

                for cell in cluster:
                    text = cell[5].strip()
                    start_row, start_col, end_row, end_col = find_cell_indices_for_cluster(cell, x_boundaries, y_boundaries)
                    if start_row == -1:
                        continue

                    cells.append(
                        CellRegion(
                            row_start=start_row,
                            row_end=end_row,
                            col_start=start_col,
                            col_end=end_col,
                            content=text,
                        )
                    )

                if not cells:
                    continue

                sheets.append(
                    (
                        f"Table_{i + 1}",
                        TableGrid(
                            rows=[float(y) for y in y_boundaries],
                            cols=[float(x) for x in x_boundaries],
                            cells=cells,
                        ),
                    )
                )

        if not sheets:
            logger.warning("未生成任何有效表格工作表: %s", pdf_path)
            return False

        ok = write_table_grids(excel_path, sheets)
        if ok:
            logger.info("已成功将所有表格转换为: %s", excel_path)
        return ok

    except Exception as exc:
        logger.exception("处理 PDF 转 Excel 过程中发生错误: %s", exc)
        return False


__all__ = [
    "convert_pdf_geometric_multitable",
    "fitz",
]
