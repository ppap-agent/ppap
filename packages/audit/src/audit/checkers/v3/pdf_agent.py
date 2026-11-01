"""
PDF 文件专用审核代理基类

在 BaseAgent 和具体的 PDF 文档审核代理之间提供专门的 PDF 文件处理功能。
所有处理 PDF 文件的 PPAP 审核代理都应该继承此类。
"""

import base64
import io
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from documents.contracts import ConversionArtifact, FileRef
from documents.conversion import convert_pdf_to_excel_auto
from documents.extractors import PDFExtractor
from documents.files import detect_file_type, download_file_sync
from documents.images import extract_images_from_bytes
from PIL import Image

from audit.checkers.v2.base_agent import BaseAgent
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.contracts import AuditResultItem as AuditResult
from audit.engine.checkers.client import AIClient

logger = logging.getLogger(__name__)
RuleChecker = Callable[[int], Optional[AuditResult]]


class PDFAgent(BaseAgent):
    """
    PDF文件专用审核代理基类

    提供了处理PDF文件的通用功能，包括：
    - 将PDF页面同步渲染为Base64编码的图像序列，并进行缓存。
    - 对单张图像进行裁剪。
    - 封装了针对图像的AI分析流程。
    - 提供了AI提示链式调用的能力。
    """

    def __init__(
        self,
        audit_file: AuditInput,
        rule: RuleInput,
        group_context: GroupContextInput | None = None,
    ) -> None:
        """
        初始化PDF代理

        Args:
            audit_file: 要审核的文件对象。
            rule: 审核规则对象。
        """
        super().__init__(audit_file, rule, group_context=group_context)
        self.rule_checkers: List[Optional[RuleChecker]] = []
        self.agent_rules = []
        self.client = AIClient()
        self.pdf_extractor = PDFExtractor()
        self.temp_files: List[str] = []
        self._pdf_images_cache: Optional[List[str]] = None
        self._pdf_page_texts_cache: Optional[List[str]] = None
        self._pdf_page_ocr_texts_cache: Optional[List[str]] = None
        self._pdf_file_content_cache: Optional[bytes] = None
        self.conversion_artifact: Optional[ConversionArtifact] = None

    def _register_temp_file(self, file_path: Optional[str]) -> None:
        """Register a temp file path for cleanup on object destruction."""
        if file_path:
            self.temp_files.append(file_path)

    def _cleanup_temp_files(self) -> None:
        """Best-effort temp file cleanup."""
        import os

        for file_path in self.temp_files:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.debug(f"已清理临时文件: {file_path}")
                except OSError as e:
                    logger.error(f"清理临时文件 {file_path} 失败: {e}")

    def __del__(self) -> None:
        self._cleanup_temp_files()

    def check(self, ith: int) -> Optional[AuditResult]:
        """
        执行第ith条规则的审核。
        这是代理的统一入口点，它会根据规则索引分派到相应的检查方法。
        如果没有匹配到专门的检查方法，则调用父类的AI审核逻辑。
        """
        self.log_audit_start(ith)

        # 检查是否有对应的专门检查方法
        checker_func = self.rule_checkers[ith] if ith < len(self.rule_checkers) else None

        if not checker_func:
            logger.debug(f"规则 {ith + 1} 没有对应的专门检查方法，使用父类AI审核逻辑。")
            # 调用父类的check方法进行AI审核
            return super().check(ith)

        # 如果有专门的检查方法，验证文件格式并执行检查
        if self._is_pdf_file() or self._is_image_file():
            self.images = self._get_pdf_images_sync()
        else:
            logger.warning(f"审核文件 {self.audit_file.file_url} 不是PDF/图像格式，回退到AI审核。")
            # 回退到父类的AI审核逻辑
            return super().check(ith)

        # 使用专门的PDF检查方法
        try:
            result = checker_func(ith)
            if result is None:
                return None
            self.log_audit_result(result, ith)
            return result
        except Exception as e:
            logger.error(f"PDF专门检查规则 {ith + 1} 时发生错误: {e}", exc_info=True)
            logger.debug("PDF检查失败，回退到AI审核逻辑")
            # 发生异常时回退到父类的AI审核逻辑
            return super().check(ith)

    def _is_pdf_file(self) -> Any:
        """
        检查当前审核文件是否为PDF格式。

        Returns:
            bool: 如果是PDF文件，返回True；否则返回False。
        """
        file_url = getattr(self.audit_file, "file_url", "")
        if file_url:
            from documents.files import detect_file_type_from_filename

            detected_type = detect_file_type_from_filename(file_url)
            if detected_type.lower() == ".pdf":
                return True

        return False

    def _is_image_file(self) -> Any:
        """
        检查当前审核文件是否为图像格式。

        Returns:
            bool: 如果是图像文件，返回True；否则返回False。
        """
        file_url = getattr(self.audit_file, "file_url", "")
        if file_url:
            from documents.files import detect_file_type_from_filename

            detected_type = detect_file_type_from_filename(file_url)
            return detected_type.lower() in [".jpg", ".jpeg", ".png", ".gif"]

        return False

    def _get_pdf_file_content(self) -> bytes:
        """
        获取PDF文件内容，带缓存

        Returns:
            PDF文件的二进制内容
        """
        if self._pdf_file_content_cache is not None:
            return self._pdf_file_content_cache

        try:
            file_url = str(self.audit_file.file_url)

            # 检查是否为本地文件路径
            if file_url.startswith("http://") or file_url.startswith("https://"):
                # 远程文件，使用下载函数
                file_content, _ = download_file_sync(file_url, max_file_size=100 * 1024 * 1024)  # 100MB limit
            else:
                # 本地文件，直接读取
                file_path = Path(file_url)
                if not file_path.exists():
                    raise FileNotFoundError(f"本地文件不存在: {file_url}")

                # 检查文件大小限制
                file_size = file_path.stat().st_size
                max_size = 100 * 1024 * 1024  # 100MB limit
                if file_size > max_size:
                    raise ValueError(f"文件太大，超过了{max_size}字节的限制")

                with open(file_path, "rb") as f:
                    file_content = f.read()

            self._pdf_file_content_cache = file_content
            return file_content
        except Exception as e:
            logger.error(f"获取PDF文件内容失败: {e}")
            return b""

    def _convert_pdf_to_excel(self) -> Optional[str]:
        return self._convert_pdf_to_excel_pipeline()

    def _convert_pdf_to_excel_pipeline(self) -> Optional[str]:
        file_url = str(self.audit_file.file_url)
        from audit.engine.runtime import get_runtime
        runtime = get_runtime()
        if runtime is not None:
            artifact = runtime.convert_pdf_to_excel(
                FileRef(location=file_url),
                enable_cache=True,
            )
        else:
            artifact = convert_pdf_to_excel_auto(
                FileRef(location=file_url),
                enable_cache=True,
            )
        self.conversion_artifact = artifact
        if not artifact.success or not artifact.xlsx_path:
            logger.error("统一 PDF->Excel 管线转换失败: %s", artifact.diagnostics)
            return None

        # Keep non-xlsx temp files managed by PDFAgent lifecycle.
        for temp_path in artifact.temp_files:
            if temp_path != artifact.xlsx_path:
                self._register_temp_file(temp_path)
        logger.debug(
            "通过统一管线成功转换 PDF→Excel: strategy=%s, path=%s",
            artifact.strategy_used,
            artifact.xlsx_path,
        )
        return artifact.xlsx_path

    def _get_paged_pdf_content(self) -> list[bytes]:
        """
        使用fitz提取PDF每一页内容并返回为bytes列表

        Returns:
            list[bytes]: 每一页PDF的二进制内容列表
        """
        full_pdf_content = self._get_pdf_file_content()
        return self.pdf_extractor.extract_pages_content(full_pdf_content)

    def _get_pdf_page_texts(self) -> List[str]:
        """
        提取PDF的每页文本内容，带缓存

        Returns:
            每页文本内容的列表
        """
        if self._pdf_page_texts_cache is not None:
            logger.debug("从缓存中获取PDF页面文本。")
            return self._pdf_page_texts_cache

        try:
            file_content = self._get_pdf_file_content()
            if not file_content:
                logger.warning("无法获取PDF文件内容")
                return []
            page_texts = self.pdf_extractor.extract_pages_text(file_content)
            self._pdf_page_texts_cache = page_texts
            logger.debug(f"成功提取PDF的 {len(page_texts)} 页文本内容")
            return page_texts

        except Exception as e:
            logger.error(f"提取PDF页面文本失败: {e}")
            return []

    def _ocr_pdf_page_texts(self) -> List[str]:
        """
        提取PDF的每页文本内容，带缓存

        Returns:
            每页文本内容的列表
        """
        if self._pdf_page_ocr_texts_cache is not None:
            logger.debug("从缓存中获取PDF页面文本。")
            return self._pdf_page_ocr_texts_cache

        try:
            file_content = self._get_pdf_file_content()
            if not file_content:
                logger.warning("无法获取PDF文件内容")
                return []
            images = self._get_pdf_images_sync()
            logger.debug(f"PDFAgent: 开始 OCR PDF 页面文本内容... 共 {len(images)} 页")

            def _ocr_page_with_ai_client(image_base64: str) -> Optional[str]:
                from audit.engine.runtime import get_runtime
                runtime = get_runtime()
                if runtime is not None:
                    return runtime.run_image_prompt(
                        system_prompt="",
                        user_text=("extract text from the image, organized into exact text based on the table structure"),
                        image_base64_list=[image_base64],
                        temperature=1.0,
                    )
                return self.client.image_analysis_completion(
                    system_prompt="",
                    user_text=("extract text from the image, organized into exact text based on the table structure"),
                    image_base64_list=[image_base64],
                    temperature=1.0,
                    operation="ocr_pdf_page_text",
                )

            self._pdf_page_ocr_texts_cache = self.pdf_extractor.ocr_pages_text(
                images,
                ocr_page_callable=_ocr_page_with_ai_client,
            )
            logger.debug(f"PDFAgent: 成功 OCR PDF 的 {len(self._pdf_page_ocr_texts_cache)} 页文本内容")
            return self._pdf_page_ocr_texts_cache

        except Exception as e:
            logger.error(f"PDFAgent: OCR PDF页面文本失败: {e}")
            return []

    def _find_target_pages(
        self,
        target_keywords: List[str],
        strategies: List[Callable[[List[str]], Dict[str, int]]],
    ) -> Dict[str, int]:
        """
        在PDF页面中查找包含目标关键词的页面

        Args:
            target_keywords: 要查找的关键词列表，如 ["检具清单", "检具认可报告"]
            fns: 一系列查找函数，按优先级顺序执行

        Returns:
            Dict[str, int]: 关键词到页面索引的映射 (0-based)
        """
        found_pages: Dict[str, int] = {}
        for fn in strategies:
            found_pages.update(fn(target_keywords))
            target_keywords = [target for target in target_keywords if target not in found_pages]

        return found_pages

    def _find_target_pages_by_text(self, target_keywords: List[str], page_texts: List[str]) -> Dict[str, int]:
        """
        在PDF页面文本中查找包含目标关键词的页面

        Args:
            target_keywords: 要查找的关键词列表，如 ["检具清单", "检具认可报告"]
            page_texts: PDF每页的文本内容列表

        Returns:
            Dict[str, int]: 关键词到页面索引的映射 (0-based)
        """
        if not page_texts:
            return {}

        found_pages = {}

        for page_idx, page_text in enumerate(page_texts):
            # 记录每页的文本长度用于诊断
            logger.debug(f"第 {page_idx + 1} 页文本长度: {len(page_text)} 字符")

            for keyword in target_keywords:
                if keyword in page_text:
                    found_pages[keyword] = page_idx
                    logger.debug(f"在第 {page_idx + 1} 页找到关键词: '{keyword}'")
                    # 显示关键词上下文
                    keyword_pos = page_text.find(keyword)
                    context_start = max(0, keyword_pos - 50)
                    context_end = min(len(page_text), keyword_pos + len(keyword) + 50)
                    context = page_text[context_start:context_end].replace("\n", " ")
                    logger.debug(f"关键词 '{keyword}' 上下文: ...{context}...")

        if not found_pages:
            logger.warning(f"未找到任何目标关键词: {target_keywords}，将检查所有页面作为后备方案")
            # 提供一些诊断信息
            for page_idx, page_text in enumerate(page_texts):
                if len(page_text.strip()) > 0:
                    # 显示每页前100个字符作为诊断
                    preview = page_text.strip()[:100].replace("\n", " ")
                    logger.debug(f"第 {page_idx + 1} 页内容预览: {preview}...")

        return found_pages

    def _find_target_pages_by_textboxes(self, target_keywords: List[str]) -> Dict[str, int]:
        """
        使用文本框分析在PDF页面中查找包含目标关键词的页面
        这种方法能更精确地定位文本位置，特别适用于表格和结构化文档

        Args:
            target_keywords: 要查找的关键词列表，如 ["检具清单", "检具认可报告"]

        Returns:
            Dict[str, int]: 关键词到页面索引的映射 (0-based)
        """
        file_content = self._get_pdf_file_content()
        if not file_content:
            logger.warning("无法获取PDF文件内容")
            return {}

        no_whitespace = True  # 忽略字之间的空白，例如 “空 白 字”
        search_results = self.pdf_extractor.search_text_in_boxes(
            file_content,
            target_keywords,
            case_sensitive=False,
            no_whitespace=no_whitespace,
        )

        found_pages = {}
        for keyword, text_boxes in search_results.items():
            if text_boxes:
                # 取第一个匹配的文本框所在的页面
                first_match = text_boxes[0]
                found_pages[keyword] = first_match.page
                logger.debug(f"在第 {first_match.page + 1} 页的文本框中找到关键词: '{keyword}'")
                logger.debug(f"文本框位置: ({first_match.x0:.1f}, {first_match.y0:.1f}, {first_match.x1:.1f}, {first_match.y1:.1f})")
                logger.debug(f"文本框内容预览: {first_match.text[:100]}...")

        not_found_targets = [target for target in target_keywords if target not in found_pages]
        if len(not_found_targets) > 0:
            logger.warning(f"by_textboxes 未找到以下目标关键词: {', '.join(not_found_targets)}")

        return found_pages

    def _get_specific_page_images(self, page_indices: List[int]) -> List[str]:
        """
        获取指定页面的图像

        Args:
            page_indices: 页面索引列表 (0-based)

        Returns:
            List[str]: 指定页面的base64编码图像列表
        """
        try:
            file_content = self._get_pdf_file_content()
            if not file_content:
                return []

            # 使用enhanced image_utils function with specific_pages parameter
            images = extract_images_from_bytes(file_content, ".pdf", specific_pages=page_indices)

            logger.debug(f"成功获取 {len(images)} 个指定页面的图像")
            return images

        except Exception as e:
            logger.error(f"获取指定页面图像失败: {e}")
            return []

    def _analyze_pdf_structure(self) -> Dict[str, Any]:
        """
        分析PDF结构，包括文本框、表格边框等信息
        这对于检测表格结构和验证文档完整性很有用

        Returns:
            Dict[str, Any]: PDF结构分析结果
        """
        try:
            file_content = self._get_pdf_file_content()
            if not file_content:
                return {}

            structure = self.pdf_extractor.analyze_pdf_structure(file_content)

            text_boxes_by_page: dict[int, list[dict[str, Any]]] = {}
            result: dict[str, Any] = {
                "total_text_boxes": len(structure.text_boxes),
                "pages_with_text": len(set(box.page for box in structure.text_boxes)),
                "total_lines": structure.lines_count,
                "total_rectangles": structure.rectangles_count,
                "total_curves": structure.curves_count,
                "total_drawings": structure.drawings_count,
                "text_boxes_by_page": text_boxes_by_page,
            }

            # 按页面分组文本框
            for box in structure.text_boxes:
                page_num = box.page
                if page_num not in text_boxes_by_page:
                    text_boxes_by_page[page_num] = []
                text_boxes_by_page[page_num].append(
                    {
                        "text": box.text[:100] + "..." if len(box.text) > 100 else box.text,
                        "bbox": (box.x0, box.y0, box.x1, box.y1),
                    }
                )

            logger.debug(f"PDF结构分析完成: {result['total_text_boxes']} 个文本框, {result['total_drawings']} 个绘图元素")

            return result

        except Exception as e:
            logger.error(f"PDF结构分析失败: {e}")
            return {}

    def _get_pdf_images_sync(self) -> List[str]:
        """
        将PDF文件所有页面渲染成Base64编码的图像列表，并缓存结果（同步版本）。
        这是处理PDF视觉元素检查的关键步骤。

        Returns:
            一个包含所有页面图像Base64字符串的列表。
        """
        if self._pdf_images_cache is not None:
            logger.debug("从缓存中获取PDF图像。")
            return self._pdf_images_cache

        logger.debug("开始将PDF渲染为图像...")

        try:
            file_url = str(self.audit_file.file_url)
            from audit.engine.runtime import get_runtime
            runtime = get_runtime()
            if runtime is not None:
                images = runtime.render_pdf_images(file_url, render_all_pages=True)
            else:
                file_content = self._get_pdf_file_content()
                file_type = detect_file_type(file_content, url=file_url).lower()
                images = extract_images_from_bytes(file_content, file_type, render_all_pages=True) if file_content else []
        except Exception as e:
            logger.error(f"渲染PDF图像失败: {e}")
            images = []

        if not images:
            logger.warning(f"未能从PDF文件 {self.audit_file.file_url} 中渲染出任何图像。")
        else:
            logger.debug(f"成功将PDF渲染成 {len(images)} 张图像。")

        self._pdf_images_cache = images
        return images

    def _get_images_for_analysis(self) -> List[str]:
        """
        获取用于分析的图像列表。
        子类可以重写此方法来实现优化的图像获取策略。

        Returns:
            List[str]: base64编码的图像列表
        """
        return self._get_pdf_images_sync()

    def _crop_image(self, image_base64: str, crop_box: Tuple[int, int, int, int]) -> str:
        """
        裁剪单张Base64编码的图像。

        Args:
            image_base64: 原始图像的Base64字符串 (需要包含 data:image/...;base64, 前缀)。
            crop_box: 一个四元组 (left, upper, right, lower) 定义裁剪区域。

        Returns:
            裁剪后图像的Base64字符串。
        """
        try:
            # 移除前缀并解码
            header, encoded = image_base64.split(",", 1)
            image_data = base64.b64decode(encoded)
            image = Image.open(io.BytesIO(image_data))

            # 裁剪图像
            cropped_image = image.crop(crop_box)

            # 将裁剪后的图像编码回Base64
            buffered = io.BytesIO()
            # 保持原始格式或统一转为JPEG
            img_format = header.split("/")[1].split(";")[0].upper()
            if img_format not in ["JPEG", "PNG"]:
                img_format = "JPEG"
            cropped_image.save(buffered, format=img_format)

            new_encoded = base64.b64encode(buffered.getvalue()).decode("utf-8")

            return f"{header},{new_encoded}"
        except Exception as e:
            logger.error(f"裁剪图像失败: {e}", exc_info=True)
            return image_base64  # 失败时返回原图

    def _analyze_images_with_prompt(
        self,
        system_prompt: str,
        user_prompt: str,
        images: List[str],
        json_format: bool = False,
    ) -> str:
        """
        使用给定的提示和图像列表调用多模态AI进行分析（同步版本）。

        Args:
            system_prompt: 提供给AI的系统角色和指令。
            user_prompt: 用户的具体问题或任务描述。
            images: Base64编码的图像列表。
            json_format: 是否要求AI返回严格的JSON格式。

        Returns:
            AI模型的原始文本响应。
        """
        if not images:
            logger.error("分析图像失败：图像列表为空。")
            return "{}"

        from audit.engine.runtime import get_runtime
        runtime = get_runtime()
        if runtime is not None:
            response = runtime.run_image_prompt(
                system_prompt=system_prompt,
                user_text=user_prompt,
                image_base64_list=images,
                json_format=json_format,
            )
        else:
            response = self.client.image_analysis_completion(
                system_prompt=system_prompt,
                user_text=user_prompt,
                image_base64_list=images,
                json_format=json_format,
            )
        return response

    def _chain_text_analysis(
        self,
        initial_response: str,
        next_system_prompt: str,
        next_user_prompt_template: str = "{initial_response}",
    ) -> str:
        """
        将一个AI调用的输出作为另一个文本分析AI调用的输入（提示链）（同步版本）。
        这对于多步骤的推理任务非常有用。

        Args:
            initial_response: 第一个AI调用的结果。
            next_system_prompt: 第二个AI调用的系统提示。
            next_user_prompt_template: 第二个AI调用的用户提示模板，
                                     可以用 {initial_response} 占位符。

        Returns:
            第二个AI调用的结果。
        """
        user_prompt = next_user_prompt_template.format(initial_response=initial_response)

        response = self.client.structured_completion(system_prompt=next_system_prompt, user_prompt=user_prompt, json_format=True)
        return response
