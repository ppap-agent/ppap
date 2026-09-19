from pathlib import Path
from typing import Any

from .base_processor import BaseProcessor, LayoutBox, ProcessingContext


class SealProcessor(BaseProcessor):
    """
    处理印章 (Seal)。
    目前仅做弹出操作。
    """

    def process(self, ctx: ProcessingContext) -> None:
        if not ctx.layout_structure:
            return

        print(">>> [Chain] SealProcessor Start...")
        remaining_structure: list[LayoutBox | list[LayoutBox]] = []
        seals_found: list[str] = []

        # 1. 遍历并提取 Seal
        for item in ctx.layout_structure:
            # Seal 不会是 list (跨页组)，只会是 dict
            if isinstance(item, dict) and item.get("label") == "seal":
                page_idx = item.get("page_idx")
                print(f"  Found Seal on Page {page_idx}")

                # 尝试获取印章文字
                # 策略：从 raw_data 的 seal_res_list 中查找该页的印章结果
                seal_text = self._find_seal_text(ctx, page_idx, item)
                seals_found.append(seal_text)

                # 标记为已处理（不加入 remaining），即弹出
                continue

            remaining_structure.append(item)

        # 2. 如果找到了印章，追加写入 HTML
        if seals_found:
            self._append_to_html(ctx.output_html_path, seals_found)

        # 3. 更新 Context
        ctx.layout_structure = remaining_structure

    def _find_seal_text(self, ctx: ProcessingContext, page_idx: Any, seal_box: LayoutBox) -> str:
        """
        尝试从原始数据中找到印章文字。
        Paddle 结构通常在 prunedResult -> seal_res_list 中。
        """
        try:
            # 获取该页原始数据
            results = ctx.raw_data.get("layoutParsingResults", [])
            if page_idx < len(results):
                page_data = results[page_idx]
                seal_res_list = page_data.get("prunedResult", {}).get("seal_res_list", [])

                # 简单策略：如果该页有 seal_res_list，直接拼接所有印章文字
                # 进阶策略：计算 seal_box 和 seal_res_list 中坐标的 IoU 来精确匹配
                texts = []
                for res in seal_res_list:
                    if "rec_texts" in res:
                        # rec_texts 是一个列表 (e.g. ["XX专用章", "2024..."])
                        texts.extend(res["rec_texts"])

                if texts:
                    return f"Page {page_idx + 1} 印章: {' '.join(texts)}"
        except Exception as e:
            print(f"  Error extracting seal text: {e}")

        return f"Page {page_idx + 1} 检测到印章 (内容未识别)"

    def _append_to_html(self, html_path: str | Path, seal_texts: list[str]) -> None:
        """追加模式写入 HTML"""
        html_snippet = '<div class="seal-section" style="color:red; border:1px solid red; padding:10px; margin:10px 0;">\n'
        html_snippet += "  <h4>🛑 印章/签署信息 (Seal/Signature)</h4>\n"
        html_snippet += "  <ul>\n"
        for text in seal_texts:
            html_snippet += f"    <li>{text}</li>\n"
        html_snippet += "  </ul>\n"
        html_snippet += "</div>\n"

        try:
            # 关键点：使用 'a' (append) 模式
            with open(html_path, "a", encoding="utf-8") as f:
                f.write(html_snippet)
            print(f"  ✅ Seals appended to HTML: {html_path}")
        except Exception as e:
            print(f"  ❌ Failed to append seals to HTML: {e}")
