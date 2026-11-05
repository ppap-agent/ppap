"""PDF文件内容提取器"""

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Set, Union

from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)


def _page_text_dict(page: Any, mode: str) -> dict[str, Any]:
    value = page.get_text(mode)
    if not isinstance(value, dict):
        raise TypeError(f"expected PyMuPDF get_text({mode!r}) to return dict")
    return value


def _page_words(page: Any) -> list[Any]:
    value = page.get_text("words")
    if not isinstance(value, list):
        raise TypeError("expected PyMuPDF get_text('words') to return list")
    return value


def _page_plain_text(page: Any) -> str:
    value = page.get_text()
    if not isinstance(value, str):
        raise TypeError("expected PyMuPDF get_text() to return str")
    return value


class TextBox(NamedTuple):
    """表示PDF中的文本框"""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int


class CharacterBox(NamedTuple):
    """表示PDF中的单个字符"""

    char: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int
    font_name: str
    font_size: float


class SpanBox(NamedTuple):
    """表示PDF中的文本片段"""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int
    font_name: str
    font_size: float
    font_flags: int


class WordBox(NamedTuple):
    """表示PDF中的单词级文本片段"""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int


class LineBox(NamedTuple):
    """表示PDF中的文本行"""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int
    spans: List[SpanBox]


PDFTextElement = TextBox | CharacterBox | SpanBox | WordBox | LineBox


class PDFStructure(NamedTuple):
    """表示PDF结构分析结果"""

    text_boxes: List[TextBox]
    lines_count: int
    rectangles_count: int
    curves_count: int
    drawings_count: int


class PDFExtractor(BaseExtractor):
    """PDF文件内容提取器"""

    def __init__(self) -> None:
        super().__init__()

    @property
    def supported_extensions(self) -> Set[str]:
        return {".pdf"}

    def extract_text_boxes(
        self,
        file_content: bytes,
        file_path: Optional[Union[str, Path]] = None,
        *,
        selected_pages: Optional[Iterable[int]] = None,
    ) -> List[TextBox]:
        """提取PDF文档的文本框信息

        Args:
            file_content: PDF文件的二进制内容
            file_path: 可选的文件路径
            selected_pages: 可选的 0-based 页码集合；为 None 时提取全部页面

        Returns:
            List[TextBox]: 文本框列表，包含位置和内容信息

        Raises:
            ExtractionError: 当PDF解析失败时抛出
        """
        try:
            text_boxes = self._extract_text_boxes_pymupdf(file_content, selected_pages=selected_pages)
            logger.debug(f"使用 PyMuPDF 成功提取 {len(text_boxes)} 个文本框")
            return text_boxes
        except Exception as e:
            logger.error(f"PyMuPDF 提取文本框失败: {e}")
            raise ExtractionError(f"PDF文本框提取失败: {str(e)}", file_type="PDF", original_error=e)

    def extract_character_boxes(
        self,
        file_content: bytes,
        file_path: Optional[Union[str, Path]] = None,
        *,
        selected_pages: Optional[Iterable[int]] = None,
    ) -> List[CharacterBox]:
        """提取PDF文档的字符级信息，用于精确定位和处理相邻文本

        Args:
            file_content: PDF文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            List[CharacterBox]: 字符框列表，包含每个字符的位置和样式信息

        Raises:
            ExtractionError: 当PDF解析失败时抛出
        """
        try:
            import fitz

            character_boxes = []
            pdf_document = fitz.open(stream=file_content, filetype="pdf")

            page_numbers = self._resolve_selected_page_numbers(len(pdf_document), selected_pages)
            for page_num in page_numbers:
                page = pdf_document[page_num]
                # 获取页面上的文本详细信息，包括字符级别
                text_dict = _page_text_dict(page, "dict")

                for block in text_dict.get("blocks", []):
                    if "lines" in block:  # 文本块
                        for line in block["lines"]:
                            for span in line.get("spans", []):
                                span_text = span.get("text", "")
                                span_bbox = span.get("bbox", [0, 0, 0, 0])
                                font_name = span.get("font", "")
                                font_size = span.get("size", 0)

                                if span_text:
                                    # 计算每个字符的大概位置
                                    char_width = (span_bbox[2] - span_bbox[0]) / len(span_text) if len(span_text) > 0 else 0

                                    for i, char in enumerate(span_text):
                                        char_x0 = span_bbox[0] + i * char_width
                                        char_x1 = char_x0 + char_width

                                        character_boxes.append(
                                            CharacterBox(
                                                char=char,
                                                x0=char_x0,
                                                y0=span_bbox[1],
                                                x1=char_x1,
                                                y1=span_bbox[3],
                                                page=page_num,
                                                font_name=font_name,
                                                font_size=font_size,
                                            )
                                        )

            pdf_document.close()
            logger.debug(f"成功提取 {len(character_boxes)} 个字符框")
            return character_boxes

        except ImportError:
            raise ImportError("PyMuPDF (fitz) 库未安装")
        except Exception as e:
            raise ExtractionError(f"字符框提取失败: {str(e)}", file_type="PDF", original_error=e)

    def extract_word_boxes(
        self,
        file_content: bytes,
        file_path: Optional[Union[str, Path]] = None,
        *,
        selected_pages: Optional[Iterable[int]] = None,
    ) -> List[WordBox]:
        """提取PDF文档的单词级文本片段，并处理页面旋转。"""
        try:
            import fitz

            word_boxes: List[WordBox] = []
            pdf_document = fitz.open(stream=file_content, filetype="pdf")
            page_numbers = self._resolve_selected_page_numbers(len(pdf_document), selected_pages)

            for page_num in page_numbers:
                page = pdf_document[page_num]
                page_rotation = page.rotation
                page_rect = page.rect
                media_box = page.mediabox
                crop_box = page.cropbox

                words = _page_words(page)
                for word_info in words:
                    if len(word_info) < 5:
                        continue
                    word_text = str(word_info[4] or "").strip()
                    if not word_text:
                        continue

                    bbox = [
                        float(word_info[0]),
                        float(word_info[1]),
                        float(word_info[2]),
                        float(word_info[3]),
                    ]
                    transformed_bbox = self._transform_coordinates_for_rotation(
                        bbox,
                        page_rotation,
                        page_rect,
                        media_box,
                        crop_box,
                    )
                    word_boxes.append(
                        WordBox(
                            text=word_text,
                            x0=transformed_bbox[0],
                            y0=transformed_bbox[1],
                            x1=transformed_bbox[2],
                            y1=transformed_bbox[3],
                            page=page_num,
                        )
                    )

            pdf_document.close()
            logger.debug(f"成功提取 {len(word_boxes)} 个单词框")
            return word_boxes

        except Exception as e:
            raise ExtractionError(f"单词框提取失败: {str(e)}", file_type="PDF", original_error=e)

    def extract_span_boxes(
        self,
        file_content: bytes,
        file_path: Optional[Union[str, Path]] = None,
        *,
        selected_pages: Optional[Iterable[int]] = None,
    ) -> List[SpanBox]:
        """提取PDF文档的文本片段信息，比文本框更精细，并处理页面旋转

        Args:
            file_content: PDF文件的二进制内容
            file_path: 可选的文件路径
            selected_pages: 可选的 0-based 页码集合；为 None 时提取全部页面

        Returns:
            List[SpanBox]: 文本片段列表，包含位置和样式信息，坐标已根据页面旋转调整
        """
        try:
            import fitz

            span_boxes = []
            pdf_document = fitz.open(stream=file_content, filetype="pdf")

            page_numbers = self._resolve_selected_page_numbers(len(pdf_document), selected_pages)
            for page_num in page_numbers:
                page = pdf_document[page_num]

                # 获取页面旋转角度和尺寸信息
                page_rotation = page.rotation  # 页面旋转角度 (0, 90, 180, 270)
                page_rect = page.rect  # 页面边界矩形
                media_box = page.mediabox  # 媒体框
                crop_box = page.cropbox  # 裁剪框

                logger.debug(f"页面 {page_num}: 旋转角度={page_rotation}°, 页面尺寸={page_rect}")

                text_dict = _page_text_dict(page, "dict")

                for block in text_dict.get("blocks", []):
                    if "lines" in block:  # 文本块
                        for line in block["lines"]:
                            for span in line.get("spans", []):
                                span_text = span.get("text", "").strip()
                                if span_text:
                                    bbox = span.get("bbox", [0, 0, 0, 0])

                                    # 应用坐标变换以处理页面旋转
                                    transformed_bbox = self._transform_coordinates_for_rotation(
                                        bbox,
                                        page_rotation,
                                        page_rect,
                                        media_box,
                                        crop_box,
                                    )

                                    span_boxes.append(
                                        SpanBox(
                                            text=span_text,
                                            x0=transformed_bbox[0],
                                            y0=transformed_bbox[1],
                                            x1=transformed_bbox[2],
                                            y1=transformed_bbox[3],
                                            page=page_num,
                                            font_name=span.get("font", ""),
                                            font_size=span.get("size", 0),
                                            font_flags=span.get("flags", 0),
                                        )
                                    )

            pdf_document.close()
            logger.debug(f"成功提取 {len(span_boxes)} 个文本片段")
            return span_boxes

        except Exception as e:
            raise ExtractionError(f"文本片段提取失败: {str(e)}", file_type="PDF", original_error=e)

    def extract_line_boxes(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> List[LineBox]:
        """提取PDF文档的文本行信息

        Args:
            file_content: PDF文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            List[LineBox]: 文本行列表，包含位置信息和包含的文本片段
        """
        try:
            import fitz

            line_boxes = []
            pdf_document = fitz.open(stream=file_content, filetype="pdf")

            for page_num in range(len(pdf_document)):
                page = pdf_document[page_num]
                text_dict = _page_text_dict(page, "dict")

                for block in text_dict.get("blocks", []):
                    if "lines" in block:  # 文本块
                        for line in block["lines"]:
                            line_text = ""
                            line_spans = []
                            line_bbox = [
                                float("inf"),
                                float("inf"),
                                0,
                                0,
                            ]  # x0, y0, x1, y1

                            for span in line.get("spans", []):
                                span_text = span.get("text", "")
                                if span_text:
                                    line_text += span_text
                                    bbox = span.get("bbox", [0, 0, 0, 0])

                                    # 更新行的边界框
                                    line_bbox[0] = min(line_bbox[0], bbox[0])  # x0
                                    line_bbox[1] = min(line_bbox[1], bbox[1])  # y0
                                    line_bbox[2] = max(line_bbox[2], bbox[2])  # x1
                                    line_bbox[3] = max(line_bbox[3], bbox[3])  # y1

                                    line_spans.append(
                                        SpanBox(
                                            text=span_text,
                                            x0=bbox[0],
                                            y0=bbox[1],
                                            x1=bbox[2],
                                            y1=bbox[3],
                                            page=page_num,
                                            font_name=span.get("font", ""),
                                            font_size=span.get("size", 0),
                                            font_flags=span.get("flags", 0),
                                        )
                                    )

                            if line_text.strip():
                                line_boxes.append(
                                    LineBox(
                                        text=line_text,
                                        x0=line_bbox[0] if line_bbox[0] != float("inf") else 0,
                                        y0=line_bbox[1] if line_bbox[1] != float("inf") else 0,
                                        x1=line_bbox[2],
                                        y1=line_bbox[3],
                                        page=page_num,
                                        spans=line_spans,
                                    )
                                )

            pdf_document.close()
            logger.debug(f"成功提取 {len(line_boxes)} 个文本行")
            return line_boxes

        except Exception as e:
            raise ExtractionError(f"文本行提取失败: {str(e)}", file_type="PDF", original_error=e)

    def group_nearby_elements(self, elements: List[Any], distance_threshold: float = 5.0) -> List[List[Any]]:
        """将相邻的文本元素分组

        Args:
            elements: 要分组的文本元素列表 (CharacterBox, SpanBox, LineBox, TextBox)
            distance_threshold: 距离阈值，超过此距离的元素不会被分组

        Returns:
            List[List[Any]]: 分组后的元素列表
        """
        if not elements:
            return []

        # 按页面和垂直位置排序
        sorted_elements = sorted(elements, key=lambda x: (x.page, x.y0, x.x0))

        groups = []
        current_group = [sorted_elements[0]]

        for element in sorted_elements[1:]:
            last_element = current_group[-1]

            # 检查是否在同一页面
            if element.page != last_element.page:
                groups.append(current_group)
                current_group = [element]
                continue

            # 计算距离
            horizontal_distance = abs(element.x0 - last_element.x1)
            vertical_distance = abs(element.y0 - last_element.y0)

            # 如果距离在阈值内，添加到当前组
            if horizontal_distance <= distance_threshold and vertical_distance <= distance_threshold:
                current_group.append(element)
            else:
                groups.append(current_group)
                current_group = [element]

        if current_group:
            groups.append(current_group)

        return groups

    def extract_precise_character_boxes(
        self,
        file_content: bytes,
        file_path: Optional[Union[str, Path]] = None,
        *,
        selected_pages: Optional[Iterable[int]] = None,
    ) -> List[CharacterBox]:
        """使用PyMuPDF的更精确方法提取字符级信息，并处理页面旋转

        这个方法使用PyMuPDF的get_text("rawdict")来获得更精确的字符位置信息

        Args:
            file_content: PDF文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            List[CharacterBox]: 精确的字符框列表，坐标已根据页面旋转调整
        """
        try:
            import fitz

            character_boxes = []
            pdf_document = fitz.open(stream=file_content, filetype="pdf")

            page_numbers = self._resolve_selected_page_numbers(len(pdf_document), selected_pages)
            for page_num in page_numbers:
                page = pdf_document[page_num]

                # 获取页面旋转和尺寸信息
                page_rotation = page.rotation
                page_rect = page.rect
                media_box = page.mediabox
                crop_box = page.cropbox

                # 使用rawdict获得更精确的字符信息
                raw_dict = _page_text_dict(page, "rawdict")

                for block in raw_dict.get("blocks", []):
                    if "lines" in block:
                        for line in block["lines"]:
                            for span in line.get("spans", []):
                                font_name = span.get("font", "")
                                font_size = span.get("size", 0)
                                chars_info = span.get("chars", [])

                                for char_info in chars_info:
                                    char = char_info.get("c", "")
                                    bbox = char_info.get("bbox", [0, 0, 0, 0])

                                    if char and char.strip():  # 跳过空白字符
                                        # 应用坐标变换以处理页面旋转
                                        transformed_bbox = self._transform_coordinates_for_rotation(
                                            bbox,
                                            page_rotation,
                                            page_rect,
                                            media_box,
                                            crop_box,
                                        )

                                        character_boxes.append(
                                            CharacterBox(
                                                char=char,
                                                x0=transformed_bbox[0],
                                                y0=transformed_bbox[1],
                                                x1=transformed_bbox[2],
                                                y1=transformed_bbox[3],
                                                page=page_num,
                                                font_name=font_name,
                                                font_size=font_size,
                                            )
                                        )

            pdf_document.close()
            logger.debug(f"使用精确方法成功提取 {len(character_boxes)} 个字符框")
            return character_boxes

        except ImportError:
            raise ImportError("PyMuPDF (fitz) 库未安装")
        except Exception as e:
            # 如果精确方法失败，回退到基本方法
            logger.warning(f"精确字符提取失败，回退到基本方法: {e}")
            return self.extract_character_boxes(file_content, file_path, selected_pages=selected_pages)

    def find_close_text_elements(
        self,
        file_content: bytes,
        max_distance: float = 10.0,
        extraction_level: str = "span",
    ) -> Dict[str, Any]:
        """查找相邻的文本元素

        Args:
            file_content: PDF文件的二进制内容
            max_distance: 最大距离阈值
            extraction_level: 提取级别 ("character", "span", "line", "block")

        Returns:
            Dict包含原始元素和分组结果
        """
        try:
            elements: Iterable[PDFTextElement]
            if extraction_level == "character":
                elements = self.extract_precise_character_boxes(file_content)
            elif extraction_level == "span":
                elements = self.extract_span_boxes(file_content)
            elif extraction_level == "line":
                elements = self.extract_line_boxes(file_content)
            else:  # block
                elements = self.extract_text_boxes(file_content)

            # 分组相邻元素 - 类型转换为通用列表
            elements_list = list(elements)  # 类型转换
            groups = self.group_nearby_elements(elements_list, max_distance)

            # 统计信息
            close_groups = [group for group in groups if len(group) > 1]

            return {
                "extraction_level": extraction_level,
                "total_elements": len(elements_list),
                "total_groups": len(groups),
                "close_groups_count": len(close_groups),
                "elements": elements_list,
                "all_groups": groups,
                "close_groups": close_groups,
                "max_distance_threshold": max_distance,
            }

        except Exception as e:
            logger.error(f"查找相邻文本元素失败: {e}")
            return {
                "extraction_level": extraction_level,
                "error": str(e),
                "total_elements": 0,
                "total_groups": 0,
                "close_groups_count": 0,
                "elements": [],
                "all_groups": [],
                "close_groups": [],
            }

    def _extract_text_boxes_pymupdf(
        self,
        file_content: bytes,
        *,
        selected_pages: Optional[Iterable[int]] = None,
    ) -> List[TextBox]:
        """使用 PyMuPDF 提取文本框，并处理页面旋转"""
        try:
            import fitz

            text_boxes = []
            pdf_document = fitz.open(stream=file_content, filetype="pdf")

            page_numbers = self._resolve_selected_page_numbers(len(pdf_document), selected_pages)
            for page_num in page_numbers:
                page = pdf_document[page_num]

                # 获取页面旋转和尺寸信息
                page_rotation = page.rotation
                page_rect = page.rect
                media_box = page.mediabox
                crop_box = page.cropbox

                # 获取页面上的文本块
                text_dict = _page_text_dict(page, "dict")

                for block in text_dict.get("blocks", []):
                    if "lines" in block:  # 文本块
                        # 收集块中的所有文本
                        block_text = ""
                        for line in block["lines"]:
                            for span in line.get("spans", []):
                                block_text += span.get("text", "")
                            block_text += "\n"

                        block_text = block_text.strip()
                        if block_text:
                            bbox = block.get("bbox", [0, 0, 0, 0])

                            # 应用坐标变换以处理页面旋转
                            transformed_bbox = self._transform_coordinates_for_rotation(bbox, page_rotation, page_rect, media_box, crop_box)

                            text_boxes.append(
                                TextBox(
                                    text=block_text,
                                    x0=transformed_bbox[0],
                                    y0=transformed_bbox[1],
                                    x1=transformed_bbox[2],
                                    y1=transformed_bbox[3],
                                    page=page_num,
                                )
                            )

            pdf_document.close()
            return text_boxes

        except ImportError:
            raise ImportError("PyMuPDF (fitz) 库未安装")
        except Exception as e:
            raise ExtractionError(f"PyMuPDF 文本框提取失败: {str(e)}", file_type="PDF", original_error=e)

    def analyze_pdf_structure(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> PDFStructure:
        """分析PDF结构，包括文本框和图形元素

        Args:
            file_content: PDF文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            PDFStructure: PDF结构分析结果
        """
        try:
            # 提取文本框
            text_boxes = self.extract_text_boxes(file_content, file_path)

            # 分析图形元素
            shape_info = self._analyze_shapes_pymupdf(file_content)

            return PDFStructure(
                text_boxes=text_boxes,
                lines_count=shape_info.get("lines", 0),
                rectangles_count=shape_info.get("rectangles", 0),
                curves_count=shape_info.get("curves", 0),
                drawings_count=shape_info.get("drawings", 0),
            )

        except Exception as e:
            logger.error(f"PDF结构分析失败: {e}")
            return PDFStructure(
                text_boxes=[],
                lines_count=0,
                rectangles_count=0,
                curves_count=0,
                drawings_count=0,
            )

    def _analyze_shapes_pymupdf(self, file_content: bytes) -> Dict[str, int]:
        """使用 PyMuPDF 分析PDF中的形状元素"""
        try:
            import fitz

            total_lines = 0
            total_rectangles = 0
            total_curves = 0
            total_drawings = 0

            pdf_document = fitz.open(stream=file_content, filetype="pdf")

            for page_num in range(len(pdf_document)):
                page = pdf_document[page_num]
                drawings = page.get_drawings()
                total_drawings += len(drawings)

                # 统计绘图元素中的基本形状
                for drawing in drawings:
                    for item in drawing.get("items", []):
                        if isinstance(item, (list, tuple)) and item:
                            item_type = item[0] if item else ""
                            if item_type == "l":  # line
                                total_lines += 1
                            elif item_type == "re":  # rectangle
                                total_rectangles += 1
                            elif item_type == "c":  # curve
                                total_curves += 1

            pdf_document.close()

            return {
                "lines": total_lines,
                "rectangles": total_rectangles,
                "curves": total_curves,
                "drawings": total_drawings,
            }

        except Exception as e:
            logger.error(f"PDF形状分析失败: {e}")
            return {"lines": 0, "rectangles": 0, "curves": 0, "drawings": 0}

    def search_text_in_boxes(
        self,
        file_content: bytes,
        search_terms: List[str],
        case_sensitive: bool = False,
        no_whitespace: bool = False,
    ) -> Dict[str, List[TextBox]]:
        """在文本框中搜索特定文本

        Args:
            file_content: PDF文件的二进制内容
            search_terms: 要搜索的文本列表
            case_sensitive: 是否区分大小写

        Returns:
            Dict[str, List[TextBox]]: 搜索结果，键为搜索词，值为包含该词的文本框列表
        """
        try:
            text_boxes = self.extract_text_boxes(file_content)
            results = {}

            for term in search_terms:
                matching_boxes = []
                search_term = term if case_sensitive else term.lower()

                for box in text_boxes:
                    box_text = box.text if case_sensitive else box.text.lower()

                    if no_whitespace:
                        search_term = "".join(search_term.split())
                        box_text = "".join(box_text.split())

                    if search_term in box_text:
                        matching_boxes.append(box)

                results[term] = matching_boxes
                logger.debug(f"找到 {len(matching_boxes)} 个包含 '{term}' 的文本框")

            return results

        except Exception as e:
            logger.error(f"文本搜索失败: {e}")
            return {term: [] for term in search_terms}

    def extract_pages_content(self, pdf_content: bytes) -> List[bytes]:
        """提取PDF文档的所有页面内容

        Args:
            file_content: PDF文件的二进制内容

        Returns:
            List[bytes]: 每个页面的内容
        """
        try:
            import fitz  # PyMuPDF

            # 打开PDF文档
            pdf_doc = fitz.open(stream=pdf_content, filetype="pdf")

            if pdf_doc.page_count == 0:
                raise Exception("PDF文档为空")

            page_contents = []

            # 逐页提取内容
            for page_num in range(pdf_doc.page_count):
                # 创建只包含当前页的新PDF
                page_doc = fitz.open()  # 创建新的空PDF
                page_doc.insert_pdf(pdf_doc, from_page=page_num, to_page=page_num)  # 插入当前页

                # 将当前页PDF转换为bytes
                page_content = page_doc.tobytes()
                page_contents.append(page_content)

                # 关闭当前页文档
                page_doc.close()

                logger.debug(f"成功提取第 {page_num + 1} 页内容")

            # 关闭原始文档
            pdf_doc.close()

            logger.info(f"成功使用fitz提取PDF所有 {len(page_contents)} 页内容")
            return page_contents

        except ImportError:
            logger.warning("PyMuPDF (fitz) 未安装，回退到使用完整PDF")
            return [pdf_content]
        except Exception as e:
            logger.error(f"使用fitz提取分页失败: {e}, 回退到使用完整PDF")
            return [pdf_content]

    def extract_pages_text(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> List[str]:
        """提取PDF文档的所有页面文本内容

        Args:
            file_content: PDF文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            str: 提取的文本内容

        Raises:
            ExtractionError: 当PDF解析失败时抛出
        """
        try:
            import fitz  # PyMuPDF

            logger.debug(f"开始提取PDF页面文本，文件大小: {len(file_content)} bytes")

            pdf_document = fitz.open(stream=file_content, filetype="pdf")
            text_content = []

            for page_num in range(len(pdf_document)):
                page = pdf_document[page_num]
                text_content.append(_page_plain_text(page))

            pdf_document.close()

            if len(text_content) == 0:
                logger.warning("PDF文档中未提取到文本内容")
                return []

            return text_content

        except ImportError as e:
            raise ExtractionError("PDF提取器依赖库未安装", file_type="PDF", original_error=e)
        except Exception as e:
            raise ExtractionError(f"PDF页面文本提取失败: {str(e)}", file_type="PDF", original_error=e)

    def _resolve_selected_page_numbers(
        self,
        page_count: int,
        selected_pages: Optional[Iterable[int]],
    ) -> Iterable[int]:
        if selected_pages is None:
            return range(page_count)

        normalized = sorted({page_num for page_num in (int(page) for page in selected_pages) if 0 <= page_num < page_count})
        return normalized

    def extract_text(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> str:
        """提取PDF文档的文本内容

        Args:
            file_content: PDF文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            str: 提取的文本内容

        Raises:
            ExtractionError: 当PDF解析失败时抛出
        """
        try:
            extract_pages = self.extract_pages_text(file_content, file_path)
            result = "\n".join(extract_pages)
            if not result.strip():
                logger.warning("PDF文档中未提取到文本内容")
                return ""
            logger.debug(f"成功提取PDF文本内容，长度: {len(result)}")
            return result

        except ImportError as e:
            raise ExtractionError("PDF提取器依赖库未安装", file_type="PDF", original_error=e)
        except Exception as e:
            raise ExtractionError(f"PDF文本提取失败: {str(e)}", file_type="PDF", original_error=e)

    def extract_metadata(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """提取PDF文档的元数据

        Args:
            file_content: PDF文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            Dict[str, Any]: 包含PDF元数据的字典
        """
        try:
            import io

            import fitz  # PyMuPDF

            metadata = {}

            # 使用PyMuPDF读取PDF
            pdf_stream = io.BytesIO(file_content)
            doc = fitz.open(stream=pdf_stream.read(), filetype="pdf")

            # 提取基础信息
            metadata.update(
                {
                    "page_count": doc.page_count,
                    "file_size": len(file_content),
                    "format": "PDF",
                }
            )

            # 提取文档属性
            doc_metadata = doc.metadata
            if doc_metadata:
                metadata.update(
                    {
                        "title": doc_metadata.get("title", ""),
                        "author": doc_metadata.get("author", ""),
                        "subject": doc_metadata.get("subject", ""),
                        "creator": doc_metadata.get("creator", ""),
                        "producer": doc_metadata.get("producer", ""),
                        "creation_date": doc_metadata.get("creationDate", ""),
                        "modification_date": doc_metadata.get("modDate", ""),
                    }
                )

            doc.close()
            return metadata

        except ImportError:
            logger.warning("PyMuPDF未安装，无法提取PDF元数据")
            return {"format": "PDF", "file_size": len(file_content)}
        except Exception as e:
            logger.error(f"PDF元数据提取失败: {str(e)}")
            return {"format": "PDF", "file_size": len(file_content), "error": str(e)}

    def _transform_coordinates_for_rotation(
        self,
        bbox: List[float],
        rotation: int,
        page_rect: Any,
        media_box: Any,
        crop_box: Any,
    ) -> List[float]:
        """根据页面旋转角度变换坐标系统

        Args:
            bbox: 原始边界框 [x0, y0, x1, y1]
            rotation: 页面旋转角度 (0, 90, 180, 270)
            page_rect: 页面矩形
            media_box: 媒体框
            crop_box: 裁剪框

        Returns:
            List[float]: 变换后的边界框 [x0, y0, x1, y1]
        """
        try:
            pass

            x0, y0, x1, y1 = bbox

            # 如果没有旋转，直接返回原坐标
            if rotation == 0:
                return [x0, y0, x1, y1]

            # 获取页面尺寸
            page_width = page_rect.width
            page_height = page_rect.height

            # 根据旋转角度应用坐标变换
            if rotation == 90:
                # 顺时针旋转90度: (x, y) -> (page_height - y, x)
                new_x0 = page_height - y1
                new_y0 = x0
                new_x1 = page_height - y0
                new_y1 = x1
            elif rotation == 180:
                # 旋转180度: (x, y) -> (page_width - x, page_height - y)
                new_x0 = page_width - x1
                new_y0 = page_height - y1
                new_x1 = page_width - x0
                new_y1 = page_height - y0
            elif rotation == 270:
                # 逆时针旋转90度(顺时针270度): (x, y) -> (y, page_width - x)
                new_x0 = y0
                new_y0 = page_width - x1
                new_x1 = y1
                new_y1 = page_width - x0
            else:
                # 未知旋转角度，记录警告并返回原坐标
                logger.warning(f"未知的页面旋转角度: {rotation}°")
                return [x0, y0, x1, y1]

            # 确保坐标顺序正确 (x0 <= x1, y0 <= y1)
            final_x0 = min(new_x0, new_x1)
            final_x1 = max(new_x0, new_x1)
            final_y0 = min(new_y0, new_y1)
            final_y1 = max(new_y0, new_y1)

            logger.debug(f"坐标变换: {rotation}° - 原始({x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f}) -> 变换后({final_x0:.1f},{final_y0:.1f},{final_x1:.1f},{final_y1:.1f})")

            return [final_x0, final_y0, final_x1, final_y1]

        except Exception as e:
            logger.error(f"坐标变换失败: {e}")
            return bbox  # 变换失败时返回原坐标

    def get_page_rotation_info(self, file_content: bytes) -> Dict[int, Dict[str, Any]]:
        """获取PDF各页面的旋转信息

        Args:
            file_content: PDF文件的二进制内容

        Returns:
            Dict[int, Dict[str, Any]]: 每个页面的旋转和尺寸信息
        """
        try:
            import fitz

            rotation_info = {}
            pdf_document = fitz.open(stream=file_content, filetype="pdf")

            for page_num in range(len(pdf_document)):
                page = pdf_document[page_num]

                rotation_info[page_num] = {
                    "rotation": page.rotation,
                    "page_rect": {
                        "width": page.rect.width,
                        "height": page.rect.height,
                        "x0": page.rect.x0,
                        "y0": page.rect.y0,
                        "x1": page.rect.x1,
                        "y1": page.rect.y1,
                    },
                    "media_box": {
                        "width": page.mediabox.width,
                        "height": page.mediabox.height,
                        "x0": page.mediabox.x0,
                        "y0": page.mediabox.y0,
                        "x1": page.mediabox.x1,
                        "y1": page.mediabox.y1,
                    },
                    "crop_box": {
                        "width": page.cropbox.width,
                        "height": page.cropbox.height,
                        "x0": page.cropbox.x0,
                        "y0": page.cropbox.y0,
                        "x1": page.cropbox.x1,
                        "y1": page.cropbox.y1,
                    },
                }

                logger.debug(f"页面 {page_num}: 旋转={page.rotation}°, 尺寸={page.rect.width}x{page.rect.height}")

            pdf_document.close()
            return rotation_info

        except Exception as e:
            logger.error(f"获取页面旋转信息失败: {e}")
            return {}
