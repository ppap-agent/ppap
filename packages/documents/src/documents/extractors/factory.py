"""File extension to text extractor."""

from .base import BaseExtractor
from .excel import ExcelExtractor
from .image import ImageExtractor
from .markdown import MarkdownExtractor
from .pdf import PDFExtractor
from .powerpoint import PowerPointExtractor
from .word import WordExtractor

# Each extractor declares its own extensions; no extension belongs to two classes.
_EXTRACTORS: dict[str, type[BaseExtractor]] = {
    extension.lower(): extractor
    for extractor in (PDFExtractor, ExcelExtractor, WordExtractor, PowerPointExtractor, ImageExtractor, MarkdownExtractor)
    for extension in extractor().supported_extensions
}


def get_extractor(file_extension: str) -> BaseExtractor:
    """A new extractor for an extension such as ``.pdf``; ValueError when unsupported."""
    extractor = _EXTRACTORS.get(file_extension.lower())
    if extractor is None:
        raise ValueError(f"不支持的文件类型: {file_extension}。支持的类型: {sorted(_EXTRACTORS)}")
    return extractor()
