"""Public API wrappers for PDF extraction service."""

from __future__ import annotations

from .result import ConversionResult, ExtractionResult, PdfSource
from .service import convert_pdf_to_excel, extract_pdf_tables
from .spec import PdfSpec

__all__ = [
    "ConversionResult",
    "ExtractionResult",
    "PdfSource",
    "PdfSpec",
    "convert_pdf_to_excel",
    "extract_pdf_tables",
]
