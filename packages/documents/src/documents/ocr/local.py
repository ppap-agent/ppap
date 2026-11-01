"""Validated local PDF vision fallback, returning independent caller workbooks."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile

from documents.contracts import OcrArtifact

from .cache import process_local_pdf
from .client import ResponsesEvidenceError

logger = logging.getLogger(__name__)


def convert_local_pdf_to_excel(path: str, *, enable_cache: bool = True) -> OcrArtifact:
    try:
        result = process_local_pdf(path, enable_cache=enable_cache)
        cached_path = result.get("excel_path")
        if not cached_path:
            return OcrArtifact(provider="local_ocr_doc_cache", raw_meta=result)
        if not os.path.isfile(str(cached_path)) or not os.access(str(cached_path), os.R_OK):
            raise FileNotFoundError("Validated vision workbook is unavailable")
        fd, local_path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            shutil.copyfile(str(cached_path), local_path)
        except BaseException:
            os.unlink(local_path)
            raise
        return OcrArtifact(
            provider="local_ocr_doc_cache",
            excel_path=local_path,
            html_path=str(result.get("html_path")) if result.get("html_path") else None,
            raw_meta=result,
        )
    except Exception as exc:
        logger.error("Local PDF vision fallback failed: %s", exc)
        payload: dict[str, str] = {"error": str(exc)}
        if isinstance(exc, ResponsesEvidenceError):
            payload["error_code"] = exc.code
        return OcrArtifact(provider="local_ocr_doc_cache", raw_meta=payload)
