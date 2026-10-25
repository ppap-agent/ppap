"""Local in-process OCR adapter implementation."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from typing import Optional

from ppap_core.contracts import FileRef, OcrArtifact
from ppap_core.settings import get_settings
from ppap_docops.extractors import get_extractor_for_file
from ppap_docops.files import download_file_to_dir_sync, parse_file_size

from .base import OcrAdapter
from .doc_cache import process_file_url, process_local_pdf

log = logging.getLogger(__name__)


class LocalOcrAdapter(OcrAdapter):
    """In-process OCR adapter without a remote OCR HTTP dependency."""

    @staticmethod
    def _to_file_ref(file_ref: FileRef | str) -> FileRef:
        if isinstance(file_ref, FileRef):
            return file_ref
        return FileRef(location=str(file_ref))

    @staticmethod
    def _safe_unlink(path: Optional[str]) -> None:
        if not path:
            return
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            log.debug("failed to cleanup temp file: %s", path)

    def _resolve_local_path(self, ref: FileRef) -> tuple[str, Optional[str]]:
        if not ref.is_remote:
            return ref.location, None

        settings = get_settings()
        max_size = parse_file_size(settings.MAX_FILE_SIZE)
        dl_dir = os.path.join(tempfile.gettempdir(), "ppap_local_ocr_dl")
        os.makedirs(dl_dir, exist_ok=True)
        ext = os.path.splitext(ref.location.split("?")[0].split("#")[0])[1] or ".pdf"
        unique_name = f"{uuid.uuid4().hex}{ext}"
        local_path = download_file_to_dir_sync(
            ref.location,
            dl_dir,
            max_size,
            custom_filename=unique_name,
        )
        return local_path, local_path

    def ocr_document_content(self, file_ref: FileRef | str, host: Optional[str] = None) -> OcrArtifact:
        del host
        ref = self._to_file_ref(file_ref)
        local_path, downloaded = self._resolve_local_path(ref)
        try:
            with open(local_path, "rb") as fp:
                file_content = fp.read()
            extractor = get_extractor_for_file(local_path)
            text = extractor.extract_text(file_content, file_path=local_path)
            return OcrArtifact(
                text=text or "",
                provider="local_ocr",
            )
        finally:
            self._safe_unlink(downloaded)

    def ocr_pdf_to_excel(self, file_ref: FileRef | str, enable_cache: bool = True) -> OcrArtifact:
        ref = self._to_file_ref(file_ref)
        try:
            if ref.is_remote:
                result = process_file_url(ref.location, enable_cache=enable_cache)
            else:
                result = process_local_pdf(ref.location, enable_cache=enable_cache)

            excel_path = result.get("excel_path")
            if not excel_path:
                return OcrArtifact(
                    provider="local_ocr_doc_cache",
                    raw_meta=result,
                )

            excel_path_str = str(excel_path)
            if not os.path.exists(excel_path_str):
                raise FileNotFoundError(f"Excel file not found at path: {excel_path_str}")
            if not os.access(excel_path_str, os.R_OK):
                raise PermissionError(f"Excel file is not readable: {excel_path_str}")

            fd, local_xlsx = tempfile.mkstemp(suffix=".xlsx")
            os.close(fd)
            try:
                shutil.copyfile(excel_path_str, local_xlsx)
            except Exception:
                self._safe_unlink(local_xlsx)
                raise

            return OcrArtifact(
                provider="local_ocr_doc_cache",
                excel_path=local_xlsx,
                html_path=str(result.get("html_path")) if result.get("html_path") else None,
                raw_meta=result,
            )
        except Exception as exc:
            log.error("Local OCR PDF->Excel failed: %s", exc)
            diagnostics = {"error": str(exc)}
            code = getattr(exc, "code", None)
            if isinstance(code, str) and code.startswith("responses_"):
                diagnostics["error_code"] = code
            return OcrArtifact(
                provider="local_ocr_doc_cache",
                raw_meta=diagnostics,
            )
