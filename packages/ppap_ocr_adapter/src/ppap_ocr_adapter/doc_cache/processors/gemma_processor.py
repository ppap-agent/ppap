import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any

from ..llm_service import call_llm
from ..prompt_factory import PromptFactory
from .base_processor import BaseProcessor, ProcessingContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class GemmaProcessor(BaseProcessor):
    """
    视觉处理节点：负责处理 ParagraphProcessor 生成的拼接图片。
    核心能力：
    1. 接收拼接后的图片（已去除表格干扰）。
    2. 接收 PaddleOCR 的原始文本流（去除表格内容，按人类阅读顺序重排）。
    3. 结合两者，让 Gemma 3 提取剩余的关键信息（段落标题、KV值、总结）。
    """

    def process(self, ctx: ProcessingContext) -> None:
        # 如果没有待处理图片，直接结束
        if not ctx.images_for_gemma:
            print(">>> [Chain] GemmaProcessor: No images to process.")
            return

        print(f">>> [Chain] GemmaProcessor Start... Processing {len(ctx.images_for_gemma)} images.")

        # 预先构建页码到 raw_data 的映射，方便查找 parsing_res_list
        # 假设 raw_structures_data["layoutParsingResults"] 的顺序与页码对应
        raw_results = ctx.raw_data.get("layoutParsingResults", [])
        raw_results_list = raw_results if isinstance(raw_results, list) else []

        # 结果容器，按索引占位，保证顺序
        results: list[str | None] = [None] * len(ctx.images_for_gemma)

        # 获取并发数配置
        max_workers = int(os.getenv("CONCURRENT_SIZE", "5"))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx: dict[Future[str], int] = {}

            for idx, item in enumerate(ctx.images_for_gemma):
                # 提交任务
                future = executor.submit(self._process_single_page, item, raw_results_list)
                future_to_idx[future] = idx

            # 收集结果
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    page_html = future.result()
                    results[idx] = page_html
                except Exception as e:
                    print(f"  ❌ Error processing image {idx}: {e}")
                    results[idx] = f"<!-- Error processing page {idx}: {e} -->"

        # 4. 拼接并保存到 HTML 文件
        # 如果 results 为空，什么都不做
        valid_results = [result for result in results if result]
        if not valid_results:
            return

        full_html_content = "\n<hr class='page-break'>\n".join(valid_results)

        try:
            with open(ctx.output_html_path, "a", encoding="utf-8") as f:
                # 加个分隔符，把印章和正文分开
                f.write("\n<!-- Gemma Vision Content Below -->\n")
                f.write(full_html_content)
            print(f"  ✅ Gemma HTML content appended to: {ctx.output_html_path}")
        except Exception as e:
            print(f"  ❌ Failed to append HTML file: {e}")

    def _process_single_page(self, item: dict[str, Any], raw_results_list: list[Any]) -> str:
        """
        处理单张图片的原子逻辑 (运行在子线程中)
        """
        page_idx = item["page_idx"]
        debug_path = item.get("debug_path")  # 之前保存的临时文件路径

        print(f"  Processing Page {page_idx} with Gemma 3 Vision...")

        # 1. 获取该页的原始 OCR 文本结果
        if page_idx < len(raw_results_list):
            page_raw_data = raw_results_list[page_idx]
            # parsing_res_list 包含具体的文本块和坐标
            parsing_res_list = page_raw_data.get("prunedResult", {}).get("parsing_res_list", [])
        else:
            parsing_res_list = []
            print(f"    ⚠️ Warning: No raw parsing data found for Page {page_idx}")

        # 2. 生成辅助文本 Prompt (清洗 + 排序)
        ocr_context = self._generate_ocr_context(parsing_res_list)

        # 4. 调用 Gemma 3
        # 我们直接使用 ParagraphProcessor 保存的 debug_path (临时文件)
        if debug_path and os.path.exists(debug_path):
            try:
                # 1. 静态规则分析
                tags: set[str] = set()
                tag_hints: dict[str, str] = {}

                # 2. Gemma 视觉 Tagging (只看图，输出 JSON)
                tagging_prompt = PromptFactory.get_tagging_prompt()
                try:
                    # 注意：这里我们只传 debug_path (单张图)
                    vision_tagging_res = call_llm(
                        image_paths=[debug_path],  # 传入单字符串
                        prompt=tagging_prompt,
                        retry_count=2,
                        json_mode=True,
                        response_schema=TAGGING_SCHEMA,
                    )

                    if isinstance(vision_tagging_res, dict):
                        if vision_tagging_res.get("has_checkboxes"):
                            tags.add("HAS_CHECKBOX")
                        if vision_tagging_res.get("two_column_split"):
                            tags.add("TWO_COLUMN")
                        if vision_tagging_res.get("has_handwriting"):
                            tags.add("HAS_HANDWRITING")
                        if vision_tagging_res.get("has_merged_row_number"):
                            tags.add("HAS_MERGED_ROW_NUMBER")

                        logger.info(f"Page {os.path.basename(debug_path)} Tags: {tags}")

                except Exception as e:
                    logger.warning(f"Tagging failed for {debug_path}: {e}")
                    # 出错不中断，带上静态标签继续跑

                # 3. Gemma 内容提取 (看图 + 动态Prompt)
                extraction_prompt = PromptFactory.get_extraction_prompt(tags=tags, tag_hints=tag_hints, tagging_desc=TAGGING_DESCRIPTIONS, merged_ocr_context=ocr_context)

                final_html = call_llm(
                    image_paths=[debug_path],  # 传入单字符串
                    prompt=extraction_prompt,
                    retry_count=3,
                    json_mode=False,
                )
                return final_html if isinstance(final_html, str) else "<!-- Gemma returned non-text response -->"

            except Exception as e:
                print(f"  ❌ Gemma processing failed for Page {page_idx}: {e}")
                return f"<!-- Gemma processing failed for page {page_idx} -->"
        else:
            print(f"  ❌ Image path not found: {debug_path}")
            return f"<!-- Image path missing for page {page_idx} -->"

    def _generate_ocr_context(self, parsing_res_list: list[dict[str, Any]]) -> str:
        """
        根据 Paddle 的 parsing_res_list 生成结构化的 OCR 上下文。
        核心：基于 bbox 进行自然阅读顺序排序 (Top-Down, Left-Right)。
        """
        if not parsing_res_list:
            return ""

        # A. 过滤：去除 table 内容 (已单独处理，不留为干扰)
        # block_label 可能的值：header, footer, table, figure, text, paragraph_title, seal...
        filtered_blocks = [b for b in parsing_res_list if b.get("block_label") != "table"]

        if not filtered_blocks:
            return "【OCR提示】本页无额外文本信息。"

        # B. 排序逻辑 (复用你提供的逻辑)
        # 定义排序键：允许 Y 轴有一定的容差 (例如 20 像素)，视为同一行
        def sort_key(block: dict[str, Any]) -> tuple[int, object]:
            # block_bbox 格式通常为 [x_min, y_min, x_max, y_max]
            bbox = block.get("block_bbox", [0, 0, 0, 0])
            if not isinstance(bbox, list | tuple) or len(bbox) < 2:
                return (0, 0)
            y_min = bbox[1]
            x_min = bbox[0]
            # 容差设为 20px，将 Y 坐标整除 20，这样相近的 Y 就会被归为同一档
            # 然后再按准确的 X 坐标排序
            return (int(y_min / 20), x_min)

        # 执行排序
        sorted_blocks = sorted(filtered_blocks, key=sort_key)

        # C. 生成 Prompt 描述
        lines = []
        lines.append("\n【📊 页面视觉流分析 (Visual OCR Context)】")
        lines.append("以下是图片中对应的文本流（按自然阅读顺序）：")

        for idx, block in enumerate(sorted_blocks):
            label = block.get("block_label", "text")  # 默认 text
            content = block.get("block_content", "").strip()

            # 简单清洗一下 content，去掉过多的换行
            content = content.replace("\n", " ")

            # 构造描述行
            # 格式: [序号] <类型> : 内容
            line = f"{idx + 1}. <{label.upper()}> {content}"

            # 特殊高亮：如果是段落标题或印章
            if label == "paragraph_title":
                line = f"**{line}** (段落标题)"
            elif label == "seal":
                line += " [检测到印章]"

            lines.append(line)

        return "\n".join(lines)


# 定义 Tagging 的 Schema (JSON Schema)
TAGGING_DESCRIPTIONS = {
    "has_checkboxes": {"type": "boolean", "description": "True if checkboxes (square/ticked boxes) or 'Yes/No' options are visible. False otherwise."},
    "two_column_split": {"type": "boolean", "description": "True ONLY if the page layout is visually split into distinct left and right columns. False for standard tables."},
    "has_handwriting": {"type": "boolean", "description": "True if handwritten signatures or text are present."},
    "has_merged_row_number": {
        "type": "boolean",
        "description": "True ONLY if a table exists where the first column (Index/No.) has merged cells spanning multiple data rows. False for standard one-to-one rows.",
    },
}

TAGGING_SCHEMA = {
    "name": "document_analysis",
    "strict": True,
    "schema": {"type": "object", "properties": TAGGING_DESCRIPTIONS, "required": ["has_checkboxes", "two_column_split", "has_handwriting", "has_merged_row_number"], "additionalProperties": False},
}
