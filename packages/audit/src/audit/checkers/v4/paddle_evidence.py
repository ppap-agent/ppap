"""Explicit task-local Paddle observations; never a formal verdict or fallback.

The caller owns authorization and supplies frozen versions and selected pages.
One session is one shadow task. Its lock serializes its own calls, not the
capacity of the remote service or of other Worker processes.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from audit.contracts import AuditEvidence, EvidenceDiagnostic, EvidenceSource

PART_NUMBER_LABELS = ("零件号", "零件号/最新更改水平")


class PageService(Protocol):
    def analyze_page(self, file_path: str | Path, page_number: int, *, timeout: float | None = None) -> dict: ...


@dataclass(frozen=True)
class ShadowTarget:
    file_version_id: str
    source_sha256: str
    clause_id: str
    page_number: int

    def __post_init__(self) -> None:
        if not self.file_version_id or not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise ValueError("A frozen file version and SHA are required")
        if isinstance(self.page_number, bool) or not isinstance(self.page_number, int) or self.page_number < 1:
            raise ValueError("An explicit positive original page number is required")


@dataclass(frozen=True)
class ShadowObservation:
    target: ShadowTarget
    state: Literal["known", "unknown", "error", "skipped"]
    reason: str
    facts: tuple[dict[str, Any], ...]
    evidence: AuditEvidence
    reused: bool = False
    elapsed_seconds: float = 0


@dataclass(frozen=True)
class _PageAttempt:
    evidence: dict[str, Any] | None
    error: str | None
    elapsed_seconds: float


_CURRENT: ContextVar[PaddleShadowSession | None] = ContextVar("paddle_shadow", default=None)


class PaddleShadowSession:
    def __init__(
        self,
        service: PageService,
        *,
        project_id: str,
        targets: Sequence[ShadowTarget],
        max_requests: int = 2,
        deadline_seconds: float = 60,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        if not project_id or isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
            raise ValueError("A project and positive request budget are required")
        if deadline_seconds < 0 or not float(deadline_seconds) < float("inf"):
            raise ValueError("A finite nonnegative deadline is required")
        self.project_id = project_id
        self.targets = tuple(targets)
        if len({(t.file_version_id, t.clause_id) for t in targets}) != len(targets):
            raise ValueError("Each selected clause must have one explicit page")
        self.service = service
        self._service_identity = (id(service), getattr(service, "endpoint", None), getattr(service, "timeout", None))
        self.max_requests = max_requests
        self.deadline = time.monotonic() + deadline_seconds
        self.cancelled = cancelled or (lambda: False)
        self.request_count = 0
        self.observations: list[ShadowObservation] = []
        self.closed = False
        self.active = False
        self._entered = False
        self._lock = threading.RLock()
        self._publication = threading.Lock()
        self._attempts: dict[tuple[str, int], _PageAttempt] = {}

    def _stopped(self) -> str | None:
        if self.closed or not self.active:
            return "scope_closed"
        if self.cancelled():
            return "cancelled"
        if time.monotonic() >= self.deadline:
            return "deadline_exceeded"
        return None

    def _identity(self, agent, target: ShadowTarget) -> str | None:
        import fitz

        if agent.audit_file.project_id != self.project_id:
            return "scope_mismatch"
        if (id(self.service), getattr(self.service, "endpoint", None), getattr(self.service, "timeout", None)) != self._service_identity:
            return "service_changed"
        version = agent.audit_file.file_metadata.get("file_version_id", target.file_version_id)
        if version != target.file_version_id:
            return "source_version_mismatch"
        path = Path(agent.audit_file.file_url)
        if path.suffix.casefold() != ".pdf":
            return "unsupported_source"
        # The local file was resolved by the existing authorized caller; no URL
        # download or second object lookup occurs at this seam.
        if path.stat().st_size > 100 * 1024 * 1024:
            return "source_limit"
        with fitz.open(path) as pdf:
            if not pdf.is_pdf or pdf.needs_pass or not 1 <= target.page_number <= len(pdf):
                return "source_page_invalid"
        with path.open("rb") as content:
            digest = hashlib.file_digest(content, "sha256").hexdigest()
        return None if digest == target.source_sha256 else "source_changed"

    def _record(self, target, state, reason, *, facts=(), sources=(), diagnostics=(), reused=False, elapsed=0) -> ShadowObservation:
        with self._publication:
            if state == "known":
                stopped = self._stopped()
                if stopped:
                    state, reason, facts, sources = "error", stopped, (), ()
            return self._append(target, state, reason, facts=facts, sources=sources, diagnostics=diagnostics, reused=reused, elapsed=elapsed)

    def _append(self, target, state, reason, *, facts=(), sources=(), diagnostics=(), reused=False, elapsed=0) -> ShadowObservation:
        technical = state == "error"
        notice = EvidenceDiagnostic(
            code="paddle.shadow." + reason,
            stage="visual",
            severity="error" if technical else "info",
            message="Paddle影子事实；不改变正式审核结论。" if not technical else "Paddle影子提取未完成；尚未形成业务结论。",
        )
        evidence = AuditEvidence(
            required="structure_or_visual",
            used="visual" if sources else "none",
            source_format="pdf",
            prepared_format="pdf",
            sources=tuple(sources),
            diagnostics=tuple(diagnostics) + (notice,),
        )
        result = ShadowObservation(target, state, reason, tuple(facts), evidence, reused, elapsed)
        self.observations.append(result)
        return result

    def observe(self, agent, method: str) -> ShadowObservation | None:
        from audit.checkers.v4.uaes.bindings import BINDINGS
        from audit.checkers.v4.uaes.native import NativeFacts, UnknownFact

        if agent.rule.preset_key != "U08" or method not in {"rule_3", "rule_6"}:
            return None
        clauses = [c for c, m in BINDINGS["v4"].items() if c.startswith("U08:") and m == method]
        target = next((t for t in self.targets if t.file_version_id == agent.audit_file.audit_id and t.clause_id in clauses), None)
        if target is None:
            return None
        with self._lock:
            try:
                stopped = self._stopped() or self._identity(agent, target)
                if stopped:
                    return self._record(target, "error", stopped)
                if method == "rule_6":
                    # A separate native reader avoids consuming the formal
                    # Checker's witness/table budgets or changing its result.
                    native = NativeFacts(agent.audit_file.file_url, target.file_version_id, lambda *a, **k: None)
                    try:
                        native.field(PART_NUMBER_LABELS)
                    except UnknownFact:
                        pass
                    else:
                        return self._record(target, "skipped", "native_sufficient")
                key = (target.source_sha256, target.page_number)
                reused = key in self._attempts
                if reused:
                    attempt = self._attempts[key]
                else:
                    if self.request_count >= self.max_requests:
                        return self._record(target, "error", "request_budget_exhausted")
                    # Reservation and the eventual result (including failure)
                    # stay in this task, preventing another rule from resending.
                    self.request_count += 1
                    began = time.monotonic()
                    self._attempts[key] = _PageAttempt(None, "request_incomplete", 0)
                    try:
                        from documents.ocr.paddle import build_evidence

                        raw = self.service.analyze_page(agent.audit_file.file_url, target.page_number, timeout=max(0.001, self.deadline - time.monotonic()))
                        stopped = self._stopped() or self._identity(agent, target)
                        if stopped:
                            attempt = _PageAttempt(None, stopped, time.monotonic() - began)
                        else:
                            value = build_evidence(raw, page_count=1, pdf_path=agent.audit_file.file_url, source_pages=[target.page_number])
                            stopped = self._stopped() or self._identity(agent, target)
                            if value.get("source_sha256") != target.source_sha256:
                                stopped = "source_changed"
                            attempt = _PageAttempt(None if stopped else value, stopped, time.monotonic() - began)
                    except Exception:
                        # Never put exception text, source contents or signed
                        # endpoint details in a customer-visible diagnostic.
                        attempt = _PageAttempt(None, "request_failed", time.monotonic() - began)
                    self._attempts[key] = attempt
                stopped = self._stopped() or self._identity(agent, target)
                if stopped or attempt.error:
                    return self._record(target, "error", stopped or attempt.error, reused=reused, elapsed=attempt.elapsed_seconds)
                return self._consume(target, method, attempt, reused)
            except Exception:
                return self._record(target, "error", "source_unavailable")

    def _consume(self, target: ShadowTarget, method: str, attempt: _PageAttempt, reused: bool) -> ShadowObservation:
        from audit.checkers.v4.uaes.native import header

        value = attempt.evidence or {}
        pages = value.get("pages", [])
        if len(pages) != 1 or pages[0].get("page") != target.page_number:
            return self._record(target, "error", "page_identity_invalid", reused=reused)
        page = pages[0]
        facts: list[dict[str, Any]] = []
        sources: list[EvidenceSource] = []
        diagnostics = []
        for item in value.get("diagnostics", []):
            diagnostics.append(EvidenceDiagnostic(code=str(item.get("code", "paddle.element.invalid")), stage="structure", severity="warning", message="本页部分OCR元素不可用；其他已确认事实保留。"))

        def add(fact: dict[str, Any], item: dict[str, Any], index: int) -> None:
            if not item.get("pdf_bbox"):
                return
            locator = {
                "kind": "pdf_region",
                "page": target.page_number,
                "file_version_id": target.file_version_id,
                "source_sha256": target.source_sha256,
                "engine": value.get("engine"),
                "bbox": item["pdf_bbox"],
                "coordinate_system": "pdf_points_unrotated",
                "element_index": index,
                "region_index": item.get("region_index"),
                "recognition_index": item.get("recognition_index"),
                "element": "seal" if method == "rule_3" else "table",
            }
            facts.append(fact)
            sources.append(EvidenceSource(kind="visual", locator=json.dumps(locator, separators=(",", ":")), excerpt=str(fact.get("value", " ".join(fact.get("text", []))))[:500]))

        if method == "rule_3":
            for index, seal in enumerate(page.get("seals", [])):
                if seal.get("detected") is True:
                    add({"detected": True, "text": seal.get("recognized_text", []), "text_status": seal.get("text_status", "unreadable")}, seal, index)
        else:
            for index, table in enumerate(page.get("tables", [])):
                for row in table.get("rows", []):
                    for column, text in enumerate(row[:-1]):
                        if header(text, PART_NUMBER_LABELS) and str(row[column + 1]).strip():
                            add({"field": "零件号", "source_label": str(text).strip(), "value": str(row[column + 1]).strip()}, table, index)
            if len({f["value"] for f in facts}) > 1:
                # Conflicting alternatives remain unresolved rather than
                # lending one candidate to an authoritative field.
                return self._record(target, "unknown", "field_ambiguous", diagnostics=diagnostics, reused=reused, elapsed=attempt.elapsed_seconds)
        kind = "seal" if method == "rule_3" else "table"
        failed = not facts and any(d.get("element") == kind and d.get("outcome", "error") != "unknown" for d in value.get("diagnostics", []))
        return self._record(
            target,
            "known" if facts else "error" if failed else "unknown",
            "facts_observed" if facts else "element_failed" if failed else "fact_unconfirmed",
            facts=facts,
            sources=sources,
            diagnostics=diagnostics,
            reused=reused,
            elapsed=attempt.elapsed_seconds,
        )


@contextmanager
def paddle_shadow_scope(session: PaddleShadowSession) -> Iterator[PaddleShadowSession]:
    if session._entered or session.closed:
        raise ValueError("A shadow session cannot be reused by another task")
    session._entered = True
    session.active = True
    token = _CURRENT.set(session)
    try:
        yield session
    finally:
        # Set the stop flag without waiting for an in-flight request lock.
        with session._publication:
            session.closed = True
            session.active = False
        _CURRENT.reset(token)


def observe_shadow(agent, method: str) -> None:
    session = _CURRENT.get()
    if session is not None:
        session.observe(agent, method)
