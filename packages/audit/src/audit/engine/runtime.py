"""Runtime hook protocol for engine-side checkers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Protocol

from documents.contracts import (
    ConversionArtifact,
    FileRef,
)


class AuditRuntime(Protocol):
    """Optional runtime capabilities provided by app orchestrator."""

    def load_workbook_auto(self, file_ref: FileRef) -> Any: ...

    def convert_pdf_to_excel(
        self,
        file_ref: FileRef,
        *,
        enable_cache: bool = True,
        spec: Any = None,
        doc_type: object | None = None,
    ) -> ConversionArtifact: ...

    def render_pdf_images(self, file_url: str, *, render_all_pages: bool = True) -> list[str]: ...

    def read_paddle_page(self, file_url: str, page_number: int) -> dict[str, Any]: ...

    def read_ocr_visual_facts(self, *, images: list[bytes], prompt: str, response_schema: dict[str, Any]) -> dict[str, Any]: ...


_RUNTIME_VAR: ContextVar[AuditRuntime | None] = ContextVar("ppap_audit_runtime", default=None)


def get_runtime() -> AuditRuntime | None:
    return _RUNTIME_VAR.get()


@contextmanager
def bind_runtime(runtime: AuditRuntime | None) -> Iterator[None]:
    token = _RUNTIME_VAR.set(runtime)
    try:
        yield
    finally:
        _RUNTIME_VAR.reset(token)
