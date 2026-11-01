"""Cell clustering helpers for geometric PDF->Excel conversion."""

from __future__ import annotations

CellCoord = tuple[float, float, float, float, int, str]


def cluster_cells(cells: list[CellCoord], v_tolerance: float = 20, h_overlap_min: float = 0.5) -> list[list[CellCoord]]:
    """
    更健壮的单元格聚类算法，用于将独立的单元格组合成表格。
    它首先将单元格按垂直重叠关系组合成“行”，然后根据垂直邻近度和水平重叠度将这些“行”聚类成表格。
    这是识别单页多表和处理复杂布局的核心。

    Args:
        cells (list): 从PDF中提取的所有单元格元组 (x0, top, x1, bottom, page_number) 的列表。
        v_tolerance (int): 两行之间被视作同一表格的最大垂直间距。
        h_overlap_min (float): 两行之间被视作同一表格所需的最小水平重叠率。

    Returns:
        list: 一个列表的列表，每个子列表代表一个表格（一个单元格簇）。
    """
    if not cells:
        return []

    # 步骤 1: 将单元格按垂直重叠关系组合成“行”
    rows = []
    cells.sort(key=lambda c: (c[4], c[1]))  # 按页码和垂直位置排序
    if not cells:
        return []
    current_row = [cells[0]]
    for i in range(1, len(cells)):
        # 如果单元格在同一页，并且其顶部坐标小于当前行中所有单元格的最大底部坐标，则视为同一行
        if cells[i][4] == current_row[0][4] and cells[i][1] < max(c[3] for c in current_row):
            current_row.append(cells[i])
        else:
            rows.append(current_row)
            current_row = [cells[i]]
    rows.append(current_row)

    # 步骤 2: 将“行”聚类成表格
    clusters = []
    if not rows:
        return []
    current_cluster = rows[0]
    for i in range(1, len(rows)):
        prev_row, current_row = rows[i - 1], rows[i]

        # 计算两行的垂直和水平边界
        prev_row_bottom = max(c[3] for c in prev_row)
        current_row_top = min(c[1] for c in current_row)
        prev_row_x0, prev_row_x1 = (
            min(c[0] for c in prev_row),
            max(c[2] for c in prev_row),
        )
        current_row_x0, current_row_x1 = (
            min(c[0] for c in current_row),
            max(c[2] for c in current_row),
        )

        # 计算水平重叠度
        overlap_x0, overlap_x1 = (
            max(prev_row_x0, current_row_x0),
            min(prev_row_x1, current_row_x1),
        )
        overlap_width = max(0, overlap_x1 - overlap_x0)
        prev_row_width = prev_row_x1 - prev_row_x0

        # 判断连续性的三个条件
        same_page = prev_row[0][4] == current_row[0][4]
        vertically_close = (current_row_top - prev_row_bottom) < v_tolerance
        horizontally_overlapping = (overlap_width / prev_row_width) > h_overlap_min if prev_row_width > 0 else False

        # 如果三个条件都满足，则将当前行加入当前表格簇
        if same_page and vertically_close and horizontally_overlapping:
            current_cluster.extend(current_row)
        else:
            clusters.append(current_cluster)
            current_cluster = current_row
    clusters.append(current_cluster)

    # 步骤 3: “规模过滤器”，移除由噪声（如页眉页脚）产生的微小“伪表格”
    min_cells_threshold = 5  # 一个有效的表格至少需要包含5个单元格
    return [cluster for cluster in clusters if len(cluster) > min_cells_threshold]


def find_cell_indices_for_cluster(
    coord_tuple: CellCoord,
    x_boundaries: list[float],
    y_boundaries: list[float],
) -> tuple[int, int, int, int]:
    """
    根据单元格的坐标和已定义的网格边界，计算其在Excel中的起始/结束行列索引。

    Args:
        coord_tuple (tuple): 单元格的坐标元组 (x0, top, x1, bottom, ...)。
        x_boundaries (list): 已排序的、唯一的X坐标（列边界）列表。
        y_boundaries (list): 已排序的、唯一的Y坐标（行边界）列表。

    Returns:
        tuple: (起始行, 起始列, 结束行, 结束列) 的0-based索引。
    """
    x0, top, x1, bottom, *_ = coord_tuple
    tolerance = 1e-6  # 使用一个小的容差来处理浮点数精度问题
    try:
        start_col = next(i for i, b in enumerate(x_boundaries) if abs(b - x0) < tolerance)
        end_col = next(i for i, b in enumerate(x_boundaries) if abs(b - x1) < tolerance)
        start_row = next(i for i, b in enumerate(y_boundaries) if abs(b - top) < tolerance)
        end_row = next(i for i, b in enumerate(y_boundaries) if abs(b - bottom) < tolerance)
    except StopIteration:
        # 如果找不到精确匹配，说明该单元格可能是个异常，返回无效索引
        return -1, -1, -1, -1
    # 返回的结束行列索引需要-1，因为我们要的是单元格的跨度
    return start_row, start_col, end_row - 1, end_col - 1


__all__ = [
    "cluster_cells",
    "find_cell_indices_for_cluster",
]
