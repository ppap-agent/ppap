"""文件内容提取器模块

统一的文件内容提取接口，支持多种文件格式：
- PDF文档
- Excel表格 (.xlsx, .xls)
- Word文档 (.docx, .doc)
- PowerPoint演示文稿 (.pptx, .ppt)
- 图像文件 (.jpg, .png, .gif, .bmp, .webp, .tiff)
- Markdown 文本 (.md)

用法示例:
    from documents.extractors import get_extractor, PDFExtractor

    # 使用工厂模式
    extractor = get_extractor('.pdf')
    text_content = extractor.extract_text(file_content)

    # 直接使用特定提取器
    pdf_extractor = PDFExtractor()
    text_content = pdf_extractor.extract_text(file_content)
"""

from .base import BaseExtractor, ExtractionError
from .excel import ExcelExtractor
from .factory import (
    ExtractorFactory,
    extractor_factory,
    get_extractor,
    get_extractor_for_file,
    is_supported_file_type,
)
from .image import ImageExtractor
from .markdown import MarkdownExtractor
from .pdf import CharacterBox, LineBox, PDFExtractor, PDFStructure, SpanBox, TextBox, WordBox
from .powerpoint import PowerPointExtractor
from .word import WordExtractor

__all__ = [
    # 基础类和异常
    "BaseExtractor",
    "ExtractionError",
    # 具体提取器
    "PDFExtractor",
    "ExcelExtractor",
    "WordExtractor",
    "PowerPointExtractor",
    "ImageExtractor",
    "MarkdownExtractor",
    # PDF数据类型
    "TextBox",
    "CharacterBox",
    "SpanBox",
    "WordBox",
    "LineBox",
    "PDFStructure",
    # 工厂相关
    "ExtractorFactory",
    "extractor_factory",
    "get_extractor",
    "get_extractor_for_file",
    "is_supported_file_type",
]
