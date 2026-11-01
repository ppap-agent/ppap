"""Line-grid table reconstruction core."""

from __future__ import annotations

import logging
import math
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..contracts.geometry import LineLike
from .models import CellRegion, TableGrid

logger = logging.getLogger(__name__)


@dataclass
class Line:
    """线条对象"""

    x0: float
    y0: float
    x1: float
    y1: float
    is_horizontal: bool

    @property
    def length(self) -> float:
        return math.sqrt((self.x1 - self.x0) ** 2 + (self.y1 - self.y0) ** 2)

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2

    def overlaps_with(self, other: "Line", tolerance: float = 1.0) -> bool:
        """检查两条线是否重叠"""
        if self.is_horizontal != other.is_horizontal:
            return False

        if self.is_horizontal:
            # 水平线：Y坐标相近且X范围重叠
            if abs(self.y0 - other.y0) > tolerance:
                return False
            return not (self.x1 < other.x0 - tolerance or other.x1 < self.x0 - tolerance)
        else:
            # 垂直线：X坐标相近且Y范围重叠
            if abs(self.x0 - other.x0) > tolerance:
                return False
            return not (self.y1 < other.y0 - tolerance or other.y1 < self.y0 - tolerance)

    def merge_with(self, other: "Line") -> "Line":
        """合并两条重叠的线"""
        if self.is_horizontal:
            return Line(
                x0=min(self.x0, other.x0),
                y0=(self.y0 + other.y0) / 2,  # 取Y坐标平均值
                x1=max(self.x1, other.x1),
                y1=(self.y1 + other.y1) / 2,
                is_horizontal=True,
            )
        else:
            return Line(
                x0=(self.x0 + other.x0) / 2,  # 取X坐标平均值
                y0=min(self.y0, other.y0),
                x1=(self.x1 + other.x1) / 2,
                y1=max(self.y1, other.y1),
                is_horizontal=False,
            )


@dataclass
class Intersection:
    """交点对象"""

    x: float
    y: float
    h_line: Line
    v_line: Line


@dataclass(frozen=True)
class _TextAssignmentIndex:
    cell_bboxes: Dict[int, Tuple[float, float, float, float]]
    bucket_cell_indexes: Dict[Tuple[int, int], Tuple[int, ...]]
    active_boundary_lines_by_row: Dict[int, Tuple[Line, ...]]
    all_cell_indexes: Tuple[int, ...]


@dataclass
class _TextAssignmentPass:
    assignment_index: _TextAssignmentIndex
    processed_text_boxes: List[Any]
    split_span_count: int
    cell_text_map: Dict[int, List[Any]]
    matched_text_indexes: Set[int]
    unmatched_indexes: List[int]
    unmatched_spans: List[Any]
    cross_cell_fallback_diagnostics: _CrossCellFallbackDiagnostics


@dataclass(frozen=True)
class _CrossCellTextSplitOptions:
    enabled: bool = False
    scope: str = "whole_table"
    strategy: str = "words_then_char"
    require_active_boundary: bool = True
    min_candidate_cells: int = 2
    min_fragment_overlap_ratio: float = 0.15
    keep_original_on_ambiguous: bool = True


@dataclass(frozen=True)
class _TextCellAssignOptions:
    x_anchor_ratio: float = 0.5
    y_anchor_ratio: float = 0.2


@dataclass
class _CrossCellFallbackDiagnostics:
    word_fallback_span_count: int = 0
    word_fallback_fragment_count: int = 0
    char_fallback_word_count: int = 0
    char_fallback_fragment_count: int = 0
    cross_cell_fallback_rejected_count: int = 0


@dataclass
class _CrossCellFallbackResult:
    assignments: List[Tuple[int, Any]]
    char_fallback_word_count: int = 0
    char_fallback_fragment_count: int = 0


class LineGridReconstructor:
    """Line-grid reconstructor with merge/intersection/cell detection."""

    def __init__(self) -> None:
        self.tolerance = 1.0  # 坐标容差，用于判断线条是否重合
        self.merge_tolerance = 0.5  # 线条合并容差
        self._cross_cell_text_split_options = _CrossCellTextSplitOptions()
        self._text_cell_assign_options = _TextCellAssignOptions()
        self._last_band_diagnostics: List[Dict[str, Any]] = []
        self._last_text_assignment_diagnostics: Dict[str, int] = {
            "matched_span_count": 0,
            "unmatched_span_count": 0,
            "split_span_count": 0,
            "synthetic_cell_count": 0,
            "synthetic_assigned_span_count": 0,
            "word_fallback_span_count": 0,
            "word_fallback_fragment_count": 0,
            "char_fallback_word_count": 0,
            "char_fallback_fragment_count": 0,
            "cross_cell_fallback_rejected_count": 0,
        }

    def configure_cross_cell_text_split(self, spec: Any | None) -> None:
        if spec is None:
            self._cross_cell_text_split_options = _CrossCellTextSplitOptions()
            return

        scope = getattr(spec, "scope", "whole_table")
        strategy = getattr(spec, "strategy", "words_then_char")
        scope_value = getattr(scope, "value", scope)
        strategy_value = getattr(strategy, "value", strategy)
        self._cross_cell_text_split_options = _CrossCellTextSplitOptions(
            enabled=bool(getattr(spec, "enabled", False)),
            scope=str(scope_value).lower(),
            strategy=str(strategy_value).lower(),
            require_active_boundary=bool(getattr(spec, "require_active_boundary", True)),
            min_candidate_cells=max(2, int(getattr(spec, "min_candidate_cells", 2))),
            min_fragment_overlap_ratio=max(
                0.01,
                min(1.0, float(getattr(spec, "min_fragment_overlap_ratio", 0.15))),
            ),
            keep_original_on_ambiguous=bool(getattr(spec, "keep_original_on_ambiguous", True)),
        )

    def is_cross_cell_text_split_enabled(self) -> bool:
        return self._cross_cell_text_split_options.enabled

    def configure_text_cell_assign(self, spec: Any | None) -> None:
        if spec is None:
            self._text_cell_assign_options = _TextCellAssignOptions()
            return

        self._text_cell_assign_options = _TextCellAssignOptions(
            x_anchor_ratio=float(getattr(spec, "x_anchor_ratio", 0.5)),
            y_anchor_ratio=float(getattr(spec, "y_anchor_ratio", 0.2)),
        )

    def detect_and_merge_lines(self, elements: Sequence[LineLike]) -> Tuple[List[Line], List[Line]]:
        """检测并合并垂直和水平线条

        Args:
            elements: 线条元素列表

        Returns:
            Tuple[List[Line], List[Line]]: (水平线列表, 垂直线列表)
        """
        # 分离水平线和垂直线
        horizontal_elements = []
        vertical_elements = []

        for element in elements:
            # 判断是水平线还是垂直线
            if abs(element.y0 - element.y1) < self.tolerance:  # 水平线
                horizontal_elements.append(
                    Line(
                        x0=min(element.x0, element.x1),
                        y0=element.y0,
                        x1=max(element.x0, element.x1),
                        y1=element.y1,
                        is_horizontal=True,
                    )
                )
            elif abs(element.x0 - element.x1) < self.tolerance:  # 垂直线
                vertical_elements.append(
                    Line(
                        x0=element.x0,
                        y0=min(element.y0, element.y1),
                        x1=element.x1,
                        y1=max(element.y0, element.y1),
                        is_horizontal=False,
                    )
                )

        logger.debug(f"检测到 {len(horizontal_elements)} 条水平线, {len(vertical_elements)} 条垂直线")

        # 合并重叠的线条
        merged_horizontal = self._merge_overlapping_lines(horizontal_elements)
        merged_vertical = self._merge_overlapping_lines(vertical_elements)

        logger.debug(f"合并后 {len(merged_horizontal)} 条水平线, {len(merged_vertical)} 条垂直线")

        return merged_horizontal, merged_vertical

    def _merge_overlapping_lines(self, lines: List[Line]) -> List[Line]:
        """合并重叠的线条"""
        if not lines:
            return []

        merged_lines = []
        processed = set()

        for i, line in enumerate(lines):
            if i in processed:
                continue

            current_line = line
            processed.add(i)

            # 查找所有与当前线重叠的线
            changed = True
            while changed:
                changed = False
                for j, other_line in enumerate(lines):
                    if j in processed:
                        continue

                    if current_line.overlaps_with(other_line, self.merge_tolerance):
                        current_line = current_line.merge_with(other_line)
                        processed.add(j)
                        changed = True

            merged_lines.append(current_line)

        return merged_lines

    def compute_intersections(self, horizontal_lines: List[Line], vertical_lines: List[Line]) -> List[Intersection]:
        """计算水平线和垂直线的交点

        Args:
            horizontal_lines: 水平线列表
            vertical_lines: 垂直线列表

        Returns:
            List[Intersection]: 交点列表
        """
        intersections = []

        for h_line in horizontal_lines:
            for v_line in vertical_lines:
                # 检查是否有交点
                if h_line.x0 - self.tolerance <= v_line.x0 <= h_line.x1 + self.tolerance and v_line.y0 - self.tolerance <= h_line.y0 <= v_line.y1 + self.tolerance:
                    intersection = Intersection(x=v_line.x0, y=h_line.y0, h_line=h_line, v_line=v_line)
                    intersections.append(intersection)

        logger.debug(f"计算得到 {len(intersections)} 个交点")

        return intersections

    def _merge_close_coordinates(self, coords: List[float], tolerance: float) -> List[float]:
        """合并相近的坐标

        Args:
            coords: 已排序的坐标列表
            tolerance: 合并容差

        Returns:
            List[float]: 合并后的坐标列表
        """
        if not coords:
            return []

        merged_coords = [coords[0]]

        for coord in coords[1:]:
            # 如果当前坐标与最后一个合并坐标的距离小于容差，则合并
            if abs(coord - merged_coords[-1]) <= tolerance:
                # 取平均值作为合并后的坐标
                merged_coords[-1] = (merged_coords[-1] + coord) / 2
            else:
                # 否则添加为新坐标
                merged_coords.append(coord)

        return merged_coords

    def segment_table_grid(
        self,
        horizontal_lines: List[Line],
        vertical_lines: List[Line],
        text_boxes: Optional[List[Any]] = None,
    ) -> TableGrid:
        """将表格区域分割为网格单元格

        Args:
            horizontal_lines: 水平线列表
            vertical_lines: 垂直线列表

        Returns:
            TableGrid: 表格网格结构
        """
        if not horizontal_lines or not vertical_lines:
            self._last_band_diagnostics = []
            return TableGrid([], [], [])

        border_collapse_tolerance = self._border_collapse_tolerance()

        # 行边界来自水平线，列边界先取全局垂直线作为回退边界
        y_coords = sorted({round(line.y0, 1) for line in horizontal_lines})
        rows = self._merge_close_coordinates(y_coords, border_collapse_tolerance)

        global_x_coords = sorted({round(line.x0, 1) for line in vertical_lines})
        global_cols = self._merge_close_coordinates(global_x_coords, border_collapse_tolerance)

        # 按 row band 选择局部列边界，避免把不同区域强行对齐到同一列网格
        band_boundaries = self._collect_band_boundaries(rows, horizontal_lines, vertical_lines, border_collapse_tolerance, global_cols, text_boxes or [])
        self._last_band_diagnostics = self._build_band_diagnostics(rows, global_cols, band_boundaries)
        union_cols: List[float] = list(global_cols)
        for boundaries in band_boundaries.values():
            union_cols.extend(boundaries)
        cols = self._merge_close_coordinates(sorted(set(union_cols)), border_collapse_tolerance)

        logger.debug(f"表格结构: {len(rows) - 1} 行 x {len(cols) - 1} 列")
        logger.debug(f"行坐标: {rows}")
        logger.debug(f"列坐标: {cols}")

        # 分析单元格区域
        cells = self._analyze_cells(horizontal_lines, rows, cols, band_boundaries)

        return TableGrid(rows, cols, cells)

    def _border_collapse_tolerance(self) -> float:
        """折叠双边框/描边形成的窄缝伪边界。"""
        return max(self.merge_tolerance * 3.0, self.tolerance * 1.8, 1.8)

    def _collect_band_boundaries(
        self,
        rows: List[float],
        horizontal_lines: List[Line],
        vertical_lines: List[Line],
        collapse_tolerance: float,
        global_cols: List[float],
        text_boxes: List[Any],
    ) -> Dict[int, List[float]]:
        band_boundaries: Dict[int, List[float]] = {}
        if len(rows) < 2 or len(global_cols) < 2:
            return band_boundaries

        candidate_boundaries = sorted({round(line.x0, 1) for line in vertical_lines})
        for row_idx in range(len(rows) - 1):
            band_top = rows[row_idx]
            band_bottom = rows[row_idx + 1]
            active_boundaries: List[float] = []

            for boundary_x in candidate_boundaries:
                if self._is_boundary_active_in_band(boundary_x, band_top, band_bottom, horizontal_lines, vertical_lines, text_boxes):
                    active_boundaries.append(boundary_x)
            collapsed = self._merge_close_coordinates(sorted(set(active_boundaries)), collapse_tolerance)
            if len(collapsed) < 2:
                band_boundaries[row_idx] = [global_cols[0], global_cols[-1]]
            else:
                band_boundaries[row_idx] = collapsed

        return self._close_small_boundary_gaps(
            rows,
            band_boundaries,
            candidate_boundaries,
            horizontal_lines,
            vertical_lines,
            text_boxes,
            collapse_tolerance,
        )

    def _close_small_boundary_gaps(
        self,
        rows: List[float],
        band_boundaries: Dict[int, List[float]],
        candidate_boundaries: List[float],
        horizontal_lines: List[Line],
        vertical_lines: List[Line],
        text_boxes: List[Any],
        collapse_tolerance: float,
    ) -> Dict[int, List[float]]:
        closed_boundaries: Dict[int, List[float]] = {}
        for row_idx in range(len(rows) - 1):
            boundaries = list(band_boundaries.get(row_idx, []))

            if 0 < row_idx < len(rows) - 2:
                for boundary_x in candidate_boundaries[1:-1]:
                    if self._is_boundary_present(boundary_x, boundaries):
                        continue
                    if not self._should_close_small_boundary_gap(
                        boundary_x,
                        row_idx,
                        rows,
                        band_boundaries,
                        horizontal_lines,
                        vertical_lines,
                        text_boxes,
                    ):
                        continue
                    boundaries.append(boundary_x)

            collapsed = self._merge_close_coordinates(sorted(set(boundaries)), collapse_tolerance)
            closed_boundaries[row_idx] = collapsed

        return closed_boundaries

    def _candidate_vertical_lines(self, boundary_x: float, vertical_lines: List[Line]) -> List[Line]:
        return [line for line in vertical_lines if abs(line.x0 - boundary_x) <= self.tolerance]

    def _has_boundary_coverage(self, boundary_x: float, band_top: float, band_bottom: float, vertical_lines: List[Line]) -> bool:
        band_mid = (band_top + band_bottom) / 2
        return any(line.y0 <= band_mid + self.tolerance and line.y1 >= band_mid - self.tolerance for line in self._candidate_vertical_lines(boundary_x, vertical_lines))

    def _has_boundary_anchor_intersections(
        self,
        boundary_x: float,
        band_top: float,
        band_bottom: float,
        horizontal_lines: List[Line],
        vertical_lines: List[Line],
    ) -> bool:
        candidate_lines = self._candidate_vertical_lines(boundary_x, vertical_lines)
        if not candidate_lines:
            return False

        top_anchor = any(abs(line.y0 - band_top) <= self.tolerance or abs(line.y1 - band_top) <= self.tolerance for line in candidate_lines) and self._has_horizontal_line_at(
            horizontal_lines, band_top, boundary_x, boundary_x
        )
        bottom_anchor = any(abs(line.y0 - band_bottom) <= self.tolerance or abs(line.y1 - band_bottom) <= self.tolerance for line in candidate_lines) and self._has_horizontal_line_at(
            horizontal_lines, band_bottom, boundary_x, boundary_x
        )
        return top_anchor and bottom_anchor

    def _has_boundary_text_conflict(self, boundary_x: float, band_top: float, band_bottom: float, text_boxes: List[Any]) -> bool:
        if not text_boxes:
            return False

        synthetic_boundary = Line(x0=boundary_x, y0=band_top, x1=boundary_x, y1=band_bottom, is_horizontal=False)
        for text_box in text_boxes:
            if text_box.y1 < band_top - self.tolerance or text_box.y0 > band_bottom + self.tolerance:
                continue
            if self._is_strong_vertical_crossing(text_box, synthetic_boundary):
                return True
        return False

    def _is_boundary_present(self, boundary_x: float, boundaries: List[float]) -> bool:
        return any(abs(boundary_x - active_boundary) <= self.tolerance for active_boundary in boundaries)

    def _boundary_gap_height(self, boundary_x: float, band_top: float, band_bottom: float, vertical_lines: List[Line]) -> Optional[float]:
        candidate_lines = self._candidate_vertical_lines(boundary_x, vertical_lines)
        if not candidate_lines:
            return None

        upper_end = max(
            (line.y1 for line in candidate_lines if line.y0 <= band_top + self.tolerance and line.y1 >= band_top - self.tolerance),
            default=None,
        )
        lower_start = min(
            (line.y0 for line in candidate_lines if line.y1 >= band_bottom - self.tolerance and line.y0 <= band_bottom + self.tolerance),
            default=None,
        )
        if upper_end is None or lower_start is None:
            return None
        return max(0.0, lower_start - upper_end)

    def _should_close_small_boundary_gap(
        self,
        boundary_x: float,
        row_idx: int,
        rows: List[float],
        band_boundaries: Dict[int, List[float]],
        horizontal_lines: List[Line],
        vertical_lines: List[Line],
        text_boxes: List[Any],
    ) -> bool:
        prev_boundaries = band_boundaries.get(row_idx - 1, [])
        next_boundaries = band_boundaries.get(row_idx + 1, [])
        if not self._is_boundary_present(boundary_x, prev_boundaries):
            return False
        if not self._is_boundary_present(boundary_x, next_boundaries):
            return False

        band_top = rows[row_idx]
        band_bottom = rows[row_idx + 1]
        gap_height = self._boundary_gap_height(boundary_x, band_top, band_bottom, vertical_lines)
        if gap_height is None:
            return False

        prev_height = rows[row_idx] - rows[row_idx - 1]
        next_height = rows[row_idx + 2] - rows[row_idx + 1]
        gap_limit = min(12.0, min(prev_height, next_height) * 0.5)
        if gap_height > gap_limit:
            return False

        if not self._has_boundary_anchor_intersections(boundary_x, band_top, band_bottom, horizontal_lines, vertical_lines):
            return False
        if self._has_boundary_text_conflict(boundary_x, band_top, band_bottom, text_boxes):
            return False
        return True

    def _is_boundary_active_in_band(
        self,
        boundary_x: float,
        band_top: float,
        band_bottom: float,
        horizontal_lines: List[Line],
        vertical_lines: List[Line],
        text_boxes: List[Any],
    ) -> bool:
        if not self._candidate_vertical_lines(boundary_x, vertical_lines):
            return False

        # Step 3 only makes activation explicit; step 4 will use anchor/text
        # constraints to repair small gaps without changing the baseline topology here.
        return self._has_boundary_coverage(boundary_x, band_top, band_bottom, vertical_lines)

    def _build_band_diagnostics(
        self,
        rows: List[float],
        global_cols: List[float],
        band_boundaries: Dict[int, List[float]],
    ) -> List[Dict[str, Any]]:
        diagnostics: List[Dict[str, Any]] = []
        if len(rows) < 2 or len(global_cols) < 2:
            return diagnostics

        interior_boundaries = global_cols[1:-1]
        for row_idx in range(len(rows) - 1):
            boundaries = band_boundaries.get(row_idx) or [global_cols[0], global_cols[-1]]
            missing_boundaries = [boundary for boundary in interior_boundaries if all(abs(boundary - active_boundary) > self.tolerance for active_boundary in boundaries)]
            diagnostics.append(
                {
                    "row_idx": row_idx,
                    "top": rows[row_idx],
                    "bottom": rows[row_idx + 1],
                    "boundaries": boundaries,
                    "missing_global_boundaries": missing_boundaries,
                }
            )

        return diagnostics

    def _row_band_preview(self, table_grid: TableGrid, row_idx: int, limit: int = 3) -> str:
        previews: List[str] = []
        for cell in table_grid.cells:
            if not (cell.row_start <= row_idx <= cell.row_end) or not cell.content:
                continue

            first_line = cell.content.splitlines()[0].strip()
            if not first_line or first_line in previews:
                continue

            previews.append(first_line[:40])
            if len(previews) >= limit:
                break

        return " | ".join(previews)

    def _emit_band_diagnostics(self, table_grid: TableGrid) -> None:
        for diagnostic in self._last_band_diagnostics:
            row_idx = int(diagnostic["row_idx"])
            missing_boundaries = diagnostic["missing_global_boundaries"] or "-"
            preview = self._row_band_preview(table_grid, row_idx) or "-"
            logger.debug(
                "row band[%d]: y=[%.1f, %.1f] boundaries=%s missing_global=%s preview=%s",
                row_idx,
                diagnostic["top"],
                diagnostic["bottom"],
                diagnostic["boundaries"],
                missing_boundaries,
                preview,
            )

    def _analyze_cells(
        self,
        horizontal_lines: List[Line],
        rows: List[float],
        cols: List[float],
        band_boundaries: Dict[int, List[float]],
    ) -> List[CellRegion]:
        """按 row band 局部列拓扑构建单元格，并做受限纵向合并。"""
        if len(rows) < 2 or len(cols) < 2:
            return []

        row_cells: Dict[int, List[CellRegion]] = {}
        for row_idx in range(len(rows) - 1):
            boundaries = band_boundaries.get(row_idx) or [cols[0], cols[-1]]
            boundary_indexes = sorted({self._nearest_boundary_index(cols, x) for x in boundaries})

            segments: List[CellRegion] = []
            for start_idx, end_idx in zip(boundary_indexes, boundary_indexes[1:]):
                if end_idx <= start_idx:
                    continue
                segments.append(CellRegion(row_idx, row_idx, start_idx, end_idx - 1))

            if not segments:
                segments.append(CellRegion(row_idx, row_idx, 0, len(cols) - 2))

            row_cells[row_idx] = segments

        # 仅在上下行存在相同列跨度且中间无完整水平边界时才做纵向合并
        row_span_map: Dict[int, Set[Tuple[int, int]]] = {row_idx: {(cell.col_start, cell.col_end) for cell in cells} for row_idx, cells in row_cells.items()}
        consumed: Set[Tuple[int, int, int]] = set()
        merged_cells: List[CellRegion] = []

        for row_idx in range(len(rows) - 1):
            for cell in row_cells.get(row_idx, []):
                key = (row_idx, cell.col_start, cell.col_end)
                if key in consumed:
                    continue

                consumed.add(key)
                end_row = row_idx
                x_start = cols[cell.col_start]
                x_end = cols[cell.col_end + 1]

                while end_row + 1 < len(rows) - 1:
                    next_key = (end_row + 1, cell.col_start, cell.col_end)
                    if (cell.col_start, cell.col_end) not in row_span_map.get(end_row + 1, set()):
                        break

                    boundary_y = rows[end_row + 1]
                    if self._has_horizontal_line_at(horizontal_lines, boundary_y, x_start, x_end):
                        break

                    consumed.add(next_key)
                    end_row += 1

                merged_cells.append(CellRegion(row_idx, end_row, cell.col_start, cell.col_end))

        merged_cells.sort(key=lambda item: (item.row_start, item.col_start, item.row_end, item.col_end))
        return merged_cells

    def _nearest_boundary_index(self, boundaries: List[float], target: float) -> int:
        closest_index = 0
        closest_distance = float("inf")
        for idx, value in enumerate(boundaries):
            distance = abs(value - target)
            if distance < closest_distance:
                closest_distance = distance
                closest_index = idx
        return closest_index

    def _find_cell_boundaries(
        self,
        horizontal_lines: List[Line],
        vertical_lines: List[Line],
        rows: List[float],
        cols: List[float],
        start_row: int,
        start_col: int,
        processed_cells: Set,
    ) -> Optional[CellRegion]:
        """增强版边界查找，更准确地处理合并单元格

        Args:
            horizontal_lines: 水平线列表
            vertical_lines: 垂直线列表
            rows: 行坐标列表
            cols: 列坐标列表
            start_row: 起始行索引
            start_col: 起始列索引
            processed_cells: 已处理的单元格集合

        Returns:
            Optional[CellRegion]: 单元格区域，如果无效则返回None
        """
        # 向右扩展，找到右边界
        end_col = start_col
        while end_col + 1 < len(cols) - 1:
            # 检查是否有垂直线阻挡
            if self._has_vertical_line_at(vertical_lines, cols[end_col + 1], rows[start_row], rows[start_row + 1]):
                break
            # 检查下一个位置是否已被处理
            if (start_row, end_col + 1) in processed_cells:
                break
            end_col += 1

        # 向下扩展，找到下边界
        end_row = start_row
        while end_row + 1 < len(rows) - 1:
            # 检查这一行的所有列是否都可以扩展
            can_expand = True
            for col in range(start_col, end_col + 1):
                # 检查是否有水平线阻挡
                if self._has_horizontal_line_at(horizontal_lines, rows[end_row + 1], cols[col], cols[col + 1]):
                    can_expand = False
                    break
                # 检查下一个位置是否已被处理
                if (end_row + 1, col) in processed_cells:
                    can_expand = False
                    break

            if not can_expand:
                break
            end_row += 1

        return CellRegion(start_row, end_row, start_col, end_col)

    def _has_vertical_line_at(self, vertical_lines: List[Line], x: float, y_start: float, y_end: float) -> bool:
        """增强版垂直线检查"""
        for line in vertical_lines:
            if (
                abs(line.x0 - x) < self.tolerance  # X坐标匹配
                and line.y0 <= min(y_start, y_end) + self.tolerance  # Y范围重叠
                and line.y1 >= max(y_start, y_end) - self.tolerance
            ):
                return True
        return False

    def _has_horizontal_line_at(self, horizontal_lines: List[Line], y: float, x_start: float, x_end: float) -> bool:
        assert x_start <= x_end, "x_start should be less than or equal to x_end"

        for line in horizontal_lines:
            if (
                abs(line.y0 - y) < self.tolerance  # Y坐标匹配
                and line.x0 <= min(x_start, x_end) + self.tolerance  # X范围重叠
                and line.x1 >= max(x_start, x_end) - self.tolerance
            ):
                return True
        return False

    def _is_spanbox_overlap_vertical_line(self, spanbox: Any, vertical_lines: List[Line], min_cross_depth: float = 3.0) -> Optional[Line]:
        """
        检测 spanbox 是否真正与垂直线重叠（穿越）

        Args:
            spanbox: 文本框对象，需要有 x0, y0, x1, y1 属性
            vertical_lines: 垂直线列表
            min_cross_depth: 最小穿越深度阈值（像素）

        Returns:
            Optional[Line]: 如果存在真正重叠，返回穿越的垂直线；否则返回 None
        """
        text_left = spanbox.x0
        text_right = spanbox.x1

        for v_line in vertical_lines:
            if self._is_strong_vertical_crossing(spanbox, v_line, min_cross_depth):
                line_x = v_line.x0
                left_part = line_x - text_left
                right_part = text_right - line_x
                cross_depth = min(left_part, right_part)
                logger.debug(f"发现真正重叠: 文本 '{getattr(spanbox, 'text', 'unknown')}' 被垂直线 x={line_x:.1f} 穿越，深度={cross_depth:.1f}")
                return v_line

        return None

    def _is_strong_vertical_crossing(self, spanbox: Any, v_line: Line, min_cross_depth: float = 3.0) -> bool:
        text_left = spanbox.x0
        text_right = spanbox.x1
        text_top = spanbox.y0
        text_bottom = spanbox.y1

        line_x = v_line.x0
        line_top = v_line.y0
        line_bottom = v_line.y1

        if line_bottom < text_top or line_top > text_bottom:
            return False
        if not (text_left < line_x < text_right):
            return False

        text_height = max(1.0, text_bottom - text_top)
        overlap_height = min(text_bottom, line_bottom) - max(text_top, line_top)
        if overlap_height < max(2.0, text_height * 0.7):
            return False

        text_width = max(1.0, text_right - text_left)
        left_part = line_x - text_left
        right_part = text_right - line_x
        cross_depth = min(left_part, right_part)
        required_depth = max(min_cross_depth, text_width * 0.22)
        return bool(cross_depth >= required_depth)

    def _split_spanbox_against_vertical_lines(self, spanbox: Any, vertical_lines: List[Line]) -> List[Any]:
        """
        将被垂直线穿越的 spanbox 分割为多个部分

        Args:
            spanbox: 被穿越的文本框对象
            vertical_lines: 垂直线列表

        Returns:
            List[Any]: 分割后的 spanbox 列表
        """
        # 检查是否真的有重叠
        crossing_line = self._is_spanbox_overlap_vertical_line(spanbox, vertical_lines)
        if not crossing_line:
            # 没有真正重叠，返回原始 spanbox
            return [spanbox]

        # 找到所有穿越该 spanbox 的垂直线
        crossing_lines = []
        text_left = spanbox.x0
        text_right = spanbox.x1

        for v_line in vertical_lines:
            if self._is_strong_vertical_crossing(spanbox, v_line):
                crossing_lines.append(v_line.x0)

        if not crossing_lines:
            return [spanbox]

        # 按 x 坐标排序穿越线
        crossing_lines.sort()

        # 创建分割后的 spanbox 列表
        split_spanboxes = []
        current_left = text_left

        for line_x in crossing_lines:
            if current_left < line_x:
                # 创建左侧部分
                try:
                    # 尝试创建与原始 spanbox 相同类型的对象
                    left_spanbox = type(spanbox)(
                        text=self._extract_text_portion(spanbox, current_left, line_x),
                        x0=current_left,
                        y0=spanbox.y0,
                        x1=line_x,
                        y1=spanbox.y1,
                        page=getattr(spanbox, "page", 0),
                        font_name=getattr(spanbox, "font_name", ""),
                        font_size=getattr(spanbox, "font_size", 0),
                        font_flags=getattr(spanbox, "font_flags", 0),
                    )
                    split_spanboxes.append(left_spanbox)
                    logger.debug(f"创建左侧分割: x=[{current_left:.1f}, {line_x:.1f}] text='{left_spanbox.text}'")
                except Exception as e:
                    logger.warning(f"创建分割 spanbox 失败: {e}")
                    # 如果创建失败，返回原始 spanbox
                    return [spanbox]

            current_left = line_x

        # 创建最右侧部分
        if current_left < text_right:
            try:
                right_spanbox = type(spanbox)(
                    text=self._extract_text_portion(spanbox, current_left, text_right),
                    x0=current_left,
                    y0=spanbox.y0,
                    x1=text_right,
                    y1=spanbox.y1,
                    page=getattr(spanbox, "page", 0),
                    font_name=getattr(spanbox, "font_name", ""),
                    font_size=getattr(spanbox, "font_size", 0),
                    font_flags=getattr(spanbox, "font_flags", 0),
                )
                split_spanboxes.append(right_spanbox)
                logger.debug(f"创建右侧分割: x=[{current_left:.1f}, {text_right:.1f}] text='{right_spanbox.text}'")
            except Exception as e:
                logger.warning(f"创建右侧分割 spanbox 失败: {e}")
                return [spanbox]

        logger.debug(f"成功分割 spanbox: 原始文本 '{getattr(spanbox, 'text', '')}' 分割为 {len(split_spanboxes)} 部分")
        return split_spanboxes

    def _extract_text_portion(self, spanbox: Any, x_start: float, x_end: float) -> str:
        """
        根据 x 坐标范围提取文本的对应部分

        Args:
            spanbox: 原始文本框
            x_start: 起始 x 坐标
            x_end: 结束 x 坐标

        Returns:
            str: 提取的文本部分
        """
        original_text = getattr(spanbox, "text", "")
        if not original_text:
            return ""

        # 计算文本在该范围内的比例
        text_width = spanbox.x1 - spanbox.x0
        if text_width <= 0:
            return original_text

        # 计算起始和结束位置在文本中的比例
        start_ratio = max(0, (x_start - spanbox.x0) / text_width)
        end_ratio = min(1, (x_end - spanbox.x0) / text_width)

        logger.debug(
            f"提取文本部分: 原文='{original_text}', x_start={x_start:.1f}, x_end={x_end:.1f}, "
            f"spanbox范围=[{spanbox.x0:.1f}, {spanbox.x1:.1f}], "
            f"start_ratio={start_ratio:.3f}, end_ratio={end_ratio:.3f}"
        )

        # 改进的文本分割算法
        text_len = len(original_text)

        # 使用更精确的四舍五入而不是简单截断
        start_idx = round(start_ratio * text_len)
        end_idx = round(end_ratio * text_len)

        # 确保索引有效且有意义
        start_idx = max(0, min(start_idx, text_len))
        end_idx = max(start_idx, min(end_idx, text_len))

        if start_ratio > 0:
            start_idx = self._snap_text_index_to_whitespace_gap(original_text, start_idx)
        if end_ratio < 1:
            end_idx = max(start_idx, self._snap_text_index_to_whitespace_gap(original_text, end_idx))

        # 提取文本（保留原始空格，稍后智能处理）
        if start_idx >= end_idx:
            # 如果范围无效，尝试基于位置的智能分割
            extracted_text = self._smart_text_split(original_text, start_ratio, end_ratio)
        else:
            extracted_text = original_text[start_idx:end_idx]

        # 智能清理：只移除前导和尾随空格，但保留内部空格结构
        extracted_text = extracted_text.strip()

        logger.debug(f"文本提取结果: 索引范围[{start_idx}:{end_idx}] -> '{extracted_text}'")

        # 如果提取的文本仍为空，返回空字符串而不是回退到原文的一部分
        # 这避免了重复问题
        if not extracted_text:
            logger.debug("提取文本为空，返回空字符串")
            return ""

        return extracted_text

    def _snap_text_index_to_whitespace_gap(self, text: str, target_idx: int, max_shift: int = 8) -> int:
        if not text:
            return target_idx

        best_idx = target_idx
        best_score: Optional[Tuple[int, int]] = None
        run_start: Optional[int] = None

        for idx, char in enumerate(text):
            is_space = char.isspace() or char == "\u3000"
            if is_space and run_start is None:
                run_start = idx
            elif not is_space and run_start is not None:
                run_end = idx
                snapped_idx = run_end
                distance = abs(snapped_idx - target_idx)
                if distance <= max_shift:
                    run_length = run_end - run_start
                    score = (-run_length, distance)
                    if best_score is None or score < best_score:
                        best_score = score
                        best_idx = snapped_idx
                run_start = None

        if run_start is not None:
            run_end = len(text)
            snapped_idx = run_end
            distance = abs(snapped_idx - target_idx)
            if distance <= max_shift:
                run_length = run_end - run_start
                score = (-run_length, distance)
                if best_score is None or score < best_score:
                    best_idx = snapped_idx

        return best_idx

    def _has_wide_whitespace_gap_near_ratio(
        self,
        text: str,
        split_ratio: float,
        min_run_length: int = 2,
        max_shift: int = 8,
    ) -> bool:
        if not text:
            return False

        target_idx = round(max(0.0, min(1.0, split_ratio)) * len(text))
        run_start: Optional[int] = None

        for idx, char in enumerate(text):
            is_space = char.isspace() or char == "\u3000"
            if is_space and run_start is None:
                run_start = idx
            elif not is_space and run_start is not None:
                run_end = idx
                if run_end - run_start >= min_run_length and abs(run_end - target_idx) <= max_shift:
                    return True
                run_start = None

        if run_start is not None:
            run_end = len(text)
            if run_end - run_start >= min_run_length and abs(run_end - target_idx) <= max_shift:
                return True

        return False

    def _smart_text_split(self, text: str, start_ratio: float, end_ratio: float) -> str:
        """
        智能文本分割，考虑词汇边界和字符类型

        Args:
            text: 原始文本
            start_ratio: 起始比例
            end_ratio: 结束比例

        Returns:
            str: 分割后的文本
        """
        if not text or start_ratio >= end_ratio:
            return ""

        text_len = len(text)

        # 尝试在词汇边界分割
        words = text.split()
        if len(words) > 1:
            # 计算每个词汇的大致位置
            total_chars = sum(len(word) for word in words) + len(words) - 1  # 包括空格

            current_pos = 0
            start_word_idx = 0
            end_word_idx = len(words)

            for i, word in enumerate(words):
                word_start_ratio = current_pos / total_chars
                word_end_ratio = (current_pos + len(word)) / total_chars

                # 找到起始词汇
                if word_start_ratio <= start_ratio <= word_end_ratio:
                    start_word_idx = i

                # 找到结束词汇
                if word_start_ratio <= end_ratio <= word_end_ratio:
                    end_word_idx = i + 1
                    break

                current_pos += len(word) + 1  # 包括空格

            if start_word_idx < end_word_idx <= len(words):
                result = " ".join(words[start_word_idx:end_word_idx])
                logger.debug(f"智能分割(词汇边界): 词汇{start_word_idx}-{end_word_idx} -> '{result}'")
                return result

        # 回退到字符级分割
        start_idx = round(start_ratio * text_len)
        end_idx = round(end_ratio * text_len)
        start_idx = max(0, min(start_idx, text_len))
        end_idx = max(start_idx, min(end_idx, text_len))

        result = text[start_idx:end_idx] if start_idx < end_idx else ""
        logger.debug(f"智能分割(字符级): 字符{start_idx}-{end_idx} -> '{result}'")
        return result

    def _cell_bbox(self, table_grid: TableGrid, cell_region: CellRegion) -> Optional[Tuple[float, float, float, float]]:
        if cell_region.row_start >= len(table_grid.rows) - 1 or cell_region.col_start >= len(table_grid.cols) - 1:
            return None

        top = table_grid.rows[cell_region.row_start]
        bottom = table_grid.rows[cell_region.row_end + 1] if cell_region.row_end + 1 < len(table_grid.rows) else table_grid.rows[-1]
        left = table_grid.cols[cell_region.col_start]
        right = table_grid.cols[cell_region.col_end + 1] if cell_region.col_end + 1 < len(table_grid.cols) else table_grid.cols[-1]
        return (left, top, right, bottom)

    def _overlap_area(self, left: float, top: float, right: float, bottom: float, text_box: Any) -> float:
        overlap_left = max(left, text_box.x0)
        overlap_top = max(top, text_box.y0)
        overlap_right = min(right, text_box.x1)
        overlap_bottom = min(bottom, text_box.y1)
        overlap_width = max(0.0, overlap_right - overlap_left)
        overlap_height = max(0.0, overlap_bottom - overlap_top)
        return float(overlap_width * overlap_height)

    def _text_box_area(self, text_box: Any) -> float:
        return float(max(0.0, text_box.x1 - text_box.x0) * max(0.0, text_box.y1 - text_box.y0))

    def _line_text_overlap_height(self, line: Line, text_box: Any) -> float:
        return float(max(0.0, min(line.y1, text_box.y1) - max(line.y0, text_box.y0)))

    def _build_text_assignment_index(self, table_grid: TableGrid) -> _TextAssignmentIndex:
        cell_bboxes: Dict[int, Tuple[float, float, float, float]] = {}
        bucket_cell_indexes: Dict[Tuple[int, int], Set[int]] = {}

        max_row_band = max(0, len(table_grid.rows) - 2)
        max_col_band = max(0, len(table_grid.cols) - 2)
        for cell_idx, cell_region in enumerate(table_grid.cells):
            bbox = self._cell_bbox(table_grid, cell_region)
            if bbox is None:
                continue

            cell_bboxes[cell_idx] = bbox
            row_start = max(0, min(cell_region.row_start, max_row_band))
            row_end = max(0, min(cell_region.row_end, max_row_band))
            col_start = max(0, min(cell_region.col_start, max_col_band))
            col_end = max(0, min(cell_region.col_end, max_col_band))
            for row_idx in range(row_start, row_end + 1):
                for col_idx in range(col_start, col_end + 1):
                    bucket_cell_indexes.setdefault((row_idx, col_idx), set()).add(cell_idx)

        active_boundary_lines_by_row = {row_idx: tuple(lines) for row_idx, lines in self._active_boundary_lines_by_row(table_grid).items()}
        return _TextAssignmentIndex(
            cell_bboxes=cell_bboxes,
            bucket_cell_indexes={key: tuple(sorted(indexes)) for key, indexes in bucket_cell_indexes.items()},
            active_boundary_lines_by_row=active_boundary_lines_by_row,
            all_cell_indexes=tuple(sorted(cell_bboxes)),
        )

    @staticmethod
    def _cell_region_key(cell_region: CellRegion) -> Tuple[int, int, int, int]:
        return (
            cell_region.row_start,
            cell_region.row_end,
            cell_region.col_start,
            cell_region.col_end,
        )

    def _occupied_bands(self, table_grid: TableGrid) -> Set[Tuple[int, int]]:
        occupied: Set[Tuple[int, int]] = set()
        max_row_band = max(0, len(table_grid.rows) - 2)
        max_col_band = max(0, len(table_grid.cols) - 2)
        for cell_region in table_grid.cells:
            row_start = max(0, min(cell_region.row_start, max_row_band))
            row_end = max(0, min(cell_region.row_end, max_row_band))
            col_start = max(0, min(cell_region.col_start, max_col_band))
            col_end = max(0, min(cell_region.col_end, max_col_band))
            for row_idx in range(row_start, row_end + 1):
                for col_idx in range(col_start, col_end + 1):
                    occupied.add((row_idx, col_idx))
        return occupied

    def _rect_is_empty(
        self,
        occupied_bands: Set[Tuple[int, int]],
        row_start: int,
        row_end: int,
        col_start: int,
        col_end: int,
    ) -> bool:
        return all((row_idx, col_idx) not in occupied_bands for row_idx in range(row_start, row_end + 1) for col_idx in range(col_start, col_end + 1))

    def _expand_empty_col_range(
        self,
        occupied_bands: Set[Tuple[int, int]],
        row_start: int,
        row_end: int,
        col_start: int,
        col_end: int,
        max_col_band: int,
    ) -> Tuple[int, int]:
        expanded_start = col_start
        expanded_end = col_end

        while expanded_start > 0 and self._rect_is_empty(
            occupied_bands,
            row_start,
            row_end,
            expanded_start - 1,
            expanded_start - 1,
        ):
            expanded_start -= 1

        while expanded_end < max_col_band and self._rect_is_empty(
            occupied_bands,
            row_start,
            row_end,
            expanded_end + 1,
            expanded_end + 1,
        ):
            expanded_end += 1

        return expanded_start, expanded_end

    def _has_vertical_neighbor(
        self,
        occupied_bands: Set[Tuple[int, int]],
        row_start: int,
        row_end: int,
        col_start: int,
        col_end: int,
        max_row_band: int,
    ) -> bool:
        if row_start > 0 and any((row_start - 1, col_idx) in occupied_bands for col_idx in range(col_start, col_end + 1)):
            return True
        if row_end < max_row_band and any((row_end + 1, col_idx) in occupied_bands for col_idx in range(col_start, col_end + 1)):
            return True
        return False

    def _has_horizontal_neighbor(
        self,
        occupied_bands: Set[Tuple[int, int]],
        row_start: int,
        row_end: int,
        col_start: int,
        col_end: int,
        max_col_band: int,
    ) -> bool:
        if col_start > 0 and any((row_idx, col_start - 1) in occupied_bands for row_idx in range(row_start, row_end + 1)):
            return True
        if col_end < max_col_band and any((row_idx, col_end + 1) in occupied_bands for row_idx in range(row_start, row_end + 1)):
            return True
        return False

    def _build_text_assignment_pass(
        self,
        table_grid: TableGrid,
        text_boxes: List[Any],
        *,
        word_boxes: Optional[List[Any]] = None,
        character_boxes: Optional[List[Any]] = None,
    ) -> _TextAssignmentPass:
        assignment_index = self._build_text_assignment_index(table_grid)
        processed_text_boxes, split_span_count = self._split_text_boxes_by_active_boundaries(
            table_grid,
            text_boxes,
            assignment_index=assignment_index,
        )

        cell_text_map: Dict[int, List[Any]] = {cell_idx: [] for cell_idx in assignment_index.all_cell_indexes}
        matched_text_indexes: Set[int] = set()
        unmatched_indexes: List[int] = []
        unmatched_spans: List[Any] = []
        cross_cell_fallback_diagnostics = _CrossCellFallbackDiagnostics()
        word_boxes_by_page = self._text_boxes_by_page(word_boxes or [])
        character_boxes_by_page = self._text_boxes_by_page(character_boxes or [])

        for text_idx, text_box in enumerate(processed_text_boxes):
            extent_candidate_indexes = self._extent_candidate_cell_indexes_for_text_box(
                table_grid,
                text_box,
                assignment_index,
            )
            assigned_cell_idx, _ = self._select_cell_for_text_box(
                table_grid,
                text_box,
                assignment_index=assignment_index,
            )
            fallback_result = self._split_cross_cell_span_via_words(
                table_grid,
                text_box,
                assigned_cell_idx=assigned_cell_idx,
                candidate_indexes=extent_candidate_indexes,
                assignment_index=assignment_index,
                word_boxes_by_page=word_boxes_by_page,
                character_boxes_by_page=character_boxes_by_page,
            )
            if fallback_result is not None:
                for cell_idx, fragment in fallback_result.assignments:
                    cell_text_map[cell_idx].append(fragment)
                matched_text_indexes.add(text_idx)
                cross_cell_fallback_diagnostics.word_fallback_span_count += 1
                cross_cell_fallback_diagnostics.word_fallback_fragment_count += len(fallback_result.assignments)
                cross_cell_fallback_diagnostics.char_fallback_word_count += fallback_result.char_fallback_word_count
                cross_cell_fallback_diagnostics.char_fallback_fragment_count += fallback_result.char_fallback_fragment_count
                continue
            if self._should_count_cross_cell_rejection(
                table_grid,
                text_box,
                candidate_indexes=extent_candidate_indexes,
                assignment_index=assignment_index,
                word_boxes_by_page=word_boxes_by_page,
            ):
                cross_cell_fallback_diagnostics.cross_cell_fallback_rejected_count += 1

            if assigned_cell_idx is None:
                unmatched_indexes.append(text_idx)
                unmatched_spans.append(text_box)
                continue

            cell_text_map[assigned_cell_idx].append(text_box)
            matched_text_indexes.add(text_idx)

        return _TextAssignmentPass(
            assignment_index=assignment_index,
            processed_text_boxes=processed_text_boxes,
            split_span_count=split_span_count,
            cell_text_map=cell_text_map,
            matched_text_indexes=matched_text_indexes,
            unmatched_indexes=unmatched_indexes,
            unmatched_spans=unmatched_spans,
            cross_cell_fallback_diagnostics=cross_cell_fallback_diagnostics,
        )

    @staticmethod
    def _text_boxes_by_page(text_boxes: List[Any]) -> Dict[int, List[Any]]:
        by_page: Dict[int, List[Any]] = {}
        for box in text_boxes:
            page = int(getattr(box, "page", 0))
            by_page.setdefault(page, []).append(box)
        return by_page

    def _should_count_cross_cell_rejection(
        self,
        table_grid: TableGrid,
        text_box: Any,
        *,
        candidate_indexes: Tuple[int, ...],
        assignment_index: _TextAssignmentIndex,
        word_boxes_by_page: Dict[int, List[Any]],
    ) -> bool:
        options = self._cross_cell_text_split_options
        if not options.enabled:
            return False
        if options.strategy != "words_then_char":
            return False

        text = str(getattr(text_box, "text", "") or "").strip()
        if not text:
            return False
        if len(candidate_indexes) < options.min_candidate_cells:
            return False
        if options.scope == "header_only" and not self._text_box_in_header_band(table_grid, text_box):
            return False
        if options.require_active_boundary and not self._has_active_boundary_crossing(
            table_grid,
            text_box,
            assignment_index=assignment_index,
        ):
            return False
        return bool(self._candidate_words_for_span(text_box, word_boxes_by_page.get(int(getattr(text_box, "page", 0)), [])))

    def _text_box_in_header_band(self, table_grid: TableGrid, text_box: Any) -> bool:
        row_bands = self._band_indexes_for_interval(table_grid.rows, text_box.y0, text_box.y1)
        return bool(row_bands) and row_bands[0] == 0

    def _has_active_boundary_crossing(
        self,
        table_grid: TableGrid,
        text_box: Any,
        *,
        assignment_index: _TextAssignmentIndex,
    ) -> bool:
        active_lines = self._candidate_boundary_lines_for_text_box(
            table_grid,
            text_box,
            assignment_index=assignment_index,
        )
        for line in active_lines:
            if line.y1 < text_box.y0 or line.y0 > text_box.y1:
                continue
            if text_box.x0 < line.x0 < text_box.x1:
                return True
        return False

    def _candidate_words_for_span(self, span_box: Any, page_word_boxes: List[Any]) -> List[Any]:
        if not page_word_boxes:
            return []

        span_center_y = (span_box.y0 + span_box.y1) / 2.0
        span_height = max(1.0, span_box.y1 - span_box.y0)
        line_tolerance = max(2.0, span_height * 0.75)
        candidate_words: List[Any] = []

        for word_box in page_word_boxes:
            word_text = str(getattr(word_box, "text", "") or "").strip()
            if not word_text:
                continue
            center_x = (word_box.x0 + word_box.x1) / 2.0
            center_y = (word_box.y0 + word_box.y1) / 2.0
            if not (span_box.x0 <= center_x <= span_box.x1 and span_box.y0 <= center_y <= span_box.y1):
                continue

            overlap_height = max(0.0, min(span_box.y1, word_box.y1) - max(span_box.y0, word_box.y0))
            word_height = max(1.0, word_box.y1 - word_box.y0)
            if (overlap_height / word_height) < 0.6:
                continue
            if abs(center_y - span_center_y) > line_tolerance:
                continue
            candidate_words.append(word_box)

        candidate_words.sort(key=lambda item: (item.x0, item.y0))
        return candidate_words

    def _split_cross_cell_span_via_words(
        self,
        table_grid: TableGrid,
        span_box: Any,
        *,
        assigned_cell_idx: Optional[int],
        candidate_indexes: Tuple[int, ...],
        assignment_index: _TextAssignmentIndex,
        word_boxes_by_page: Dict[int, List[Any]],
        character_boxes_by_page: Dict[int, List[Any]],
    ) -> Optional[_CrossCellFallbackResult]:
        options = self._cross_cell_text_split_options
        if not options.enabled:
            return None
        if options.strategy != "words_then_char":
            return None

        text = str(getattr(span_box, "text", "") or "").strip()
        if not text:
            return None
        if len(candidate_indexes) < options.min_candidate_cells:
            return None
        if options.scope == "header_only" and not self._text_box_in_header_band(table_grid, span_box):
            return None
        if options.require_active_boundary and not self._has_active_boundary_crossing(
            table_grid,
            span_box,
            assignment_index=assignment_index,
        ):
            return None

        # 已有单个稳定候选 cell（含合并单元格）时，不介入 fallback。
        if assigned_cell_idx is not None and len(set(candidate_indexes)) <= 1:
            return None

        page_word_boxes = word_boxes_by_page.get(int(getattr(span_box, "page", 0)), [])
        candidate_words = self._candidate_words_for_span(span_box, page_word_boxes)
        if not candidate_words:
            return None

        raw_assignments: List[Tuple[int, Any]] = []
        char_fallback_word_count = 0
        char_fallback_fragment_count = 0

        for word_box in candidate_words:
            anchor_candidate_indexes = self._candidate_cell_indexes_for_text_box(
                table_grid,
                word_box,
                assignment_index,
            )

            overlapping_cells = self._overlapping_cell_count(
                table_grid,
                word_box,
                assignment_index=assignment_index,
            )
            needs_char_fallback = overlapping_cells >= options.min_candidate_cells and (
                not options.require_active_boundary or self._has_active_boundary_crossing(table_grid, word_box, assignment_index=assignment_index)
            )
            if needs_char_fallback:
                char_assignments = self._split_word_via_character_boxes(
                    table_grid,
                    span_box=span_box,
                    word_box=word_box,
                    assignment_index=assignment_index,
                    page_character_boxes=character_boxes_by_page.get(int(getattr(span_box, "page", 0)), []),
                )
                if not char_assignments:
                    return None
                raw_assignments.extend(char_assignments)
                char_fallback_word_count += 1
                char_fallback_fragment_count += len(char_assignments)
                continue

            word_cell_idx, _ = self._select_cell_for_text_box(
                table_grid,
                word_box,
                assignment_index=assignment_index,
                candidate_indexes=anchor_candidate_indexes,
            )
            if word_cell_idx is None:
                return None
            raw_assignments.append((word_cell_idx, word_box))

        if not raw_assignments:
            return None

        merged_assignments = self._merge_cross_cell_assignments(
            table_grid,
            span_box,
            raw_assignments,
            min_fragment_overlap_ratio=options.min_fragment_overlap_ratio,
        )
        if not merged_assignments:
            return None

        return _CrossCellFallbackResult(
            assignments=merged_assignments,
            char_fallback_word_count=char_fallback_word_count,
            char_fallback_fragment_count=char_fallback_fragment_count,
        )

    def _split_word_via_character_boxes(
        self,
        table_grid: TableGrid,
        *,
        span_box: Any,
        word_box: Any,
        assignment_index: _TextAssignmentIndex,
        page_character_boxes: List[Any],
    ) -> List[Tuple[int, Any]]:
        if not page_character_boxes:
            return []

        in_word_chars: List[Any] = []
        span_x0 = float(getattr(span_box, "x0", word_box.x0)) - self.tolerance
        span_y0 = float(getattr(span_box, "y0", word_box.y0)) - self.tolerance
        span_x1 = float(getattr(span_box, "x1", word_box.x1)) + self.tolerance
        span_y1 = float(getattr(span_box, "y1", word_box.y1)) + self.tolerance
        for char_box in page_character_boxes:
            center_x = (char_box.x0 + char_box.x1) / 2.0
            center_y = (char_box.y0 + char_box.y1) / 2.0
            if not (word_box.x0 <= center_x <= word_box.x1 and word_box.y0 <= center_y <= word_box.y1):
                continue
            # Keep only chars that belong to the original span fragment. Some
            # PDFs emit a single oversized word box that spans neighboring cells.
            if not (span_x0 <= center_x <= span_x1 and span_y0 <= center_y <= span_y1):
                continue
            in_word_chars.append(char_box)
        if not in_word_chars:
            return []

        char_groups_by_cell: Dict[int, List[Any]] = defaultdict(list)
        for char_box in in_word_chars:
            cell_idx, _ = self._select_cell_for_text_box(
                table_grid,
                char_box,
                assignment_index=assignment_index,
            )
            if cell_idx is None:
                return []
            char_groups_by_cell[cell_idx].append(char_box)

        if len(char_groups_by_cell) < 2:
            return []

        char_assignments: List[Tuple[int, Any]] = []
        for cell_idx, chars in char_groups_by_cell.items():
            self._sort_character_boxes_for_reconstruction(chars)
            text = "".join(str(getattr(item, "char", "") or "").strip() for item in chars).strip()
            if not text:
                continue
            fragment = self._build_fragment_box(
                span_box,
                text=text,
                x0=min(char.x0 for char in chars),
                y0=min(char.y0 for char in chars),
                x1=max(char.x1 for char in chars),
                y1=max(char.y1 for char in chars),
            )
            char_assignments.append((cell_idx, fragment))

        if len(char_assignments) < 2:
            return []
        return char_assignments

    @staticmethod
    def _sort_character_boxes_for_reconstruction(chars: List[Any]) -> None:
        if len(chars) <= 1:
            return
        min_y = min(item.y0 for item in chars)
        max_y = max(item.y0 for item in chars)
        y_span = max_y - min_y
        if y_span <= 0.5:
            chars.sort(key=lambda item: item.x0)
            return
        bucket_size = y_span / 10.0
        if bucket_size <= 0.0:
            chars.sort(key=lambda item: item.x0)
            return
        chars.sort(key=lambda item: (round((item.y0 - min_y) / bucket_size), item.x0))

    def _merge_cross_cell_assignments(
        self,
        table_grid: TableGrid,
        span_box: Any,
        assignments: List[Tuple[int, Any]],
        *,
        min_fragment_overlap_ratio: float,
    ) -> List[Tuple[int, Any]]:
        per_cell_fragments: Dict[int, List[Any]] = defaultdict(list)
        for cell_idx, fragment in assignments:
            per_cell_fragments[cell_idx].append(fragment)

        if len(per_cell_fragments) < 2:
            return []

        merged: List[Tuple[int, Any]] = []
        for cell_idx, fragments in per_cell_fragments.items():
            text = self._compose_cell_content(fragments)
            if not text:
                continue
            merged_fragment = self._build_fragment_box(
                span_box,
                text=text,
                x0=min(fragment.x0 for fragment in fragments),
                y0=min(fragment.y0 for fragment in fragments),
                x1=max(fragment.x1 for fragment in fragments),
                y1=max(fragment.y1 for fragment in fragments),
            )
            merged.append((cell_idx, merged_fragment))

        if len(merged) < 2:
            return []

        merged.sort(key=lambda item: (item[1].x0, item[1].y0))
        if not self._has_strict_contiguous_cell_progression(table_grid, [cell_idx for cell_idx, _ in merged]):
            return []

        for cell_idx, fragment in merged:
            cell_bbox = self._cell_bbox(table_grid, table_grid.cells[cell_idx])
            if cell_bbox is None:
                return []
            fragment_area = self._text_box_area(fragment)
            if fragment_area <= 0.0:
                return []
            overlap_ratio = self._overlap_area(*cell_bbox, fragment) / fragment_area
            if overlap_ratio < min_fragment_overlap_ratio:
                return []

        source_chars = self._normalize_compact_text(str(getattr(span_box, "text", "") or ""))
        merged_chars = self._normalize_compact_text("".join(str(getattr(fragment, "text", "") or "") for _, fragment in merged))
        if not source_chars or not merged_chars:
            return []
        max_diff = max(1, math.ceil(len(source_chars) * 0.1))
        if abs(len(source_chars) - len(merged_chars)) > max_diff:
            return []
        return merged

    def _has_strict_contiguous_cell_progression(self, table_grid: TableGrid, cell_indexes: List[int]) -> bool:
        if len(cell_indexes) < 2:
            return False
        previous_col_end: Optional[int] = None
        for cell_idx in cell_indexes:
            cell = table_grid.cells[cell_idx]
            if previous_col_end is not None:
                if cell.col_start <= previous_col_end:
                    return False
                if cell.col_start > previous_col_end + 1:
                    return False
            previous_col_end = cell.col_end
        return True

    @staticmethod
    def _normalize_compact_text(text: str) -> str:
        return re.sub(r"\s+", "", str(text or ""))

    def _build_fragment_box(
        self,
        source_box: Any,
        *,
        text: str,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ) -> Any:
        if hasattr(source_box, "_fields"):
            values: list[object] = []
            for field_name in getattr(source_box, "_fields"):
                if field_name == "text":
                    values.append(text)
                elif field_name == "x0":
                    values.append(x0)
                elif field_name == "y0":
                    values.append(y0)
                elif field_name == "x1":
                    values.append(x1)
                elif field_name == "y1":
                    values.append(y1)
                else:
                    values.append(getattr(source_box, field_name, None))
            try:
                return type(source_box)(*values)
            except Exception:
                pass

        return SimpleNamespace(
            text=text,
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
            page=getattr(source_box, "page", 0),
        )

    def _create_synthetic_cells_for_unmatched_spans(
        self,
        table_grid: TableGrid,
        unmatched_spans: List[Any],
        assignment_index: _TextAssignmentIndex,
    ) -> List[CellRegion]:
        min_cell_area_coverage = 0.15
        max_row_band = len(table_grid.rows) - 2
        max_col_band = len(table_grid.cols) - 2
        if max_row_band < 0 or max_col_band < 0:
            return []

        base_occupied_bands = self._occupied_bands(table_grid)
        occupied_bands = set(base_occupied_bands)
        synthetic_cells: List[CellRegion] = []
        synthetic_keys: Set[Tuple[int, int, int, int]] = set()

        for text_box in unmatched_spans:
            text = str(getattr(text_box, "text", "") or "").strip()
            if not text:
                continue

            row_bands = self._band_indexes_for_interval(table_grid.rows, text_box.y0, text_box.y1)
            col_bands = self._band_indexes_for_interval(table_grid.cols, text_box.x0, text_box.x1)
            if not row_bands or not col_bands:
                continue
            if self._extent_candidate_cell_indexes_for_text_box(table_grid, text_box, assignment_index):
                continue

            row_start = row_bands[0]
            row_end = row_bands[-1]
            col_start = col_bands[0]
            col_end = col_bands[-1]
            if not self._rect_is_empty(occupied_bands, row_start, row_end, col_start, col_end):
                continue

            expanded_col_start, expanded_col_end = self._expand_empty_col_range(
                occupied_bands,
                row_start,
                row_end,
                col_start,
                col_end,
                max_col_band,
            )
            synthetic_cell = CellRegion(
                row_start=row_start,
                row_end=row_end,
                col_start=expanded_col_start,
                col_end=expanded_col_end,
            )
            synthetic_bbox = self._cell_bbox(table_grid, synthetic_cell)
            if synthetic_bbox is None:
                continue
            synthetic_area = (synthetic_bbox[2] - synthetic_bbox[0]) * (synthetic_bbox[3] - synthetic_bbox[1])
            if synthetic_area <= 0.0:
                continue
            if (self._overlap_area(*synthetic_bbox, text_box) / synthetic_area) < min_cell_area_coverage:
                continue

            if not self._has_vertical_neighbor(
                base_occupied_bands,
                row_start,
                row_end,
                expanded_col_start,
                expanded_col_end,
                max_row_band,
            ):
                continue
            if not self._has_horizontal_neighbor(
                base_occupied_bands,
                row_start,
                row_end,
                expanded_col_start,
                expanded_col_end,
                max_col_band,
            ):
                continue

            synthetic_key = self._cell_region_key(synthetic_cell)
            if synthetic_key in synthetic_keys:
                continue

            synthetic_cells.append(synthetic_cell)
            synthetic_keys.add(synthetic_key)
            for row_idx in range(row_start, row_end + 1):
                for col_idx in range(expanded_col_start, expanded_col_end + 1):
                    occupied_bands.add((row_idx, col_idx))

        if not synthetic_cells:
            return []

        table_grid.cells.extend(synthetic_cells)
        table_grid.cells.sort(key=lambda item: (item.row_start, item.col_start, item.row_end, item.col_end))
        return synthetic_cells

    def _band_indexes_for_interval(
        self,
        boundaries: List[float],
        start: float,
        end: float,
    ) -> Tuple[int, ...]:
        if len(boundaries) < 2:
            return ()
        if start > end:
            start, end = end, start

        band_start = max(0, bisect_right(boundaries, start - self.tolerance) - 1)
        band_end = min(len(boundaries) - 2, bisect_left(boundaries, end + self.tolerance) - 1)
        if band_end < band_start:
            return ()
        return tuple(range(band_start, band_end + 1))

    def _candidate_cell_indexes_for_text_box(
        self,
        table_grid: TableGrid,
        text_box: Any,
        assignment_index: _TextAssignmentIndex,
    ) -> Tuple[int, ...]:
        anchor_x = self._anchor_value(
            float(text_box.x0),
            float(text_box.x1),
            self._text_cell_assign_options.x_anchor_ratio,
        )
        anchor_y = self._anchor_value(
            float(text_box.y0),
            float(text_box.y1),
            self._text_cell_assign_options.y_anchor_ratio,
        )
        row_bands = self._band_indexes_for_anchor(table_grid.rows, anchor_y)
        col_bands = self._band_indexes_for_anchor(table_grid.cols, anchor_x)
        if not row_bands or not col_bands:
            return ()
        candidate_indexes: Set[int] = set()
        for row_idx in row_bands:
            for col_idx in col_bands:
                candidate_indexes.update(assignment_index.bucket_cell_indexes.get((row_idx, col_idx), ()))
        return tuple(sorted(candidate_indexes))

    def _extent_candidate_cell_indexes_for_text_box(
        self,
        table_grid: TableGrid,
        text_box: Any,
        assignment_index: _TextAssignmentIndex,
    ) -> Tuple[int, ...]:
        row_bands = self._band_indexes_for_interval(table_grid.rows, text_box.y0, text_box.y1)
        col_bands = self._band_indexes_for_interval(table_grid.cols, text_box.x0, text_box.x1)
        if not row_bands or not col_bands:
            return ()

        candidate_indexes: Set[int] = set()
        for row_idx in row_bands:
            for col_idx in col_bands:
                candidate_indexes.update(assignment_index.bucket_cell_indexes.get((row_idx, col_idx), ()))
        return tuple(sorted(candidate_indexes))

    @staticmethod
    def _anchor_value(start: float, end: float, ratio: float) -> float:
        return float(start) + (float(end) - float(start)) * float(ratio)

    def _band_indexes_for_anchor(
        self,
        boundaries: Sequence[float],
        value: float,
        *,
        boundary_epsilon: float = 1e-6,
    ) -> Tuple[int, ...]:
        if len(boundaries) < 2:
            return ()

        first_boundary = float(boundaries[0])
        last_boundary = float(boundaries[-1])
        if value < first_boundary or value > last_boundary:
            return ()

        for boundary_idx, boundary in enumerate(boundaries[1:-1], start=1):
            if abs(value - float(boundary)) <= boundary_epsilon:
                return (boundary_idx - 1, boundary_idx)

        for idx in range(len(boundaries) - 1):
            top = float(boundaries[idx])
            bottom = float(boundaries[idx + 1])
            if top <= value < bottom:
                return (idx,)

        if abs(value - last_boundary) <= boundary_epsilon:
            return (len(boundaries) - 2,)
        return ()

    def _candidate_boundary_lines_for_text_box(
        self,
        table_grid: TableGrid,
        text_box: Any,
        assignment_index: Optional[_TextAssignmentIndex],
    ) -> List[Line]:
        if assignment_index is None:
            return self._active_boundary_lines(table_grid)

        row_bands = self._band_indexes_for_interval(table_grid.rows, text_box.y0, text_box.y1)
        if not row_bands:
            return []

        candidate_lines: List[Line] = []
        for row_idx in row_bands:
            candidate_lines.extend(assignment_index.active_boundary_lines_by_row.get(row_idx, ()))
        return candidate_lines

    def _select_cell_for_text_box(
        self,
        table_grid: TableGrid,
        text_box: Any,
        assignment_index: Optional[_TextAssignmentIndex] = None,
        candidate_indexes: Optional[Tuple[int, ...]] = None,
    ) -> Tuple[Optional[int], float]:
        if assignment_index is None:
            assignment_index = self._build_text_assignment_index(table_grid)

        if candidate_indexes is None:
            candidate_indexes = self._candidate_cell_indexes_for_text_box(table_grid, text_box, assignment_index)

        # KISS: anchor-only mode requires a unique cell hit.
        if len(candidate_indexes) != 1:
            return None, 0.0
        return candidate_indexes[0], 1.0

    def _overlapping_cell_count(
        self,
        table_grid: TableGrid,
        text_box: Any,
        assignment_index: Optional[_TextAssignmentIndex] = None,
    ) -> int:
        text_area = self._text_box_area(text_box)
        if text_area <= 0.0:
            return 0

        if assignment_index is None:
            assignment_index = self._build_text_assignment_index(table_grid)
        return len(self._extent_candidate_cell_indexes_for_text_box(table_grid, text_box, assignment_index))

    def _has_stable_split_geometry(self, text_box: Any, split_spanboxes: List[Any]) -> bool:
        min_fragment_width = 20.0
        min_total_width = 60.0
        if (text_box.x1 - text_box.x0) < min_total_width:
            return False
        return all((spanbox.x1 - spanbox.x0) >= min_fragment_width for spanbox in split_spanboxes)

    def _split_improves_cell_assignment(
        self,
        table_grid: TableGrid,
        text_box: Any,
        split_spanboxes: List[Any],
        assignment_index: Optional[_TextAssignmentIndex] = None,
    ) -> bool:
        if len(split_spanboxes) <= 1:
            return False
        if not self._has_stable_split_geometry(text_box, split_spanboxes):
            return False
        if assignment_index is None:
            assignment_index = self._build_text_assignment_index(table_grid)
        if not self._has_active_boundary_crossing(table_grid, text_box, assignment_index=assignment_index):
            return False
        if len(self._extent_candidate_cell_indexes_for_text_box(table_grid, text_box, assignment_index)) < 2:
            return False
        assigned_cell_indexes: List[int] = []

        for spanbox in split_spanboxes:
            cell_idx, _ = self._select_cell_for_text_box(table_grid, spanbox, assignment_index=assignment_index)
            if cell_idx is None:
                return False
            assigned_cell_indexes.append(cell_idx)

        if len(set(assigned_cell_indexes)) <= 1:
            return False
        if any(curr <= prev for prev, curr in zip(assigned_cell_indexes, assigned_cell_indexes[1:])):
            return False
        return True

    def _active_boundary_lines_by_row(self, table_grid: TableGrid) -> Dict[int, List[Line]]:
        active_lines_by_row: Dict[int, List[Line]] = {}
        seen: Set[Tuple[int, int]] = set()
        if len(table_grid.rows) < 2 or len(table_grid.cols) < 3:
            return active_lines_by_row

        for row_idx in range(len(table_grid.rows) - 1):
            band_top = table_grid.rows[row_idx]
            band_bottom = table_grid.rows[row_idx + 1]
            for cell in table_grid.cells:
                if not (cell.row_start <= row_idx <= cell.row_end):
                    continue
                boundary_idx = cell.col_end + 1
                if boundary_idx <= 0 or boundary_idx >= len(table_grid.cols) - 1:
                    continue
                key = (row_idx, boundary_idx)
                if key in seen:
                    continue
                seen.add(key)
                boundary_x = table_grid.cols[boundary_idx]
                active_lines_by_row.setdefault(row_idx, []).append(
                    Line(
                        x0=boundary_x,
                        y0=band_top,
                        x1=boundary_x,
                        y1=band_bottom,
                        is_horizontal=False,
                    )
                )

        return active_lines_by_row

    def _active_boundary_lines(self, table_grid: TableGrid) -> List[Line]:
        active_lines: List[Line] = []
        for lines in self._active_boundary_lines_by_row(table_grid).values():
            active_lines.extend(lines)
        return active_lines

    def _split_text_boxes_by_active_boundaries(
        self,
        table_grid: TableGrid,
        text_boxes: List[Any],
        assignment_index: Optional[_TextAssignmentIndex] = None,
    ) -> Tuple[List[Any], int]:
        if assignment_index is not None:
            has_active_boundaries = any(assignment_index.active_boundary_lines_by_row.values())
        else:
            has_active_boundaries = bool(self._active_boundary_lines(table_grid))
        if not has_active_boundaries:
            return list(text_boxes), 0

        processed_text_boxes: List[Any] = []
        split_span_count = 0

        for text_box in text_boxes:
            active_lines = self._candidate_boundary_lines_for_text_box(table_grid, text_box, assignment_index)
            if not active_lines:
                processed_text_boxes.append(text_box)
                continue

            candidate_lines: Dict[float, Line] = {}
            for line in active_lines:
                if line.y1 < text_box.y0 or line.y0 > text_box.y1:
                    continue
                if not (text_box.x0 < line.x0 < text_box.x1):
                    continue
                text_width = max(1.0, text_box.x1 - text_box.x0)
                split_ratio = (line.x0 - text_box.x0) / text_width
                if not self._has_wide_whitespace_gap_near_ratio(getattr(text_box, "text", ""), split_ratio):
                    continue

                key = round(line.x0, 4)
                existing_line = candidate_lines.get(key)
                if existing_line is None or self._line_text_overlap_height(line, text_box) > self._line_text_overlap_height(existing_line, text_box):
                    candidate_lines[key] = line

            if not candidate_lines:
                processed_text_boxes.append(text_box)
                continue

            split_spanboxes = self._split_spanbox_against_vertical_lines(text_box, list(candidate_lines.values()))
            split_spanboxes = [spanbox for spanbox in split_spanboxes if getattr(spanbox, "text", "").strip()]
            if len(split_spanboxes) <= 1:
                processed_text_boxes.append(text_box)
                continue
            if not self._split_improves_cell_assignment(
                table_grid,
                text_box,
                split_spanboxes,
                assignment_index=assignment_index,
            ):
                processed_text_boxes.append(text_box)
                continue

            processed_text_boxes.extend(split_spanboxes)
            split_span_count += 1

        return processed_text_boxes, split_span_count

    def fill_text_content_by_location(
        self,
        table_grid: TableGrid,
        text_boxes: List[Any],
        *,
        word_boxes: Optional[List[Any]] = None,
        character_boxes: Optional[List[Any]] = None,
    ) -> List[Any]:
        """基于位置填充单元格文本内容

        Args:
            table_grid: 表格网格结构
            text_boxes: 文本框列表
        """
        for cell_region in table_grid.cells:
            cell_region.content = ""

        assignment_pass = self._build_text_assignment_pass(
            table_grid,
            text_boxes,
            word_boxes=word_boxes,
            character_boxes=character_boxes,
        )
        synthetic_cells = self._create_synthetic_cells_for_unmatched_spans(
            table_grid,
            assignment_pass.unmatched_spans,
            assignment_pass.assignment_index,
        )
        if synthetic_cells:
            assignment_pass = self._build_text_assignment_pass(
                table_grid,
                text_boxes,
                word_boxes=word_boxes,
                character_boxes=character_boxes,
            )

        for cell_idx, cell_region in enumerate(table_grid.cells):
            assigned_spans = assignment_pass.cell_text_map.get(cell_idx) or []
            if assigned_spans:
                cell_region.content = self._compose_cell_content(assigned_spans)

        synthetic_keys = {self._cell_region_key(cell_region) for cell_region in synthetic_cells}
        synthetic_assigned_span_count = sum(
            len(assignment_pass.cell_text_map.get(cell_idx) or []) for cell_idx, cell_region in enumerate(table_grid.cells) if self._cell_region_key(cell_region) in synthetic_keys
        )
        self._last_text_assignment_diagnostics = {
            "matched_span_count": len(assignment_pass.matched_text_indexes),
            "unmatched_span_count": len(assignment_pass.unmatched_indexes),
            "split_span_count": assignment_pass.split_span_count,
            "synthetic_cell_count": len(synthetic_cells),
            "synthetic_assigned_span_count": synthetic_assigned_span_count,
            "word_fallback_span_count": assignment_pass.cross_cell_fallback_diagnostics.word_fallback_span_count,
            "word_fallback_fragment_count": assignment_pass.cross_cell_fallback_diagnostics.word_fallback_fragment_count,
            "char_fallback_word_count": assignment_pass.cross_cell_fallback_diagnostics.char_fallback_word_count,
            "char_fallback_fragment_count": assignment_pass.cross_cell_fallback_diagnostics.char_fallback_fragment_count,
            "cross_cell_fallback_rejected_count": assignment_pass.cross_cell_fallback_diagnostics.cross_cell_fallback_rejected_count,
        }

        logger.debug(
            "文本落格诊断: matched=%d unmatched=%d split=%d synthetic=%d synthetic_assigned=%d "
            "word_fallback_spans=%d word_fallback_fragments=%d char_fallback_words=%d "
            "char_fallback_fragments=%d fallback_rejected=%d",
            self._last_text_assignment_diagnostics["matched_span_count"],
            self._last_text_assignment_diagnostics["unmatched_span_count"],
            self._last_text_assignment_diagnostics["split_span_count"],
            self._last_text_assignment_diagnostics["synthetic_cell_count"],
            self._last_text_assignment_diagnostics["synthetic_assigned_span_count"],
            self._last_text_assignment_diagnostics["word_fallback_span_count"],
            self._last_text_assignment_diagnostics["word_fallback_fragment_count"],
            self._last_text_assignment_diagnostics["char_fallback_word_count"],
            self._last_text_assignment_diagnostics["char_fallback_fragment_count"],
            self._last_text_assignment_diagnostics["cross_cell_fallback_rejected_count"],
        )

        for synthetic_cell in synthetic_cells[:10]:
            logger.debug(
                "synthetic cell: rows=%d-%d cols=%d-%d",
                synthetic_cell.row_start,
                synthetic_cell.row_end,
                synthetic_cell.col_start,
                synthetic_cell.col_end,
            )

        for idx in assignment_pass.unmatched_indexes[:10]:
            text_box = assignment_pass.processed_text_boxes[idx]
            logger.debug(
                "unmatched span[%d]: page=%s bbox=[%.1f, %.1f, %.1f, %.1f] text=%r",
                idx,
                getattr(text_box, "page", "?"),
                text_box.x0,
                text_box.y0,
                text_box.x1,
                text_box.y1,
                getattr(text_box, "text", ""),
            )

        remaining_unmatched = len(assignment_pass.unmatched_indexes) - 10
        if remaining_unmatched > 0:
            logger.debug("unmatched span日志已截断: 还有 %d 个未打印", remaining_unmatched)

        self._emit_band_diagnostics(table_grid)
        return assignment_pass.unmatched_spans

    def _compose_cell_content(self, text_boxes: List[Any]) -> str:
        """Assemble cell text by geometry: same visual line joins with spaces, line breaks keep `\\n`."""
        spans: List[Tuple[float, float, float, str]] = []
        for text_box in text_boxes:
            text = str(getattr(text_box, "text", "") or "").strip()
            if not text:
                continue
            y0 = float(getattr(text_box, "y0", 0.0))
            y1 = float(getattr(text_box, "y1", y0))
            x0 = float(getattr(text_box, "x0", 0.0))
            center_y = (y0 + y1) / 2.0
            height = max(1.0, y1 - y0)
            spans.append((center_y, x0, height, text))

        if not spans:
            return ""

        spans.sort(key=lambda item: (item[0], item[1]))
        lines: List[List[Tuple[float, str]]] = []
        current_center: float | None = None
        current_height: float = 0.0

        for center_y, x0, height, text in spans:
            if current_center is None:
                lines.append([(x0, text)])
                current_center = center_y
                current_height = height
                continue

            same_line_tolerance = max(1.5, min(current_height, height) * 0.6)
            if abs(center_y - current_center) <= same_line_tolerance:
                lines[-1].append((x0, text))
                line_item_count = len(lines[-1])
                current_center = ((current_center * (line_item_count - 1)) + center_y) / line_item_count
                current_height = max(current_height, height)
                continue

            lines.append([(x0, text)])
            current_center = center_y
            current_height = height

        line_texts: List[str] = []
        for line in lines:
            ordered = [text for _, text in sorted(line, key=lambda item: item[0])]
            line_texts.append(" ".join(ordered))

        # Avoid dangling single-CJK-char lines caused by tiny baseline drift, e.g. "一\n般".
        merged_line_texts: List[str] = []
        for line_text in line_texts:
            token = line_text.strip()
            if merged_line_texts and self._is_single_cjk_char(token):
                merged_line_texts[-1] = f"{merged_line_texts[-1]}{token}"
                continue
            merged_line_texts.append(line_text)
        return "\n".join(merged_line_texts)

    @staticmethod
    def _is_single_cjk_char(text: str) -> bool:
        if len(text) != 1:
            return False
        ch = text[0]
        return "\u4e00" <= ch <= "\u9fff"

    def analyze_table_structure(self, elements: Sequence[LineLike]) -> TableGrid:
        """分析线条元素，提取表格结构 - 增强版本

        Args:
            elements: 线条元素列表

        Returns:
            TableGrid: 表格网格结构
        """
        if not elements:
            return TableGrid([], [], [])

        # 步骤1: 检测并合并线条
        horizontal_lines, vertical_lines = self.detect_and_merge_lines(elements)

        # 步骤2: 计算交点
        self.compute_intersections(horizontal_lines, vertical_lines)

        # 步骤3: 分割表格网格
        table_grid = self.segment_table_grid(horizontal_lines, vertical_lines)

        logger.debug(f"表格分析完成: {len(table_grid.rows) - 1} 行 x {len(table_grid.cols) - 1} 列, {len(table_grid.cells)} 个单元格")

        return table_grid


__all__ = [
    "Intersection",
    "Line",
    "LineGridReconstructor",
]
