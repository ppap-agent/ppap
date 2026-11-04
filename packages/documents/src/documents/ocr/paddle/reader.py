"""Explicit original-page Paddle evidence; no model or whole-file fallback."""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from documents.ocr.env import paddle_evidence_enabled

from .client import PaddleOCRFormatError, PaddleStructureService, validate_pdf
from .evidence import build_evidence


class PaddleEvidenceUnavailable(ValueError):
    """The requested capability is disabled or cannot establish a fact."""

    def __init__(self, message: str, *, code: str = "paddle_evidence_unavailable") -> None:
        self.code = code
        super().__init__(message)


@dataclass
class _PageScope:
    max_requests: int
    attempts: dict[tuple, dict | Exception] = field(default_factory=dict)
    request_count: int = 0
    closed: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)


_PAGE_SCOPE: ContextVar[_PageScope | None] = ContextVar("paddle_page_scope", default=None)


@contextmanager
def paddle_page_scope(*, max_requests: int = 2) -> Iterator[None]:
    """Reuse successful and failed page attempts within one authorized audit.

    This limit belongs to this task only, not global service capacity. Nested
    helper calls share their owner's scope; nothing persists across audits.
    """
    if type(max_requests) is not int or max_requests < 1:
        raise ValueError("A positive Paddle request count is required")
    if _PAGE_SCOPE.get() is not None:
        yield
        return
    scope = _PageScope(max_requests=max_requests)
    token = _PAGE_SCOPE.set(scope)
    try:
        yield
    finally:
        with scope.lock:
            scope.closed = True
            scope.attempts.clear()
        _PAGE_SCOPE.reset(token)


def read_pdf_page_evidence(file_path: str | Path, page_number: int) -> dict:
    """Read one explicitly selected page and retain its original identity.

    The authorized caller owns page selection, task scope and business
    interpretation. An empty response does not establish absent content.
    """
    if not paddle_evidence_enabled():
        raise PaddleEvidenceUnavailable("Paddle证据尚未启用，需核对原件。", code="paddle_evidence_disabled")
    endpoint = os.environ.get("PADDLE_OCR_URL", "").strip()
    if not endpoint:
        raise PaddleEvidenceUnavailable("Paddle服务尚未配置，需核对原件。", code="paddle_evidence_unconfigured")
    source, page_count = validate_pdf(file_path)
    if type(page_number) is not int or not 1 <= page_number <= page_count:
        raise ValueError("An existing original PDF page must be explicitly selected")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    credential = os.environ.get("PADDLE_TOKEN", "")

    def read() -> dict:
        service = PaddleStructureService(endpoint, token=credential, timeout=20)
        response = service.analyze_page(source, page_number)
        evidence = build_evidence(response, page_count=1, pdf_path=source, source_pages=[page_number])
        if evidence["source_sha256"] != source_hash:
            raise PaddleOCRFormatError("PaddleOCR source changed during the request")
        return evidence

    scope = _PAGE_SCOPE.get()
    if scope is None:
        return read()
    service_identity = hashlib.sha256((endpoint + "\0" + credential).encode()).digest()
    key = (source_hash, page_number, service_identity, "paddleocr_ppstructure_v3", 20)
    with scope.lock:
        if scope.closed:
            raise PaddleEvidenceUnavailable("本次审核的Paddle证据作用域已结束。", code="paddle_scope_closed")
        # A source can change while another page request owns the task lock.
        if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
            raise PaddleOCRFormatError("PaddleOCR source changed before evidence reuse")
        if key not in scope.attempts:
            if scope.request_count >= scope.max_requests:
                raise PaddleEvidenceUnavailable("本次审核的Paddle请求数已达上限；其余页面尚未核实。", code="paddle_request_budget_exhausted")
            scope.request_count += 1
            scope.attempts[key] = PaddleOCRFormatError("PaddleOCR previous request did not complete")
            try:
                scope.attempts[key] = read()
            except Exception as exc:
                # Unknown remote outcomes are not sent again by a second rule.
                scope.attempts[key] = exc
                raise
        attempt = scope.attempts[key]
        if isinstance(attempt, Exception):
            raise attempt
        # Export-local diagnostics must not mutate another rule's page facts.
        return deepcopy(attempt)
