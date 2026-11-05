"""Original document capabilities available during fixed checker execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from audit.engine.rule_engine.context import GuardStop
from audit.engine.rule_engine.contracts import Decision
from documents.contracts import ConversionArtifact, FileRef
from documents.conversion import convert_pdf_to_excel_auto
from documents.excel import load_excel_workbook


class CheckerRuntime:
    """Native reads are repeatable. Unrecorded model calls remain unresolved."""

    def load_workbook_auto(self, file_ref: FileRef) -> Any:
        if Path(file_ref.location).suffix.lower() == ".pdf":
            raise GuardStop(Decision.UNABLE, "PDF 必须提供本次文件的 PdfSpec")
        return load_excel_workbook(file_ref.location)

    def convert_pdf_to_excel(
        self,
        file_ref: FileRef,
        *,
        enable_cache: bool = True,
        spec: Any = None,
        doc_type: object | None = None,
    ) -> ConversionArtifact:
        if spec is None:
            raise GuardStop(Decision.UNABLE, "PDF 必须提供本次文件的 PdfSpec")
        return convert_pdf_to_excel_auto(file_ref, enable_cache=enable_cache, spec=spec, doc_type=doc_type)

    def render_pdf_images(self, file_url: str, *, render_all_pages: bool = True) -> list[str]:
        from documents.images.image import extract_images_from_bytes

        return extract_images_from_bytes(Path(file_url).read_bytes(), "pdf", render_all_pages=render_all_pages)

    def read_paddle_page(self, file_url: str, page_number: int) -> dict[str, Any]:
        raise GuardStop(Decision.UNABLE, "固定 checker 没有可重放的视觉观察（Paddle），需补充证据")

    def run_image_prompt(self, **kwargs: Any) -> str:
        # Stored checker code can name the retired hook. It has no transport.
        raise GuardStop(Decision.UNABLE, "固定 checker 没有可重放的视觉观察，需补充证据")

    def read_ocr_visual_facts(self, *, images: list[bytes], prompt: str, response_schema: dict[str, Any]) -> dict[str, Any]:
        raise GuardStop(Decision.UNABLE, "固定 checker 没有可重放的 OCR 视觉观察，需补充证据")
