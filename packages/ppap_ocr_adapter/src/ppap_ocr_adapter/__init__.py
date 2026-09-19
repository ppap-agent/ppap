"""PPAP OCR Adapter package."""

from .base import OcrAdapter
from .facade import (
    ocr_document_content,
    ocr_pdf_to_excel,
)
from .local import LocalOcrAdapter
from .remote import RemoteOcrAdapter
from .selector import get_ocr_adapter, normalize_ocr_adapter_mode

__all__ = [
    "OcrAdapter",
    "LocalOcrAdapter",
    "RemoteOcrAdapter",
    "get_ocr_adapter",
    "normalize_ocr_adapter_mode",
    "ocr_document_content",
    "ocr_pdf_to_excel",
]
