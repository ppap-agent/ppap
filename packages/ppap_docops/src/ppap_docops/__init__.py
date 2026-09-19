"""PPAP Document Operations - extractors, converters, file utilities."""

__version__ = "0.2.0"

from .excel import ExcelLoader, ExcelLoadError
from .extractors import (
    BaseExtractor,
    ExcelExtractor,
    ExtractionError,
    ImageExtractor,
    MarkdownExtractor,
    PDFExtractor,
    PowerPointExtractor,
    WordExtractor,
    get_extractor,
    get_extractor_for_file,
    is_supported_file_type,
)
from .files import detect_file_type, download_file_sync
from .pdf.linegrid.extractor import PDFTableExtractor
from .pdf.linegrid.labeler import PDFLabeler
from .word import WordDocument, extract_docx_structure, word_document_to_workbook

__all__ = [
    "__version__",
    "BaseExtractor",
    "ExcelExtractor",
    "ExcelLoadError",
    "ExcelLoader",
    "ExtractionError",
    "ImageExtractor",
    "MarkdownExtractor",
    "PDFExtractor",
    "PDFLabeler",
    "PDFTableExtractor",
    "PowerPointExtractor",
    "WordExtractor",
    "WordDocument",
    "detect_file_type",
    "download_file_sync",
    "get_extractor",
    "get_extractor_for_file",
    "is_supported_file_type",
    "extract_docx_structure",
    "word_document_to_workbook",
]
