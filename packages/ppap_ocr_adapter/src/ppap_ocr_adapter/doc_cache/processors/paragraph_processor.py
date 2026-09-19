import os
from typing import Any

import fitz  # PyMuPDF
from PIL import Image

from .base_processor import BaseProcessor, ProcessingContext


class ParagraphProcessor(BaseProcessor):
    """
    处理复杂段落 (Paragraph Title)。
    如果发现某页有 paragraph_title，则对该页进行图像处理：
    1. 渲染该页为图片。
    2. 查找该页原始的 Table 区域，并将其“裁掉”。
    3. 拼接剩余部分。
    4. 加入待处理列表。
    """

    def process(self, ctx: ProcessingContext) -> None:
        if not ctx.layout_structure:
            return

        print(">>> [Chain] ParagraphProcessor Start...")

        # 1. 找出包含 paragraph_title 的页码
        target_pages = set()
        for item in ctx.layout_structure:
            if isinstance(item, dict) and item.get("label") == "paragraph_title":
                page_idx = item.get("page_idx")
                if isinstance(page_idx, int):
                    target_pages.add(page_idx)

        if not target_pages:
            print("  No paragraph titles found. Skipping visual processing.")
            return

        print(f"  Pages requiring visual processing: {target_pages}")

        # 2. 准备原始 Table 布局信息的索引 (Page -> List of Table Boxes)
        # 我们需要回溯 raw_structures_data，因为 final_layout_structure 里的 table 已经被弹出了
        raw_tables_map: dict[int, list[dict[str, Any]]] = {}
        if "layoutParsingResults" in ctx.raw_data:
            for page_res in ctx.raw_data["layoutParsingResults"]:
                # 假设 Paddle 返回列表索引就是页码，或者解析 page_num
                # 这里为了稳健，尝试获取 page_num，如果没有则用遍历索引
                # 注意：raw_structures_data 的结构要和你之前的解析一致
                # 这里简化处理，假设我们能通过某种方式对应上 page_idx
                pass

        # 更简单的办法：直接再次解析一遍 raw_data 只取 table
        # 实际工程中建议在 Context 初始化时就建立好这个 Map
        raw_tables_map = self._build_raw_table_map(ctx.raw_data)

        # 3. 准备 Debug 目录
        debug_dir = "tmp"
        os.makedirs(debug_dir, exist_ok=True)

        # 4. 处理每一页
        doc = fitz.open(ctx.pdf_path)

        for page_idx in target_pages:
            print(f"  Processing visual layout for Page {page_idx}...")

            # A. 渲染页面为图片
            page = doc[page_idx]
            # 计算缩放比例以匹配 img_w (Paddle 识别时的宽度)
            # PyMuPDF 默认 72 DPI。如果 img_w 是 1654 (约 200 DPI)，需要 zoom
            zoom = ctx.img_w / page.rect.width
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

            # B. 获取该页所有的 Table 区域 (原始坐标)
            tables_on_page = raw_tables_map.get(page_idx, [])

            # C. 图像切割与拼接
            processed_img = self._cut_and_stitch(img, tables_on_page)

            if processed_img:
                # D. 保存 Debug 图片
                debug_filename = f"debug_stitched_page_{page_idx}.png"
                debug_path = os.path.join(debug_dir, debug_filename)

                try:
                    processed_img.save(debug_path)
                    print(f"  🐛 [Debug] Stitched image saved to: {debug_path}")
                except Exception as e:
                    print(f"  ⚠️ Failed to save debug image: {e}")

                # E. 加入待 Gemma 处理列表
                ctx.images_for_gemma.append(
                    {
                        "page_idx": page_idx,
                        "image": processed_img,
                        "debug_path": debug_path,  # 顺便存个路径，方便后续追踪
                    }
                )
            else:
                print(f"  Page {page_idx} became empty after stitching (Full page table?), skipping.")

        doc.close()
        # 注意：这里不需要更新 ctx.layout_structure，因为这是链条的末端逻辑，
        # 或者你可以清空它表示处理完毕。

    def _build_raw_table_map(self, raw_data: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
        """辅助：从原始数据中提取所有 Table 的坐标"""
        mapping: dict[int, list[dict[str, Any]]] = {}
        results = raw_data.get("layoutParsingResults", [])
        for idx, page_data in enumerate(results):
            boxes = page_data.get("prunedResult", {}).get("layout_det_res", {}).get("boxes", [])
            # 过滤出 Table
            tables = [b for b in boxes if b.get("label") == "table"]
            # 排序 (Y轴)
            tables.sort(key=lambda x: x["coordinate"][1])
            mapping[idx] = tables
        return mapping

    def _cut_and_stitch(self, pil_image: Image.Image, table_boxes: list[dict[str, Any]]) -> Image.Image | None:
        """
        核心视觉逻辑：传入一张图片和若干个要挖掉的 Table 盒子。
        返回拼接后的图片。
        """
        if not table_boxes:
            return pil_image

        w, h = pil_image.size

        # 1. 整理切割线 (Y轴)
        # 我们需要保留的区域是：[0, t1_y0], [t1_y1, t2_y0], [t2_y1, h]
        # table_boxes 里的 coordinate 是 [x0, y0, x1, y1]

        cut_intervals = []
        for box in table_boxes:
            # 稍微扩一点点切掉，或者严格切掉，取决于需求。这里严格切。
            y0 = max(0, int(box["coordinate"][1]))
            y1 = min(h, int(box["coordinate"][3]))
            cut_intervals.append((y0, y1))

        # 合并重叠的区间 (以防万一)
        cut_intervals.sort()
        merged_cuts = []
        if cut_intervals:
            curr_start, curr_end = cut_intervals[0]
            for next_start, next_end in cut_intervals[1:]:
                if next_start < curr_end:  # 重叠
                    curr_end = max(curr_end, next_end)
                else:
                    merged_cuts.append((curr_start, curr_end))
                    curr_start, curr_end = next_start, next_end
            merged_cuts.append((curr_start, curr_end))

        # 2. 生成保留区域
        keep_intervals = []
        current_y = 0
        for cut_start, cut_end in merged_cuts:
            if cut_start > current_y:
                keep_intervals.append((current_y, cut_start))
            current_y = max(current_y, cut_end)
        if current_y < h:
            keep_intervals.append((current_y, h))

        # 3. 裁剪并拼接
        slices = []
        total_height = 0
        for start_y, end_y in keep_intervals:
            # crop: (left, top, right, bottom)
            # 切下整个宽度的条
            part = pil_image.crop((0, start_y, w, end_y))
            slices.append(part)
            total_height += end_y - start_y

        if total_height == 0:
            return None  # 极端情况：整页都是表格

        # 创建新画布
        new_img = Image.new("RGB", (w, total_height), (255, 255, 255))
        current_paste_y = 0
        for part in slices:
            new_img.paste(part, (0, current_paste_y))
            current_paste_y += part.height

        return new_img
