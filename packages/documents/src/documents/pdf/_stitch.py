"""Stitching helpers for per-page extraction results."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from ..table.models import CellRegion, TableGrid
from .result import PageExtraction
from .spec import ColumnPolicy, StitchMode, StitchSpec


def stitch_pages(page_results: list[PageExtraction], stitch: StitchSpec) -> TableGrid:
    return stitch_pages_with_outcome(page_results, stitch).table_grid


@dataclass(frozen=True)
class StitchOutcome:
    table_grid: TableGrid
    consumed_page_count: int
    stop_reason: Literal["overlap", "large_y_gap"] | None = None
    large_y_gap: float | None = None
    large_y_gap_threshold: float | None = None


def stitch_pages_with_outcome(
    page_results: list[PageExtraction],
    stitch: StitchSpec,
) -> StitchOutcome:
    if stitch.mode == StitchMode.NO_STITCH:
        grid = _no_stitch(page_results)
        return StitchOutcome(
            table_grid=grid,
            consumed_page_count=1,
            stop_reason=None,
            large_y_gap=None,
            large_y_gap_threshold=None,
        )
    return _stack_rows(page_results, stitch)


def _no_stitch(page_results: list[PageExtraction]) -> TableGrid:
    valid = _successful_grids(page_results)
    if len(valid) != 1:
        raise ValueError(f"no_stitch expects exactly one successful page, got={len(valid)}")
    return valid[0]


def _stack_rows(
    page_results: list[PageExtraction],
    stitch: StitchSpec,
) -> StitchOutcome:
    successful_results: list[PageExtraction] = []
    for page_result in _successful_page_results(page_results):
        adjusted = _split_multirow_cells_by_marker_rows(page_result, stitch=stitch)
        adjusted = _split_multirow_value_cells_by_neighbor_labels(adjusted, stitch=stitch)
        successful_results.append(adjusted)
    grids = [page_result.table_grid for page_result in successful_results]
    if not successful_results:
        raise ValueError("no successful page grids to stitch")

    target_cols = _resolve_target_columns(grids, stitch.column_policy)
    if len(target_cols) < 2:
        raise ValueError("stitched target columns must contain at least two boundaries")
    max_cell_col = max(0, len(target_cols) - 2)

    total_rows = 0
    combined_cells: list[CellRegion] = []
    consumed_page_count = 0
    stop_reason: Literal["overlap", "large_y_gap"] | None = None
    large_y_gap: float | None = None
    large_y_gap_threshold: float | None = None
    previous_stitched_page: PageExtraction | None = None

    for idx, page_result in enumerate(successful_results):
        grid = page_result.table_grid
        rows_in_grid = max(0, len(grid.rows) - 1)
        if rows_in_grid == 0:
            consumed_page_count = idx + 1
            continue

        skip_local_rows = 1 if (stitch.drop_repeated_header and idx > 0) else 0
        if skip_local_rows >= rows_in_grid:
            consumed_page_count = idx + 1
            continue

        if previous_stitched_page is not None:
            gap_split = _resolve_large_y_gap_split(
                previous_page=previous_stitched_page,
                current_page=page_result,
                stitch=stitch,
            )
            if gap_split is not None:
                large_y_gap = gap_split[0]
                large_y_gap_threshold = gap_split[1]
                stop_reason = "large_y_gap"
                break

        boundary_map = _build_boundary_index_map(grid.cols, target_cols)
        row_offset = total_rows
        remapped_page_cells: list[CellRegion] = []

        for cell in grid.cells:
            if cell.row_end < skip_local_rows:
                continue

            col_start, col_end = _remap_cell_columns(cell, boundary_map)
            if col_start > max_cell_col:
                if stitch.column_policy == ColumnPolicy.FIRST_PAGE:
                    continue
                col_start = max_cell_col
            if col_end > max_cell_col:
                col_end = max_cell_col

            new_row_start = max(cell.row_start, skip_local_rows) - skip_local_rows + row_offset
            new_row_end = max(cell.row_end, skip_local_rows) - skip_local_rows + row_offset

            remapped_page_cells.append(
                replace(
                    cell,
                    row_start=new_row_start,
                    row_end=new_row_end,
                    col_start=col_start,
                    col_end=col_end,
                    content=cell.content,
                )
            )

        # Fail-closed for this group: once remapped cells overlap, stop stitching
        # subsequent pages instead of falling back to index projection.
        if _has_overlapping_regions(remapped_page_cells):
            stop_reason = "overlap"
            break

        combined_cells.extend(remapped_page_cells)
        total_rows += rows_in_grid - skip_local_rows
        consumed_page_count = idx + 1
        previous_stitched_page = page_result

    combined_rows = [float(i) for i in range(total_rows + 1)]
    combined_cols = [float(x) for x in target_cols]
    return StitchOutcome(
        table_grid=TableGrid(rows=combined_rows, cols=combined_cols, cells=combined_cells),
        consumed_page_count=consumed_page_count,
        stop_reason=stop_reason,
        large_y_gap=large_y_gap,
        large_y_gap_threshold=large_y_gap_threshold,
    )


def _successful_grids(page_results: list[PageExtraction]) -> list[TableGrid]:
    return [page_result.table_grid for page_result in _successful_page_results(page_results)]


def _successful_page_results(page_results: list[PageExtraction]) -> list[PageExtraction]:
    return [page_result for page_result in page_results if page_result.success and page_result.table_grid.rows and page_result.table_grid.cols]


def _resolve_target_columns(grids: list[TableGrid], policy: ColumnPolicy) -> list[float]:
    if not grids:
        return []

    if policy == ColumnPolicy.FIRST_PAGE:
        return list(grids[0].cols)

    if policy == ColumnPolicy.STRICT:
        expected = len(grids[0].cols)
        for grid in grids[1:]:
            if len(grid.cols) != expected:
                raise ValueError(f"strict column policy mismatch: expected={expected}, got={len(grid.cols)}")
        return list(grids[0].cols)

    merged = _merge_close_boundaries(
        [boundary for grid in grids for boundary in grid.cols],
        tolerance=0.5,
    )
    return merged


def _merge_close_boundaries(boundaries: list[float], *, tolerance: float) -> list[float]:
    if not boundaries:
        return []
    sorted_values = sorted(boundaries)
    merged: list[float] = [sorted_values[0]]
    for value in sorted_values[1:]:
        if abs(value - merged[-1]) <= tolerance:
            merged[-1] = (merged[-1] + value) / 2.0
        else:
            merged.append(value)
    return merged


def _build_boundary_index_map(source_cols: list[float], target_cols: list[float]) -> list[int]:
    if len(source_cols) < 2:
        raise ValueError("source boundary count < 2")
    if len(target_cols) < 2:
        raise ValueError("target boundary count < 2")

    target_last = len(target_cols) - 1
    mapped = [_nearest_boundary_index(boundary, target_cols) for boundary in source_cols]
    mapped[0] = 0
    mapped[-1] = target_last

    for index in range(1, len(mapped)):
        if mapped[index] < mapped[index - 1]:
            mapped[index] = mapped[index - 1]
    for index in range(len(mapped) - 2, -1, -1):
        if mapped[index] > mapped[index + 1]:
            mapped[index] = mapped[index + 1]

    mapped[0] = 0
    mapped[-1] = target_last
    return mapped


def _nearest_boundary_index(boundary: float, target_cols: list[float]) -> int:
    return min(range(len(target_cols)), key=lambda index: abs(target_cols[index] - boundary))


def _remap_cell_columns(cell: CellRegion, boundary_map: list[int]) -> tuple[int, int]:
    source_left_boundary = cell.col_start
    source_right_boundary = cell.col_end + 1

    mapped_left = boundary_map[source_left_boundary]
    mapped_right = boundary_map[source_right_boundary]

    if mapped_right <= mapped_left:
        return mapped_left, mapped_left
    return mapped_left, mapped_right - 1


def _has_overlapping_regions(cells: list[CellRegion]) -> bool:
    ordered = sorted(cells, key=lambda cell: (cell.row_start, cell.row_end, cell.col_start, cell.col_end))
    active: list[CellRegion] = []
    for cell in ordered:
        active = [other for other in active if other.row_end >= cell.row_start]
        for other in active:
            if cell.col_end >= other.col_start and other.col_end >= cell.col_start:
                return True
        active.append(cell)
    return False


def _resolve_large_y_gap_split(
    *,
    previous_page: PageExtraction,
    current_page: PageExtraction,
    stitch: StitchSpec,
) -> tuple[float, float] | None:
    if not stitch.split_on_large_y_gap:
        return None

    previous_height = previous_page.page_height
    if previous_height is None or previous_height <= 0:
        return None

    previous_grid = previous_page.table_grid
    current_grid = current_page.table_grid
    if not previous_grid.rows or not current_grid.rows:
        return None

    previous_bottom = previous_grid.rows[-1]
    current_top = current_grid.rows[0]
    virtual_gap = (previous_height - previous_bottom) + current_top
    threshold = stitch.large_y_gap_ratio * previous_height
    if virtual_gap >= threshold:
        return (virtual_gap, threshold)
    return None


def _split_multirow_cells_by_marker_rows(
    page_result: PageExtraction,
    *,
    stitch: StitchSpec,
) -> PageExtraction:
    markers = tuple(_normalize_marker_text(marker) for marker in stitch.row_split_markers if str(marker).strip())
    if not markers:
        return page_result

    grid = page_result.table_grid
    if len(grid.rows) < 2 or len(grid.cols) < 2 or not grid.cells:
        return page_result

    marker_rows = _resolve_marker_rows(grid=grid, markers=markers)
    if not marker_rows:
        return page_result

    replaced = False
    next_cells: list[CellRegion] = []
    for cell in sorted(grid.cells, key=lambda item: (item.row_start, item.col_start, item.row_end, item.col_end)):
        split_points = sorted(row for row in marker_rows if cell.row_start < row <= cell.row_end)
        if not split_points:
            next_cells.append(cell)
            continue

        segments: list[tuple[int, int]] = []
        seg_start = cell.row_start
        for split_at in split_points:
            seg_end = split_at - 1
            if seg_end >= seg_start:
                segments.append((seg_start, seg_end))
            seg_start = split_at
        if seg_start <= cell.row_end:
            segments.append((seg_start, cell.row_end))

        if len(segments) <= 1:
            next_cells.append(cell)
            continue

        rows = list(range(cell.row_start, cell.row_end + 1))
        row_contents = _split_cell_content_by_rows(page_result=page_result, cell=cell, rows=rows)
        for seg_start, seg_end in segments:
            content = "".join(row_contents.get(row, "") for row in range(seg_start, seg_end + 1)).strip()
            next_cells.append(
                replace(
                    cell,
                    row_start=seg_start,
                    row_end=seg_end,
                    col_start=cell.col_start,
                    col_end=cell.col_end,
                    content=content,
                )
            )
        replaced = True

    if not replaced:
        return page_result

    stitched_grid = TableGrid(rows=list(grid.rows), cols=list(grid.cols), cells=next_cells)
    return PageExtraction(
        page_index=page_result.page_index,
        success=page_result.success,
        table_grid=stitched_grid,
        span_boxes=page_result.span_boxes,
        line_elements=page_result.line_elements,
        intersections_count=page_result.intersections_count,
        cells_count=len(next_cells),
        page_height=page_result.page_height,
        outer_text_lines=page_result.outer_text_lines,
        table_region=page_result.table_region,
        error_message=page_result.error_message,
    )


def _normalize_marker_text(value: str) -> str:
    return "".join(str(value).split()).lower()


def _resolve_marker_rows(*, grid: TableGrid, markers: tuple[str, ...]) -> set[int]:
    marker_rows: set[int] = set()
    row_count = len(grid.rows) - 1
    for row_idx in range(row_count):
        row_text = _normalize_marker_text("".join(str(cell.content or "") for cell in grid.cells if cell.row_start <= row_idx <= cell.row_end and str(cell.content or "").strip()))
        if not row_text:
            continue
        if any(marker in row_text for marker in markers):
            marker_rows.add(row_idx)
    return marker_rows


def _split_multirow_value_cells_by_neighbor_labels(
    page_result: PageExtraction,
    *,
    stitch: StitchSpec,
) -> PageExtraction:
    if not stitch.split_multirow_value_by_neighbor_labels:
        return page_result

    grid = page_result.table_grid
    if len(grid.rows) < 2 or len(grid.cols) < 2 or not grid.cells:
        return page_result

    replaced = False
    next_cells: list[CellRegion] = []

    for cell in sorted(grid.cells, key=lambda item: (item.row_start, item.col_start, item.row_end, item.col_end)):
        split_rows = _resolve_neighbor_label_split_rows(grid=grid, target=cell)
        if not split_rows:
            next_cells.append(cell)
            continue

        row_contents = _split_cell_content_by_rows(page_result=page_result, cell=cell, rows=split_rows)
        for row in split_rows:
            next_cells.append(
                replace(
                    cell,
                    row_start=row,
                    row_end=row,
                    col_start=cell.col_start,
                    col_end=cell.col_end,
                    content=row_contents.get(row, ""),
                )
            )
        replaced = True

    if not replaced:
        return page_result

    stitched_grid = TableGrid(rows=list(grid.rows), cols=list(grid.cols), cells=next_cells)
    return PageExtraction(
        page_index=page_result.page_index,
        success=page_result.success,
        table_grid=stitched_grid,
        span_boxes=page_result.span_boxes,
        line_elements=page_result.line_elements,
        intersections_count=page_result.intersections_count,
        cells_count=len(next_cells),
        page_height=page_result.page_height,
        outer_text_lines=page_result.outer_text_lines,
        table_region=page_result.table_region,
        error_message=page_result.error_message,
    )


def _resolve_neighbor_label_split_rows(*, grid: TableGrid, target: CellRegion) -> list[int]:
    row_span = target.row_end - target.row_start + 1
    col_span = target.col_end - target.col_start + 1
    if row_span < 2 or col_span < 2:
        return []

    split_rows: list[int] = []
    for row_idx in range(target.row_start, target.row_end + 1):
        neighbor = _find_left_neighbor_label_cell(grid=grid, target=target, row_idx=row_idx)
        if neighbor is None:
            return []
        # Keep split conservative: each target row must map to an independent label row.
        if neighbor.row_start != neighbor.row_end:
            return []
        if not str(neighbor.content or "").strip():
            return []
        split_rows.append(row_idx)

    if len(split_rows) < 2:
        return []
    return split_rows


def _find_left_neighbor_label_cell(
    *,
    grid: TableGrid,
    target: CellRegion,
    row_idx: int,
) -> CellRegion | None:
    best: CellRegion | None = None
    best_gap = 10**9
    for cell in grid.cells:
        if cell.col_end >= target.col_start:
            continue
        if cell.row_start > row_idx or cell.row_end < row_idx:
            continue
        gap = target.col_start - cell.col_end
        if gap < 1 or gap > 2:
            continue
        if best is None or gap < best_gap or (gap == best_gap and cell.col_end > best.col_end):
            best = cell
            best_gap = gap
    return best


def _split_cell_content_by_rows(
    *,
    page_result: PageExtraction,
    cell: CellRegion,
    rows: list[int],
) -> dict[int, str]:
    grid = page_result.table_grid
    x0 = float(grid.cols[cell.col_start])
    x1 = float(grid.cols[cell.col_end + 1])
    y0 = float(grid.rows[cell.row_start])
    y1 = float(grid.rows[cell.row_end + 1])

    per_row_spans: dict[int, list[tuple[float, float, str]]] = {row: [] for row in rows}
    for span in page_result.span_boxes:
        if getattr(span, "page", None) != page_result.page_index:
            continue

        center_x = (float(getattr(span, "x0", 0.0)) + float(getattr(span, "x1", 0.0))) / 2.0
        center_y = (float(getattr(span, "y0", 0.0)) + float(getattr(span, "y1", 0.0))) / 2.0
        if center_x < x0 or center_x > x1 or center_y < y0 or center_y > y1:
            continue

        row_idx = _locate_row_band(grid.rows, center_y)
        if row_idx is None or row_idx not in per_row_spans:
            continue
        text = str(getattr(span, "text", "") or "").strip()
        if not text:
            continue
        per_row_spans[row_idx].append((float(getattr(span, "y0", 0.0)), float(getattr(span, "x0", 0.0)), text))

    if not any(per_row_spans.values()):
        return {rows[0]: str(cell.content or "").strip(), **{row: "" for row in rows[1:]}}

    resolved: dict[int, str] = {}
    for row_idx in rows:
        spans = sorted(per_row_spans[row_idx], key=lambda item: (item[0], item[1]))
        resolved[row_idx] = "".join(text for _, _, text in spans).strip()
    return resolved


def _locate_row_band(rows: list[float], y: float) -> int | None:
    if len(rows) < 2:
        return None
    if y <= rows[0]:
        return 0
    if y >= rows[-1]:
        return len(rows) - 2
    for idx in range(len(rows) - 1):
        if rows[idx] <= y <= rows[idx + 1]:
            return idx
    return None
