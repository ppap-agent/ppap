"""OCR adapter protocol."""

from __future__ import annotations

from typing import Optional, Protocol

from ppap_core.contracts import FileRef, OcrArtifact


class OcrAdapter(Protocol):
    """Protocol for OCR adapters."""

    def ocr_document_content(self, file_ref: FileRef | str, host: Optional[str] = None) -> OcrArtifact: ...

    def ocr_pdf_to_excel(self, file_ref: FileRef | str, enable_cache: bool = True) -> OcrArtifact: ...
