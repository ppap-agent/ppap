"""Debug helpers for linegrid extraction."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from .labeler import LineElement

if TYPE_CHECKING:
    from ...table.linegrid import LineGridReconstructor
    from .extractor import TableExtractionResult
    from .labeler import PDFLabeler


def visualize_extraction(
    file_content: bytes,
    extraction_result: "TableExtractionResult",
    *,
    pdf_labeler: "PDFLabeler",
    linegrid_reconstructor: "LineGridReconstructor",
    output_path: str,
    logger: logging.Logger,
) -> bool:
    """Visualize extracted lines/intersections and save a debug PDF."""
    try:
        logger.debug("开始创建表格提取可视化...")

        # 设置可视化颜色
        pdf_labeler.set_color("red")
        pdf_labeler.set_opacity(0.7)
        result_content = file_content

        # 确定目标页面 - 从提取结果中获取实际使用的页面
        target_pages: set[int] = set()
        if extraction_result.line_elements:
            target_pages.update(elem.page for elem in extraction_result.line_elements)
        if not target_pages:
            target_pages = {0}  # 默认第一页

        logger.debug(f"可视化目标页面: {sorted(target_pages)}")

        # 重新提取线条元素，但不应用旋转变换（用于可视化绘制）
        # 这样得到的坐标与PDF原始坐标系统匹配，但是这样的
        logger.debug("重新提取线条元素（不应用旋转变换）用于可视化...")
        # TODO 不是所有表格边框都是黑色
        black_fill = {
            "fill_colors": [(0, 0, 0)],  # 仅黑色
            "tolerance": 0.1,
        }

        original_line_elements = pdf_labeler.extract_lines_and_rects(file_content, handle_rotation=False, color_filter=black_fill)

        # 过滤到目标页面
        original_line_elements = [elem for elem in original_line_elements if elem.page in target_pages]

        # 绘制原始线条元素（可选，用于调试）
        # result_content = pdf_labeler.draw_line_elements(
        #     result_content,
        #     original_line_elements,
        #     thickness=0.3,
        #     color='green',
        #     opacity=0.2
        # )

        # 按页面分别处理线条，避免多页面叠加问题
        merged_elements: list[LineElement] = []
        marks: list[dict[str, object]] = []

        # 为每个页面分别检测和合并线条
        for page_num in target_pages:
            # 获取该页面的原始线条元素
            page_original_elements = [elem for elem in original_line_elements if elem.page == page_num]

            if not page_original_elements:
                continue

            # 检测和合并该页面的线条
            page_horizontal_lines, page_vertical_lines = linegrid_reconstructor.detect_and_merge_lines(page_original_elements)

            # 为该页面创建线条元素
            for line in page_horizontal_lines + page_vertical_lines:
                merged_elements.append(
                    LineElement(
                        type="line",
                        page=page_num,
                        x0=line.x0,
                        y0=line.y0,
                        x1=line.x1,
                        y1=line.y1,
                    )
                )

            # 计算该页面的交点
            page_intersections = linegrid_reconstructor.compute_intersections(page_horizontal_lines, page_vertical_lines)

            # 为该页面的交点添加标记
            for intersection in page_intersections:
                marks.append(
                    {
                        "type": "dot",
                        "page": page_num,
                        "x": intersection.x,
                        "y": intersection.y,
                        "size": 1.0,
                        "color": "blue",
                    }
                )

        # 绘制合并后的线条
        if merged_elements:
            result_content = pdf_labeler.draw_line_elements(
                result_content,
                merged_elements,
                thickness=1,
                color="red",
                opacity=0.5,
            )

        if marks:
            result_content = pdf_labeler.add_multiple_marks(result_content, marks)

            # 保存可视化结果
            pdf_labeler.save_labeled_pdf(result_content, output_path)
            logger.debug(f"可视化文件已保存到: {output_path}")

        return True

    except Exception as exc:
        logger.error(f"创建可视化文件失败: {exc}")
        return False


def export_extraction_info(
    extraction_result: "TableExtractionResult",
    output_path: str,
    logger: logging.Logger,
) -> bool:
    """Export extraction details as JSON."""
    try:
        info: dict[str, Any] = {
            "success": extraction_result.success,
            "error_message": extraction_result.error_message,
            "table_structure": {
                "rows": len(extraction_result.table_grid.rows) - 1 if extraction_result.table_grid.rows else 0,
                "cols": len(extraction_result.table_grid.cols) - 1 if extraction_result.table_grid.cols else 0,
                "cells": extraction_result.cells_count,
                "intersections": extraction_result.intersections_count,
            },
            "extracted_elements": {
                "line_elements": len(extraction_result.line_elements),
                "text_boxes": len(extraction_result.span_boxes),
                "outer_text_lines": len(extraction_result.outer_text_lines),
            },
            "outer_text_lines": [],
            "cells_content": [],
        }

        for line in extraction_result.outer_text_lines:
            info["outer_text_lines"].append(
                {
                    "text": line.text,
                    "x0": line.x0,
                    "y0": line.y0,
                    "x1": line.x1,
                    "y1": line.y1,
                    "position": line.position,
                }
            )

        # 添加单元格内容信息
        for i, cell in enumerate(extraction_result.table_grid.cells):
            cell_info = {
                "cell_id": i + 1,
                "row_span": f"{cell.row_start}-{cell.row_end}",
                "col_span": f"{cell.col_start}-{cell.col_end}",
                "content": cell.content,
                "is_merged": (cell.row_start != cell.row_end) or (cell.col_start != cell.col_end),
            }
            info["cells_content"].append(cell_info)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)

        logger.debug(f"提取信息已导出到: {output_path}")
        return True

    except Exception as exc:
        logger.error(f"导出提取信息失败: {exc}")
        return False


__all__ = [
    "export_extraction_info",
    "visualize_extraction",
]
