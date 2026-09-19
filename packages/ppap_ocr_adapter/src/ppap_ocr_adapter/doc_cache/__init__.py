"""Local in-process OCR runtime ported from doc-cache."""

from .ocr_service import process_file_url, process_local_pdf

__all__ = ["process_file_url", "process_local_pdf"]
