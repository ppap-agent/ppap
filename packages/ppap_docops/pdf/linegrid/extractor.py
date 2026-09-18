"""PDF表格提取器 - 完整的表格检测和重建系统"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple, Union

from ...contracts.geometry import LineLike
from ...extractors.pdf import CharacterBox, PDFExtractor, SpanBox, WordBox
from ...table import CellRegion, LineGridReconstructor, TableGrid
from ..result import OuterTextLine
from .labeler import PDFLabeler

logger = logging.getLogger(__name__)

_RR_TITLE_TOKEN = "量具重复性和再现性r&r分析报告"
_RR_GUIDE_MARKERS = ("仅供参考", "填表指南")
_RR_RELATED_MARKERS = ("r&r", "量具重复性和再现性", "%r&r")
_RR_GUIDE_LIGHTNESS_DELTA = 35.0
_NON_CONTENT_TEXT = re.compile(r"^[\s\W_]+$", re.UNICODE)


@dataclass
class TableExtractionResult:
    """表格提取结果"""

    table_grid: TableGrid
    span_boxes: List[SpanBox]
    line_elements: List[LineLike]
    intersections_count: int
    cells_count: int
    success: bool
    outer_text_lines: List[OuterTextLine] = field(default_factory=list)
    error_message: Optional[str] = None


class PDFTableExtractor:
    """PDF表格提取器 - 完整的表格检测和重建系统

    主要功能：
    1. 检测PDF中的垂直和水平线条
    2. 合并和细化这些线条为连续边框
    3. 计算垂直线和水平线的交点以定义单元格边界
    4. 将表格区域分割为单元格网格
    5. 基于位置填充文本框内容
    6. 重建Excel格式的表格，保留单元格结构和格式
    """

    def __init__(self) -> None:
        self.pdf_labeler = PDFLabeler()
        self.pdf_extractor = PDFExtractor()
        self.linegrid_reconstructor = LineGridReconstructor()

        # 设置容差参数
        self.line_tolerance = 1.0
        self.merge_tolerance = 1.0
        self.text_tolerance = 1.0

    def set_tolerances(
        self,
        line_tolerance: float = 1.0,
        merge_tolerance: float = 1.0,
        text_tolerance: float = 1.0,
        cross_cell_text_split: Any | None = None,
        text_cell_assign: Any | None = None,
    ) -> None:
        """设置容差参数

        Args:
            line_tolerance: 线条检测容差
            merge_tolerance: 线条合并容差
            text_tolerance: 文本定位容差
        """
        self.line_tolerance = line_tolerance
        self.merge_tolerance = merge_tolerance
        self.text_tolerance = text_tolerance

        self.linegrid_reconstructor.tolerance = line_tolerance
        self.linegrid_reconstructor.merge_tolerance = merge_tolerance
        self.linegrid_reconstructor.configure_cross_cell_text_split(cross_cell_text_split)
        self.linegrid_reconstructor.configure_text_cell_assign(text_cell_assign)

    def extract_table_from_pdf(
        self,
        file_content: bytes,
        page_range: Optional[Tuple[int, int]] = None,
        table_region: Optional[Tuple[Optional[float], float, Optional[float], float]] = None,
        color_filter: Optional[Dict[str, Any]] = None,
    ) -> TableExtractionResult:
        """从PDF中提取表格结构

        Args:
            file_content: PDF文件的二进制内容
            page_range: 可选的页面范围 (start_page, end_page)
            table_region: 可选的表格区域 (x0, y0, x1, y1)，x0和x1可以为None以仅使用y坐标过滤
            color_filter: 可选的颜色过滤条件，详见PDFLabeler.extract_lines_and_rects的说明

        Returns:
            TableExtractionResult: 表格提取结果
        """
        try:
            logger.info("开始从PDF中提取表格结构...")
            selected_pages: Optional[Iterable[int]] = None
            if page_range:
                start_page, end_page = page_range
                selected_pages = range(start_page, end_page + 1)
            else:
                # 针对MSA R&R“正表/填表指南”同模板并存场景：
                # 自动剔除浅灰指南页，避免与正表合并重建。
                selected_pages = self._auto_filter_msa_rr_guide_pages(file_content, selected_pages=None)

            # 步骤1: 提取线条元素
            logger.debug("步骤1: 提取线条元素...")
            line_elements: list[LineLike] = [
                element
                for element in self.pdf_labeler.extract_lines_and_rects(
                    file_content,
                    handle_rotation=True,
                    color_filter=color_filter,
                    selected_pages=selected_pages,
                )
            ]

            # 过滤页面范围
            if page_range:
                start_page, end_page = page_range
                line_elements = [elem for elem in line_elements if start_page <= elem.page <= end_page]

            logger.debug(f"提取到 {len(line_elements)} 个线条元素")

            # 步骤2: 提取文本框
            logger.debug("步骤2: 提取文本框...")
            span_boxes = self.pdf_extractor.extract_span_boxes(
                file_content,
                selected_pages=selected_pages,
            )
            word_boxes: List[WordBox] = []
            character_boxes: List[CharacterBox] = []
            if self.linegrid_reconstructor.is_cross_cell_text_split_enabled():
                word_boxes = self.pdf_extractor.extract_word_boxes(
                    file_content,
                    selected_pages=selected_pages,
                )
                character_boxes = self.pdf_extractor.extract_precise_character_boxes(
                    file_content,
                    selected_pages=selected_pages,
                )

            # 过滤文本框页面范围（与线条元素保持一致）
            if page_range:
                start_page, end_page = page_range
                span_boxes = [box for box in span_boxes if start_page <= box.page <= end_page]
                word_boxes = [box for box in word_boxes if start_page <= box.page <= end_page]
                character_boxes = [box for box in character_boxes if start_page <= box.page <= end_page]

            logger.debug(f"提取到 {len(span_boxes)} 个文本 span")
            if word_boxes:
                logger.debug(f"提取到 {len(word_boxes)} 个文本 word")
            if character_boxes:
                logger.debug(f"提取到 {len(character_boxes)} 个文本 char")

            # 步骤2.1: 使用表格区域过滤线条和文本框（如果提供）
            if table_region:
                logger.debug("步骤2.1: 使用提供的表格区域过滤线条和文本框...")
                x0, y0, x1, y1 = table_region
                original_line_count = len(line_elements)
                original_span_count = len(span_boxes)
                # 如果只提供了y坐标（x0和x1为None），则只按y坐标过滤
                if x0 is None and x1 is None:
                    line_elements = [element for element in line_elements if not (element.y1 < y0 or element.y0 > y1)]
                    span_boxes = [box for box in span_boxes if not (box.y1 < y0 or box.y0 > y1)]
                    word_boxes = [box for box in word_boxes if not (box.y1 < y0 or box.y0 > y1)]
                    character_boxes = [box for box in character_boxes if not (box.y1 < y0 or box.y0 > y1)]
                    logger.debug(f"过滤后保留 {len(line_elements)} 个线条元素 (原有 {original_line_count} 个)")
                    logger.debug(f"过滤后保留 {len(span_boxes)} 个文本 span (原有 {original_span_count} 个)")
                    if word_boxes:
                        logger.debug(f"过滤后保留 {len(word_boxes)} 个文本 word")
                    if character_boxes:
                        logger.debug(f"过滤后保留 {len(character_boxes)} 个文本 char")
                    logger.debug(f"表格区域: y=[{y0:.2f}, {y1:.2f}] (仅y坐标)")
                elif x0 is not None and x1 is not None:
                    # 保留在表格区域内的线条：线条与表格区域有重叠
                    line_elements = [element for element in line_elements if not (element.x1 < x0 or element.x0 > x1 or element.y1 < y0 or element.y0 > y1)]
                    # 保留在表格区域内的文本框：文本框与表格区域有重叠
                    span_boxes = [box for box in span_boxes if not (box.x1 < x0 or box.x0 > x1 or box.y1 < y0 or box.y0 > y1)]
                    word_boxes = [box for box in word_boxes if not (box.x1 < x0 or box.x0 > x1 or box.y1 < y0 or box.y0 > y1)]
                    character_boxes = [box for box in character_boxes if not (box.x1 < x0 or box.x0 > x1 or box.y1 < y0 or box.y0 > y1)]
                    logger.debug(f"过滤后保留 {len(line_elements)} 个线条元素 (原有 {original_line_count} 个)")
                    logger.debug(f"过滤后保留 {len(span_boxes)} 个文本 span (原有 {original_span_count} 个)")
                    if word_boxes:
                        logger.debug(f"过滤后保留 {len(word_boxes)} 个文本 word")
                    if character_boxes:
                        logger.debug(f"过滤后保留 {len(character_boxes)} 个文本 char")
                    logger.debug(f"表格区域: x=[{x0:.2f}, {x1:.2f}], y=[{y0:.2f}, {y1:.2f}]")
            else:
                logger.debug("未提供表格区域，使用所有线条元素和文本框")

            # 步骤3: 检测和合并线条
            logger.debug("步骤3: 检测和合并线条...")
            horizontal_lines, vertical_lines = self.linegrid_reconstructor.detect_and_merge_lines(line_elements)

            # 不在提取阶段按垂直线硬拆文本框。跨区域长竖线会导致过度切词，
            # 例如 "check list" 被拆成 "chec|k|list"。
            # 文本落格改由后续单元格定位承担。

            # 步骤4: 计算交点
            logger.debug("步骤4: 计算交点...")
            intersections = self.linegrid_reconstructor.compute_intersections(horizontal_lines, vertical_lines)

            # 步骤5: 分割表格网格
            logger.debug("步骤5: 分割表格网格...")
            table_grid: TableGrid = self.linegrid_reconstructor.segment_table_grid(horizontal_lines, vertical_lines, span_boxes)

            # 步骤6: 填充文本内容
            logger.debug("步骤6: 填充文本内容...")
            unmatched_spans = self.linegrid_reconstructor.fill_text_content_by_location(
                table_grid,
                span_boxes,
                word_boxes=word_boxes,
                character_boxes=character_boxes,
            )
            outer_text_lines = self._build_outer_text_lines(table_grid, unmatched_spans)
            text_diagnostics = self.linegrid_reconstructor._last_text_assignment_diagnostics
            logger.debug(
                "linegrid诊断: page_range=%s row_bands=%d unmatched_spans=%d split_spans=%d",
                page_range,
                len(self.linegrid_reconstructor._last_band_diagnostics),
                text_diagnostics.get("unmatched_span_count", 0),
                text_diagnostics.get("split_span_count", 0),
            )
            logger.debug("linegrid落格结果: matched_spans=%d unmatched_spans=%d", len(span_boxes) - len(unmatched_spans), len(unmatched_spans))

            result = TableExtractionResult(
                table_grid=table_grid,
                span_boxes=span_boxes,
                line_elements=line_elements,
                intersections_count=len(intersections),
                cells_count=len(table_grid.cells),
                success=True,
                outer_text_lines=outer_text_lines,
            )

            logger.debug(f"表格提取完成: {len(table_grid.rows) - 1} 行 x {len(table_grid.cols) - 1} 列, {len(table_grid.cells)} 个单元格, {len(intersections)} 个交点")

            return result

        except Exception as exc:
            logger.error(f"表格提取失败: {exc}")
            return TableExtractionResult(
                table_grid=TableGrid([], [], []),
                span_boxes=[],
                line_elements=[],
                intersections_count=0,
                cells_count=0,
                success=False,
                outer_text_lines=[],
                error_message=str(exc),
            )

    def _auto_filter_msa_rr_guide_pages(
        self,
        file_content: bytes,
        selected_pages: Optional[Iterable[int]],
    ) -> Optional[Iterable[int]]:
        candidate_pages = tuple(selected_pages) if selected_pages is not None else None
        try:
            import fitz
        except Exception:
            return selected_pages

        try:
            doc = fitz.open(stream=file_content, filetype="pdf")
        except Exception:
            return selected_pages

        if doc.page_count <= 0:
            doc.close()
            return selected_pages

        pages_to_scan = sorted(int(page) for page in candidate_pages) if candidate_pages is not None else list(range(doc.page_count))
        if not pages_to_scan:
            doc.close()
            return selected_pages

        page_meta: dict[int, dict[str, Any]] = {}
        for page in pages_to_scan:
            if page < 0 or page >= doc.page_count:
                continue
            page_dict = doc[page].get_text("dict")
            title_hit = False
            has_guide_marker = False
            has_rr_related_marker = False
            weighted_luma_sum = 0.0
            weighted_chars = 0.0
            for block in page_dict.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = str(span.get("text", "")).strip()
                        if not text:
                            continue
                        lowered = text.lower()
                        if _RR_TITLE_TOKEN in lowered:
                            title_hit = True
                        if any(marker in lowered for marker in _RR_RELATED_MARKERS):
                            has_rr_related_marker = True
                        if any(marker in text for marker in _RR_GUIDE_MARKERS):
                            has_guide_marker = True
                        if _NON_CONTENT_TEXT.fullmatch(text):
                            continue
                        rgb = int(span.get("color", 0))
                        r = (rgb >> 16) & 0xFF
                        g = (rgb >> 8) & 0xFF
                        b = rgb & 0xFF
                        luma = 0.299 * r + 0.587 * g + 0.114 * b
                        weight = max(1, len(text))
                        weighted_luma_sum += luma * weight
                        weighted_chars += weight

            if weighted_chars <= 0:
                continue
            page_meta[page] = {
                "luma": (weighted_luma_sum / weighted_chars),
                "title_hit": title_hit,
                "has_guide_marker": has_guide_marker,
                "has_rr_related_marker": has_rr_related_marker,
            }

        rr_title_pages = sorted(page for page, meta in page_meta.items() if bool(meta.get("title_hit")))
        if len(rr_title_pages) < 2:
            doc.close()
            return selected_pages

        rr_instances: list[dict[str, Any]] = []
        for idx, start_page in enumerate(rr_title_pages):
            next_start = rr_title_pages[idx + 1] if idx + 1 < len(rr_title_pages) else (max(pages_to_scan) + 1)
            instance_pages: list[int] = [start_page]
            for page in range(start_page + 1, next_start):
                meta = page_meta.get(page)
                if meta is None:
                    break
                if bool(meta.get("has_rr_related_marker")) or bool(meta.get("has_guide_marker")):
                    instance_pages.append(page)
                    continue
                break

            related_lumas = [float(page_meta[p]["luma"]) for p in instance_pages if p in page_meta and bool(page_meta[p].get("has_rr_related_marker") or page_meta[p].get("title_hit"))]
            if not related_lumas:
                continue

            rr_instances.append(
                {
                    "start": start_page,
                    "pages": tuple(instance_pages),
                    "avg_luma": sum(related_lumas) / len(related_lumas),
                    "has_guide_marker": any(bool(page_meta.get(p, {}).get("has_guide_marker")) for p in instance_pages),
                }
            )

        if len(rr_instances) < 2:
            doc.close()
            return selected_pages

        darkest_luma = min(float(instance["avg_luma"]) for instance in rr_instances)
        guide_instances = [instance for instance in rr_instances if bool(instance["has_guide_marker"]) or (float(instance["avg_luma"]) - darkest_luma) >= _RR_GUIDE_LIGHTNESS_DELTA]
        if not guide_instances:
            doc.close()
            return selected_pages

        guide_pages = {int(page) for instance in guide_instances for page in instance["pages"]}

        universe_pages = pages_to_scan
        filtered_pages = tuple(page for page in universe_pages if page not in guide_pages)
        if not filtered_pages:
            doc.close()
            return selected_pages

        logger.info(
            "MSA R&R实例过滤: 保留页面=%s, 过滤页面=%s, 实例=%s",
            list(filtered_pages),
            sorted(guide_pages),
            [
                {
                    "start": int(instance["start"]),
                    "pages": list(instance["pages"]),
                    "avg_luma": round(float(instance["avg_luma"]), 2),
                    "guide": bool(instance["has_guide_marker"]),
                }
                for instance in rr_instances
            ],
        )
        doc.close()
        return filtered_pages

    def _group_spans_by_line(self, spans: List[SpanBox]) -> List[List[SpanBox]]:
        if not spans:
            return []

        grouped_lines: List[List[SpanBox]] = []
        current_line: List[SpanBox] = []
        current_mid_y: Optional[float] = None
        line_tolerance = max(4.0, self.text_tolerance * 4.0)

        for span in sorted(spans, key=lambda item: (item.y0, item.x0)):
            span_mid_y = (span.y0 + span.y1) / 2
            if current_mid_y is None or abs(span_mid_y - current_mid_y) <= line_tolerance:
                current_line.append(span)
                if current_mid_y is None:
                    current_mid_y = span_mid_y
                else:
                    current_mid_y = (current_mid_y * (len(current_line) - 1) + span_mid_y) / len(current_line)
                continue

            grouped_lines.append(sorted(current_line, key=lambda item: item.x0))
            current_line = [span]
            current_mid_y = span_mid_y

        if current_line:
            grouped_lines.append(sorted(current_line, key=lambda item: item.x0))

        return grouped_lines

    def _build_outer_text_lines(self, table_grid: TableGrid, unmatched_spans: List[SpanBox]) -> List[OuterTextLine]:
        if not unmatched_spans or len(table_grid.rows) < 2:
            return []

        top = table_grid.rows[0]
        bottom = table_grid.rows[-1]
        outer_lines: List[OuterTextLine] = []

        outer_groups: tuple[tuple[Literal["header", "footer"], list[SpanBox]], ...] = (
            ("header", [span for span in unmatched_spans if span.y1 < top]),
            ("footer", [span for span in unmatched_spans if span.y0 > bottom]),
        )
        for outer_position, spans in outer_groups:
            for line_spans in self._group_spans_by_line(spans):
                line_text = " ".join(span.text.strip() for span in line_spans if span.text.strip()).strip()
                if not line_text:
                    continue
                outer_lines.append(
                    OuterTextLine(
                        text=line_text,
                        x0=min(span.x0 for span in line_spans),
                        y0=min(span.y0 for span in line_spans),
                        x1=max(span.x1 for span in line_spans),
                        y1=max(span.y1 for span in line_spans),
                        position=outer_position,
                    )
                )

        return outer_lines

    def save_workbook_from_extraction(
        self,
        extraction_result: TableExtractionResult,
        output_path: str = "extracted_table.xlsx",
    ) -> bool:
        """从提取结果保存Excel工作簿文件。"""
        from .workbook import save_workbook_from_extraction

        return save_workbook_from_extraction(
            extraction_result,
            output_path,
            logger,
        )

    def visualize_extraction(
        self,
        file_content: bytes,
        extraction_result: TableExtractionResult,
        output_path: str = "visualized_extraction.pdf",
    ) -> bool:
        """可视化表格提取结果。"""
        from .debug import visualize_extraction

        return visualize_extraction(
            file_content,
            extraction_result,
            pdf_labeler=self.pdf_labeler,
            linegrid_reconstructor=self.linegrid_reconstructor,
            output_path=output_path,
            logger=logger,
        )

    def export_extraction_info(
        self,
        extraction_result: TableExtractionResult,
        output_path: str = "extraction_info.json",
    ) -> bool:
        """导出表格提取信息到JSON文件。"""
        from .debug import export_extraction_info

        return export_extraction_info(
            extraction_result,
            output_path,
            logger,
        )

    # -------- New: cross-page helpers --------
    def _find_y_region_between_markers(
        self,
        file_content: bytes,
        page: int,
        start_markers: List[str],
        end_markers: List[str],
    ) -> Optional[Tuple[float, float]]:
        """Find y-range between start and end markers on a given page.

        Returns (y0, y1) where y0 is the bottom of the start marker box and y1 is the top of the end marker box.
        If not found, returns None.
        """
        try:
            boxes = self.pdf_extractor.extract_text_boxes(file_content, selected_pages={page})
            page_boxes = [b for b in boxes if b.page == page]
            if not page_boxes:
                return None

            # Find candidate y positions
            start_y_bottoms: List[float] = []
            end_y_tops: List[float] = []

            for b in page_boxes:
                text = b.text
                if any(marker in text for marker in start_markers):
                    start_y_bottoms.append(b.y1)
                if any(marker in text for marker in end_markers):
                    end_y_tops.append(b.y0)

            if not start_y_bottoms or not end_y_tops:
                return None

            y0 = max(start_y_bottoms)
            y1 = min(end_y_tops)
            if y1 <= y0:
                return None
            return (y0, y1)
        except Exception:
            return None

    def _extract_single_page_table(
        self,
        file_content: bytes,
        page: int,
        y_region: Optional[Tuple[float, float]],
        color_filter: Optional[Dict[str, Any]],
    ) -> Optional[TableExtractionResult]:
        """Run the existing single-page pipeline for a specific page with optional y-only region filter."""
        try:
            table_region = None
            if y_region:
                table_region = (None, y_region[0], None, y_region[1])
            return self.extract_table_from_pdf(
                file_content,
                page_range=(page, page),
                table_region=table_region,
                color_filter=color_filter,
            )
        except Exception as exc:
            logger.warning(f"页面 {page} 提取失败: {exc}")
            return None

    def _combine_table_grids(self, grids: List[TableGrid], drop_repeated_header: bool = True) -> TableGrid:
        """Combine multiple per-page TableGrids into a single grid by stacking rows.

        Assumptions:
        - Column count is consistent across pages (same template). If not, uses the first grid's columns.
        - Content has already been filled into cells per page.
        - Optionally drops the first row from subsequent grids to avoid repeated headers.
        """
        valid_grids = [g for g in grids if g and g.rows and g.cols]
        if not valid_grids:
            return TableGrid([], [], [])

        ref_cols = valid_grids[0].cols
        total_rows = 0
        combined_cells: List[CellRegion] = []

        for idx, g in enumerate(valid_grids):
            rows_in_g = max(0, len(g.rows) - 1)
            if rows_in_g == 0:
                continue

            # Decide starting row index within this grid (skip header if requested)
            start_row_local = 1 if (drop_repeated_header and idx > 0) else 0
            if start_row_local >= rows_in_g:
                continue

            row_offset = total_rows

            for cell in g.cells:
                # Skip cells fully within the skipped header row
                if cell.row_end < start_row_local:
                    continue
                # Clamp to start from start_row_local
                new_row_start = max(cell.row_start, start_row_local) - start_row_local + row_offset
                new_row_end = max(cell.row_end, start_row_local) - start_row_local + row_offset

                combined_cells.append(
                    CellRegion(
                        row_start=new_row_start,
                        row_end=new_row_end,
                        col_start=cell.col_start,
                        col_end=cell.col_end,
                        content=cell.content,
                    )
                )

            # Update total_rows after stacking this grid (minus the skipped header if any)
            total_rows += rows_in_g - start_row_local

        # Build synthetic continuous coordinates for rows/cols (monotonic indices suffice for workbook build)
        combined_rows = [float(i) for i in range(total_rows + 1)]
        combined_cols = [float(i) for i in range(len(ref_cols))]
        return TableGrid(rows=combined_rows, cols=combined_cols, cells=combined_cells)

    def extract_table_across_pages_by_markers(
        self,
        file_content: bytes,
        pages: List[int],
        start_markers: List[str],
        end_markers: List[str],
        color_filter: Optional[Dict[str, Any]] = None,
        drop_repeated_header: bool = True,
    ) -> TableExtractionResult:
        """Extract a table spanning multiple pages, using marker texts to crop vertically per page,
        then stitch the per-page tables by stacking rows.

        Args:
            file_content: PDF bytes
            pages: 0-based page indices to include (e.g., [1, 2] for pages 2–3)
            start_markers: texts indicating the area above the table (we use the bottom y of the matched box)
            end_markers: texts indicating the area below the table (we use the top y of the matched box)
            color_filter: optional color filter forwarded to line extractor
            drop_repeated_header: whether to drop the first row of subsequent pages (common header)

        Returns:
            TableExtractionResult with combined grid and merged spans.
        """
        per_page_results: List[TableExtractionResult] = []
        per_page_grids: List[TableGrid] = []
        per_page_spans: List[SpanBox] = []
        per_page_line_elements: List[LineLike] = []
        total_intersections = 0

        for p in pages:
            y_region = self._find_y_region_between_markers(file_content, p, start_markers, end_markers)
            res = self._extract_single_page_table(file_content, p, y_region, color_filter)
            if not res or not res.success:
                logger.warning(f"跳过页面 {p}，未获得有效表格")
                continue
            per_page_results.append(res)
            per_page_grids.append(res.table_grid)
            per_page_spans.extend(res.span_boxes)
            per_page_line_elements.extend(res.line_elements)
            total_intersections += res.intersections_count

        combined_grid = self._combine_table_grids(per_page_grids, drop_repeated_header=drop_repeated_header)

        if not combined_grid.rows:
            return TableExtractionResult(
                table_grid=TableGrid([], [], []),
                span_boxes=[],
                line_elements=[],
                intersections_count=0,
                cells_count=0,
                success=False,
                outer_text_lines=[],
                error_message="未能组合出跨页表格",
            )

        return TableExtractionResult(
            table_grid=combined_grid,
            span_boxes=per_page_spans,
            line_elements=per_page_line_elements,
            intersections_count=total_intersections,
            cells_count=len(combined_grid.cells),
            success=True,
            outer_text_lines=[],
        )

    def _fill_worksheet_from_table_grid(self, worksheet: Any, table_grid: TableGrid) -> None:
        """将表格网格数据填充到Excel工作表中。"""
        from .workbook import fill_worksheet_from_table_grid

        fill_worksheet_from_table_grid(worksheet, table_grid, logger)


# 便捷函数
def extract_table_from_pdf_file(
    input_path: Union[str, Path],
    output_path: str = "extracted_table.xlsx",
    color_filter: Optional[Dict[str, Any]] = None,
) -> TableExtractionResult:
    """从PDF文件提取表格的便捷函数 - 使用组合实现向后兼容

    Args:
        input_path: 输入PDF文件路径
        output_path: 输出Excel文件路径
        color_filter: 颜色过滤器配置，参见extract_table_from_pdf的color_filter参数

    Returns:
        TableExtractionResult: 表格提取结果
    """
    extractor = PDFTableExtractor()

    with open(input_path, "rb") as f:
        file_content = f.read()

    # 使用组合：纯函数 + 副作用函数
    result = extractor.extract_table_from_pdf(file_content, color_filter=color_filter)  # 纯函数
    if result.success:
        extractor.save_workbook_from_extraction(result, output_path)  # 包含副作用

    return result


def extract_table_with_visualization(
    input_path: Union[str, Path],
    excel_output: str = "extracted_table.xlsx",
    viz_output: str = "visualization.pdf",
    color_filter: Optional[Dict[str, Any]] = None,
) -> TableExtractionResult:
    """提取表格并创建可视化的便捷函数 - 使用组合实现

    Args:
        input_path: 输入PDF文件路径
        excel_output: 输出Excel文件路径
        viz_output: 可视化PDF文件路径
        color_filter: 颜色过滤器配置，参见extract_table_from_pdf的color_filter参数

    Returns:
        TableExtractionResult: 表格提取结果
    """
    extractor = PDFTableExtractor()

    with open(input_path, "rb") as f:
        file_content = f.read()

    # 使用组合：纯函数 + 副作用函数
    result = extractor.extract_table_from_pdf(file_content, color_filter=color_filter)  # 纯函数

    if result.success:
        # 副作用操作
        extractor.save_workbook_from_extraction(result, excel_output)
        extractor.visualize_extraction(file_content, result, viz_output)

    return result


__all__ = [
    "PDFTableExtractor",
    "TableExtractionResult",
    "extract_table_from_pdf_file",
    "extract_table_with_visualization",
]
