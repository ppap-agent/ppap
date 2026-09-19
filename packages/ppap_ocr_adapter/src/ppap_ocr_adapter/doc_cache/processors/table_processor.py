from io import StringIO
from typing import Any

import fitz
import openpyxl
import pandas as pd
from openpyxl.worksheet.worksheet import Worksheet

from .base_processor import BaseProcessor, LayoutBox, ProcessingContext


def _coerce_table_rows(rows: object) -> list[list[object]] | None:
    if not isinstance(rows, list):
        return None
    normalized: list[list[object]] = []
    for row in rows:
        if not isinstance(row, list):
            return None
        normalized.append(list(row))
    return normalized


class TableProcessor(BaseProcessor):
    """
    处理表格 (Table) 和跨页表格组。
    处理后将对应的 Box 从 layout_structure 中移除。
    """

    def process(self, ctx: ProcessingContext) -> None:
        if not ctx.layout_structure:
            return

        print(">>> [Chain] TableProcessor Start...")
        remaining_structure = []

        with fitz.open(ctx.pdf_path) as pdf:
            for item in ctx.layout_structure:
                is_processed = False

                # 情况 A: 跨页表格组 (List)
                if isinstance(item, list):
                    print("  Processing Cross-page Table Group...")
                    full_table_data: list[list[object]] = []
                    for box in item:
                        chunk = self._extract_data(pdf, box, ctx)
                        if chunk:
                            full_table_data.extend(chunk)

                    if full_table_data:
                        self._write_excel(ctx, full_table_data)
                    is_processed = True  # 标记为已处理，不加入 remaining

                # 情况 B: 单个对象 (Dict)
                elif isinstance(item, dict):
                    if item.get("label") == "table":
                        print(f"  Processing Single Table (Page {item.get('page_idx')})...")
                        data = self._extract_data(pdf, item, ctx)
                        if not data:
                            data = self._extract_paddle_table_data(ctx, item)
                        if data:
                            self._write_excel(ctx, data)
                        is_processed = True

                # 如果不是表格，或者处理完了，根据逻辑保留或丢弃
                # 现在的逻辑是：处理过的表格要弹出去 (即不加入 remaining)
                # 未处理的（如 seal, paragraph_title）要保留
                if not is_processed:
                    remaining_structure.append(item)

        # 更新 Context 中的结构
        ctx.layout_structure = remaining_structure
        # 仅当实际提取到工作表时保存 Excel，避免生成损坏的空工作簿。
        if ctx.workbook.sheetnames:
            ctx.workbook.save(ctx.output_excel_path)
        print(f"  Tables extracted. Remaining items: {len(ctx.layout_structure)}")

    def _extract_data(self, pdf_obj: Any, box: LayoutBox, ctx: ProcessingContext) -> list[list[object]] | None:
        return extract_data_from_box(pdf_obj, box, ctx.img_w, ctx.img_h, ctx.padding)

    def _extract_paddle_table_data(self, ctx: ProcessingContext, box: LayoutBox) -> list[list[object]] | None:
        page_idx = int(box.get("page_idx", -1))
        if page_idx < 0:
            return None

        pages = ctx.raw_data.get("layoutParsingResults", [])
        if page_idx >= len(pages):
            return None

        table_results = pages[page_idx].get("prunedResult", {}).get("table_res_list", []) or []
        if not table_results:
            return None

        selected = self._select_best_paddle_table(box, table_results)
        if not selected:
            return None

        return self._table_rows_from_html(str(selected.get("pred_html", "") or ""))

    def _select_best_paddle_table(self, box: LayoutBox, table_results: list[dict[str, Any]]) -> dict[str, Any] | None:
        if len(table_results) == 1:
            return table_results[0]

        target_bbox = self._normalize_bbox(box.get("coordinate"))
        if not target_bbox:
            return table_results[0]

        best_score = -1.0
        best_result: dict | None = None
        for candidate in table_results:
            candidate_bbox = self._candidate_bbox(candidate)
            score = self._bbox_iou(target_bbox, candidate_bbox) if candidate_bbox else 0.0
            if score > best_score:
                best_score = score
                best_result = candidate

        return best_result or table_results[0]

    @staticmethod
    def _table_rows_from_html(pred_html: str) -> list[list[object]] | None:
        html_text = pred_html.strip()
        if not html_text:
            return None

        try:
            tables = pd.read_html(StringIO(html_text), header=None, keep_default_na=False)
        except ValueError:
            return None

        rows: list[list[object]] = []
        for table in tables:
            filled = table.fillna("")
            rows.extend(filled.astype(str).values.tolist())
        return rows or None

    @staticmethod
    def _normalize_bbox(coordinate: object) -> tuple[float, float, float, float] | None:
        if not isinstance(coordinate, list | tuple) or len(coordinate) != 4:
            return None
        x0, y0, x1, y1 = coordinate
        return float(x0), float(y0), float(x1), float(y1)

    def _candidate_bbox(self, candidate: dict[str, Any]) -> tuple[float, float, float, float] | None:
        cells = candidate.get("cell_box_list") or []
        if not isinstance(cells, list) or not cells:
            return None

        xs0: list[float] = []
        ys0: list[float] = []
        xs1: list[float] = []
        ys1: list[float] = []
        for cell in cells:
            bbox = self._normalize_bbox(cell)
            if not bbox:
                continue
            x0, y0, x1, y1 = bbox
            xs0.append(x0)
            ys0.append(y0)
            xs1.append(x1)
            ys1.append(y1)

        if not xs0:
            return None
        return min(xs0), min(ys0), max(xs1), max(ys1)

    @staticmethod
    def _bbox_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b

        inter_x0 = max(ax0, bx0)
        inter_y0 = max(ay0, by0)
        inter_x1 = min(ax1, bx1)
        inter_y1 = min(ay1, by1)
        if inter_x1 <= inter_x0 or inter_y1 <= inter_y0:
            return 0.0

        inter_area = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
        a_area = max(0.0, (ax1 - ax0) * (ay1 - ay0))
        b_area = max(0.0, (bx1 - bx0) * (by1 - by0))
        denom = a_area + b_area - inter_area
        if denom <= 0:
            return 0.0
        return inter_area / denom

    def _write_excel(self, ctx: ProcessingContext, data: list[list[object]]) -> None:
        ws = ctx.workbook.create_sheet(f"Table_{ctx.excel_sheet_counter}")
        ctx.excel_sheet_counter += 1
        for row in data:
            clean_row = [cell if cell is not None else "" for cell in row]
            ws.append(clean_row)


def extract_pdf_to_excel_with_padding(
    pdf_path: str,
    raw_structures_data: dict[str, Any],
    output_excel_path: str,
    img_width: int,
    img_height: int,
    padding: int = 15,
) -> None:
    """
    Args:
        pdf_path: PDF文件路径
        raw_structures_data: PaddleOCR返回的原始JSON数据
        output_excel_path: 输出Excel的路径
        img_width: PaddleOCR识别时使用的图片宽度 (默认A4 200DPI左右，需根据实际情况调整)
        img_height: PaddleOCR识别时使用的图片高度
        padding: 外扩像素值 (Pixel)
    """

    # === Step 1 & 2: 提取、清洗、排序布局信息 ===
    # 结构：pages_boxes = [ [page0_box1, page0_box2], [page1_box1], ... ]
    pages_boxes: list[list[LayoutBox]] = []

    # 从 layoutParsingResults 中提取信息，通常它是按页码排序的，但为了保险我们显式按 page_idx 排序
    results = raw_structures_data.get("layoutParsingResults", [])
    # 假设 results 里没有 page_num 字段，或者索引即页码。通常 Paddle 返回 list 对应页序。
    # 这里我们直接遍历 list

    valid_labels = {"table", "paragraph_title", "seal"}

    for page_idx, page_data in enumerate(results):
        # 获取 layout_det_res
        layout_det = page_data.get("prunedResult", {}).get("layout_det_res", {})
        boxes_list = layout_det.get("boxes", [])

        # 1. 清洗：只保留指定 label
        filtered_boxes = [b for b in boxes_list if b.get("label") in valid_labels]

        # 2. 排序：根据 coordinate[1] (y0, top) 从小到大排序
        # coordinate 格式通常是 [x0, y0, x1, y1]
        filtered_boxes.sort(key=lambda x: x["coordinate"][1])

        # 注入页码信息，方便后续追踪
        for b in filtered_boxes:
            b["page_idx"] = page_idx
            b["children"] = []  # 用于存放跨页的后续表格

        pages_boxes.append(filtered_boxes)

    # === Step 3: 倒序分析与重构 (核心链表逻辑) ===
    # 从最后一页往前遍历到第二页 (index 1)
    for i in range(len(pages_boxes) - 1, 0, -1):
        curr_page_list = pages_boxes[i]
        prev_page_list = pages_boxes[i - 1]

        if not curr_page_list or not prev_page_list:
            continue

        curr_first = curr_page_list[0]
        prev_last = prev_page_list[-1]

        # 判断跨页逻辑：当前页第一个是 table，且上一页最后一个是 table
        if curr_first.get("label") == "table" and prev_last.get("label") == "table":
            # --- 动作：断开并连接 ---

            # 1. 从当前页列表中移除第一个元素 (pop)
            popped_box = curr_page_list.pop(0)

            # 2. 拼接到上一页最后一个元素的 children 中
            # 注意：如果 curr_first 本身已经吸收了下一页的表格（因为是倒序），
            # 把它加到 prev_last 的 children，自然就形成了链式结构

            # 这是一个 List[List] 结构吗？
            # 题目要求结构：[box0, box1, [box2, box3, box4]]
            # 我们可以这样约定：
            # 如果 box["children"] 非空，它就是一个跨页表格组的主节点
            # 它的 children 里的 box 是跨页的后续部分

            prev_last["children"].append(popped_box)
            # 注意：这里我们只 append 了 popped_box。
            # 如果 popped_box 里面已经有了 children (来自更后面的页)，
            # 这种递归结构 (Tree) 也是可以处理的，或者我们可以把 children 扁平化。
            # 为了方便 Step 4 处理，我们这里做一个扁平化处理：
            if popped_box["children"]:
                prev_last["children"].extend(popped_box["children"])
                popped_box["children"] = []  # 清空被吸收者的 children

    # 重构为最终的一维列表 (Linear Structure)
    final_layout_structure: list[LayoutBox | list[LayoutBox]] = []
    for p_list in pages_boxes:
        for box in p_list:
            # 如果有 children，说明它是跨页表格的头
            if box["children"]:
                # 构造 [head, child1, child2...] 结构
                group = [box] + box["children"]
                final_layout_structure.append(group)
            else:
                # 单个元素
                final_layout_structure.append(box)

    # === Step 4 & 5: 使用 padding 提取数据并输出 Excel ===
    wb = openpyxl.Workbook()
    # 移除默认 sheet
    if "Sheet" in wb.sheetnames:
        wb.remove(wb["Sheet"])

    sheet_counter = 1

    with fitz.open(pdf_path) as pdf:
        for item in final_layout_structure:
            # 区分是单个对象 还是 跨页表格列表
            if isinstance(item, list):
                # >>> 处理跨页表格 (List) <<<
                full_table_data = []
                box_group = item

                print(f"检测到跨页表格，共 {len(box_group)} 部分，正在合并...")

                for box in box_group:
                    # 提取单块数据
                    chunk_data = extract_data_from_box(pdf, box, img_width, img_height, padding)
                    if chunk_data:
                        full_table_data.extend(chunk_data)  # 逻辑拼接 List + List

                if full_table_data:
                    ws = wb.create_sheet(f"Table_{sheet_counter}")
                    write_data_to_sheet(ws, full_table_data)
                    sheet_counter += 1

            else:
                # >>> 处理单个对象 (Dict) <<<
                box = item
                label = box.get("label")

                if label == "table":
                    table_data = extract_data_from_box(pdf, box, img_width, img_height, padding)
                    if table_data:
                        ws = wb.create_sheet(f"Table_{sheet_counter}")
                        write_data_to_sheet(ws, table_data)
                        sheet_counter += 1

                elif label == "paragraph_title":
                    # 这里你可以选择打印日志，或者写入一个专门的 Sheet
                    # 为了演示，我们只打印
                    print(f"跳过段落标题: Page {box['page_idx']}")
                    pass

                elif label == "seal":
                    print(f"跳过印章: Page {box['page_idx']}")
                    pass

    wb.save(output_excel_path)
    print(f"Excel 已保存至: {output_excel_path}")


def extract_data_from_box(
    pdf_obj: Any,
    box_dict: LayoutBox,
    img_w: int,
    img_h: int,
    padding_px: int,
) -> list[list[object]] | None:
    """
    根据 Paddle Box 坐标，应用 Padding，裁剪 PDF 并提取表格数据
    """
    page_idx = box_dict["page_idx"]
    coord = box_dict["coordinate"]  # [x0, y0, x1, y1] pixel

    page = pdf_obj[page_idx]
    pdf_w = page.rect.width
    pdf_h = page.rect.height

    # 1. 计算缩放比例 (PDF Point / Image Pixel)
    scale_x = pdf_w / img_w
    scale_y = pdf_h / img_h

    # 2. 应用 Padding (在 Pixel 层面扩充，或者在 Point 层面扩充均可，这里在 Pixel 扩充)
    # 注意不要超出图片边界
    px_x0 = max(0, coord[0] - padding_px)
    px_y0 = max(0, coord[1] - padding_px)
    px_x1 = min(img_w, coord[2] + padding_px)
    px_y1 = min(img_h, coord[3] + padding_px)

    # 3. 坐标映射 (Pixel -> Point)
    crop_x0 = px_x0 * scale_x
    crop_y0 = px_y0 * scale_y
    crop_x1 = px_x1 * scale_x
    crop_y1 = px_y1 * scale_y

    clip = fitz.Rect(crop_x0, crop_y0, crop_x1, crop_y1)

    try:
        # 使用 PyMuPDF 提取表格
        tabs = page.find_tables(clip=clip)
        if tabs.tables:
            return _coerce_table_rows(tabs[0].extract())
        return None
    except Exception:
        print(f"Crop warning on page {page_idx}")
        return None


def write_data_to_sheet(worksheet: Worksheet, data: list[list[object]]) -> None:
    """
    将 list of lists 写入 openpyxl sheet
    """
    if not data:
        return
    for row in data:
        # PyMuPDF 提取出的 None 转为空字符串
        clean_row = [cell if cell is not None else "" for cell in row]
        worksheet.append(clean_row)


# === 使用示例 ===
if __name__ == "__main__":
    # 模拟数据结构进行测试
    # 实际使用时，请传入真实的 json.load(open(...))

    # 假设你已经有了 raw_structures_data
    # raw_data = ...

    # path = "your_file.pdf"
    # extract_pdf_to_excel_with_padding(path, raw_data, "output.xlsx")
    pass
