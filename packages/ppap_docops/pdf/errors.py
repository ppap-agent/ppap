"""Error model for PDF conversion failures."""

from __future__ import annotations

from typing import Any


class PdfConversionError(RuntimeError):
    def __init__(self, code: str, message: str, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.diagnostics = diagnostics or {}


__all__ = ["PdfConversionError"]
