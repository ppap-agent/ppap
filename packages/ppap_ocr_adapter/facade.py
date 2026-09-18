"""Facade APIs for OCR adapters."""

from __future__ import annotations

from typing import Optional

from ppap_core.contracts import FileRef

from .selector import get_ocr_adapter


def ocr_document_content(
    file_ref: FileRef | str,
    host: Optional[str] = None,
    mode: Optional[str] = None,
) -> str:
    """Return OCR text content."""
    adapter = get_ocr_adapter(mode=mode, host=host)
    artifact = adapter.ocr_document_content(file_ref, host=host)
    return artifact.text


def ocr_pdf_to_excel(
    file_ref: FileRef | str,
    enable_cache: bool = True,
    host: Optional[str] = None,
    mode: Optional[str] = None,
) -> Optional[str]:
    """Return OCR-generated local xlsx path."""
    adapter = get_ocr_adapter(mode=mode, host=host)
    artifact = adapter.ocr_pdf_to_excel(
        file_ref,
        enable_cache=enable_cache,
    )
    return artifact.excel_path
