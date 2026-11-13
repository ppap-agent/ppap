"""Original document capabilities shared by an accepted checker run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from audit.engine.rule_engine.context import GuardStop
from audit.engine.rule_engine.contracts import Decision
from documents.contracts import ConversionArtifact, FileRef
from documents.conversion import convert_pdf_to_excel_auto
from documents.excel import load_excel_workbook


class CheckerRuntime:
    """Per-run reads and receipts. Failed external operations are not retried.

    Development's legacy fixed replay stays offline by default. Unified audit
    callers explicitly enable tools inside the initiating user's model scope.
    """

    def __init__(self, *, allow_model_tools: bool = False) -> None:
        self.allow_model_tools = allow_model_tools
        self._cache: dict[Any, Any] = {}
        self._failed: dict[Any, BaseException] = {}
        self._closeables: list[Any] = []

    def _once(self, key: Any, operation: Callable[[], Any]) -> Any:
        if key in self._failed:
            raise self._failed[key]
        if key not in self._cache:
            try:
                self._cache[key] = operation()
            except BaseException as exc:
                self._failed[key] = exc
                raise
        return self._cache[key]

    def load_workbook_auto(self, file_ref: FileRef) -> Any:
        if Path(file_ref.location).suffix.lower() == ".pdf":
            raise GuardStop(Decision.UNABLE, "PDF 必须提供本次文件的 PdfSpec")
        key = ("workbook", str(Path(file_ref.location).resolve()))
        if key not in self._cache:
            workbook = self._once(key, lambda: load_excel_workbook(file_ref.location))
            self._closeables.append(workbook)
        return self._cache[key]

    def convert_pdf_to_excel(self, file_ref: FileRef, *, enable_cache: bool = True, spec: Any = None, doc_type: object | None = None) -> ConversionArtifact:
        if spec is None and not self.allow_model_tools:
            raise GuardStop(Decision.UNABLE, "PDF 必须提供本次文件的 PdfSpec")
        spec_key = spec.model_dump_json() if hasattr(spec, "model_dump_json") else repr(spec)
        key = ("pdf_conversion", str(Path(file_ref.location).resolve()), enable_cache, spec_key, str(doc_type))
        return self._once(key, lambda: convert_pdf_to_excel_auto(file_ref, enable_cache=enable_cache, spec=spec, doc_type=doc_type))

    def render_pdf_images(self, file_url: str, *, render_all_pages: bool = True) -> list[str]:
        from documents.images.image import extract_images_from_bytes

        key = ("pdf_images", str(Path(file_url).resolve()), render_all_pages)
        return list(self._once(key, lambda: extract_images_from_bytes(Path(file_url).read_bytes(), "pdf", render_all_pages=render_all_pages)))

    def read_ocr_visual_facts(self, *, images: list[bytes], prompt: str, response_schema: dict[str, Any]) -> dict[str, Any]:
        if not self.allow_model_tools:
            raise GuardStop(Decision.UNABLE, "固定 checker 没有可重放的 OCR 视觉观察，需补充证据")
        from audit.engine.checkers.ocr_visual_facts import request_ocr_visual_facts
        from documents.ocr.observation import current_ocr_observation

        key = ("ocr", current_ocr_observation(), tuple(hashlib.sha256(image).hexdigest() for image in images), prompt, json.dumps(response_schema, sort_keys=True))
        return self._once(key, lambda: request_ocr_visual_facts(images=images, prompt=prompt, response_schema=response_schema))

    def close(self) -> None:
        for item in self._closeables:
            close = getattr(item, "close", None)
            if callable(close):
                close()
        self._closeables.clear()
        self._cache.clear()
        self._failed.clear()
