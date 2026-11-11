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

from audit.contracts import AuditEvidence, EvidenceDiagnostic, EvidenceSource, RuleInput

PART_NUMBER_LABELS = ("零件号", "零件号/最新更改水平")
PSW_PART_NUMBER_LABELS = (*PART_NUMBER_LABELS, "客户零件号", "理想汽车零件号")


def psw_shadow_clause_id(rule: RuleInput, rule_no: int) -> str:
    """Bind a selected PSW fact to its actual frozen source clause and slot."""
    if isinstance(rule_no, bool) or not isinstance(rule_no, int) or not 1 <= rule_no <= len(rule.rules):
        raise ValueError("A source PSW rule slot is required")
    item = rule.rules[rule_no - 1]
    if not rule.rule_id or not isinstance(item.get("id"), str) or not item["id"].strip():
        raise ValueError("A frozen rule version and source clause ID are required")
    source = {"clause": item, "document_type": rule.type, "preset_key": rule.preset_key}
    digest = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return "psw:" + json.dumps([rule.rule_profile, rule.rule_id, rule_no, item["id"], digest], ensure_ascii=False, separators=(",", ":"))


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
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        if not project_id or isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
            raise ValueError("A project and positive request budget are required")
        self.project_id = project_id
        self.targets = tuple(targets)
        if len({(t.file_version_id, t.clause_id) for t in targets}) != len(targets):
            raise ValueError("Each selected clause must have one explicit page")
        self.service = service
        self._service_identity = (id(service), getattr(service, "endpoint", None), getattr(service, "timeout", None))
        self.max_requests = max_requests
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
        return None

    def _identity(self, agent, target: ShadowTarget) -> str | None:
        import fitz

        if agent.audit_file.project_id != self.project_id:
            return "scope_mismatch"
        if target.clause_id.startswith("psw:") and target.clause_id not in {psw_shadow_clause_id(agent.rule, i) for i in range(1, len(agent.rule.rules) + 1)}:
            return "rule_version_mismatch"
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
            if state in {"known", "skipped"}:
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
        from audit.checker_lib.uaes.bindings import BINDINGS

        clause: str | None
        kind: str | None
        labels: tuple[str, ...]
        if agent.rule.preset_key == "U17":
            clauses = [c for c, m in BINDINGS["v4"].items() if c.startswith("U17:") and m == method]
            slots = [i for i, item in enumerate(agent.rule.rules, 1) if item.get("id") in clauses]
            if len(slots) != 1:
                return None
            try:
                clause = psw_shadow_clause_id(agent.rule, slots[0])
            except (TypeError, ValueError):
                return None
            kind = {"rule_3": "seal", "rule_1": "part_number"}.get(method)
            labels = PSW_PART_NUMBER_LABELS
        elif agent.rule.preset_key == "U08" and method in {"rule_3", "rule_6"}:
            clauses = [c for c, m in BINDINGS["v4"].items() if c.startswith("U08:") and m == method]
            clause = next((t.clause_id for t in self.targets if t.file_version_id == agent.audit_file.audit_id and t.clause_id in clauses), None)
            kind = "seal" if method == "rule_3" else "part_number"
            labels = PART_NUMBER_LABELS
        else:
            return None
        target = next((t for t in self.targets if t.file_version_id == agent.audit_file.audit_id and t.clause_id == clause), None)
        return self._observe(agent, target, kind, labels) if target else None

    def observe_psw(self, agent, rule_no: int) -> ShadowObservation | None:
        """Observe selected PSW facts without invoking the formal model path."""
        from audit.kinds import PpapDocumentType

        if PpapDocumentType.from_string(agent.rule.type or "") != PpapDocumentType.PSW:
            return None
        if agent.rule.rule_profile == "v4" and agent.rule.preset_key != "L17":
            return None
        try:
            clause = psw_shadow_clause_id(agent.rule, rule_no)
        except (TypeError, ValueError):
            return None
        target = next((t for t in self.targets if t.file_version_id == agent.audit_file.audit_id and t.clause_id == clause), None)
        kind = {3: "seal", 6: "part_number"}.get(rule_no)
        return self._observe(agent, target, kind, PSW_PART_NUMBER_LABELS) if target else None

    def _observe(self, agent, target: ShadowTarget, kind: str | None, labels: tuple[str, ...]) -> ShadowObservation:
        from audit.checker_lib.uaes.native import NativeFacts, UnknownFact

        with self._lock:
            try:
                stopped = self._stopped() or self._identity(agent, target)
                if stopped:
                    return self._record(target, "error", stopped)
                if kind is None:
                    return self._record(target, "unknown", "unsupported_visual_fact")
                if kind == "part_number":
                    # A separate native reader avoids consuming the formal
                    # Checker's witness/table budgets or changing its result.
                    native = NativeFacts(agent.audit_file.file_url, target.file_version_id, lambda *a, **k: None)
                    try:
                        native.field(labels)
                    except UnknownFact:
                        pass
                    else:
                        stopped = self._stopped() or self._identity(agent, target)
                        return self._record(target, "error", stopped) if stopped else self._record(target, "skipped", "native_sufficient")
                # Native parsing can race a cancellation/source change.
                # Recheck before reserving work; elapsed task time is not an
                # additional OCR deadline.
                stopped = self._stopped() or self._identity(agent, target)
                if stopped:
                    return self._record(target, "error", stopped)
                key = (target.source_sha256, target.page_number)
                reused = key in self._attempts
                if reused:
                    attempt = self._attempts[key]
                else:
                    from documents.ocr.paddle import build_evidence

                    # Recheck after local preparation, before reserving or
                    # dispatching external work.
                    began = time.monotonic()
                    stopped = self._stopped()
                    if stopped:
                        return self._record(target, "error", stopped)
                    if self.request_count >= self.max_requests:
                        return self._record(target, "error", "request_budget_exhausted")
                    # Reservation and the eventual result (including failure)
                    # stay in this task, preventing another rule from resending.
                    self.request_count += 1
                    self._attempts[key] = _PageAttempt(None, "request_incomplete", 0)
                    try:
                        raw = self.service.analyze_page(agent.audit_file.file_url, target.page_number, timeout=20)
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
                return self._consume(target, kind, attempt, reused, labels)
            except Exception:
                return self._record(target, "error", "source_unavailable")

    def _consume(self, target: ShadowTarget, kind: str, attempt: _PageAttempt, reused: bool, labels: tuple[str, ...]) -> ShadowObservation:
        from audit.checker_lib.uaes.native import header
        from audit.pdf_tables import valid_pdf_bbox

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

        def locate(item: dict[str, Any], index: int, element: str) -> dict[str, Any]:
            return {
                "kind": "pdf_region",
                "page": target.page_number,
                "file_version_id": target.file_version_id,
                "source_sha256": target.source_sha256,
                "clause_id": target.clause_id,
                "engine": value.get("engine"),
                "bbox": item["pdf_bbox"],
                "coordinate_system": "pdf_points_unrotated",
                "element_index": index,
                "region_index": item.get("region_index"),
                "recognition_index": item.get("recognition_index"),
                "element": element,
            }

        def add(fact: dict[str, Any], item: dict[str, Any], index: int) -> bool:
            if not item.get("pdf_bbox"):
                return False
            locator = locate(item, index, "seal" if kind == "seal" else "table")
            facts.append(fact)
            sources.append(EvidenceSource(kind="visual", locator=json.dumps(locator, separators=(",", ":")), excerpt=str(fact.get("value", " ".join(fact.get("text", []))))[:500]))
            return True

        def region_fragments(seal: dict[str, Any], seal_index: int) -> list[dict[str, Any]]:
            # Page OCR inside a detected region is a separate observation. It
            # does not identify the ink origin or complete the seal wording.
            frame = page.get("frame") or {}
            width, height = frame.get("pdf_width"), frame.get("pdf_height")
            bounds = seal.get("pdf_bbox")
            if not valid_pdf_bbox(bounds, width, height):
                return []
            words: dict[int, dict[str, Any]] = {}
            ambiguous = set()
            for word in page.get("texts", []):
                if word.get("type") != "ocr_word" or word.get("source") != "paddle_overall_ocr" or type(word.get("index")) is not int or word["index"] < 0:
                    continue
                if word["index"] in words:
                    ambiguous.add(word["index"])
                words[word["index"]] = word
            linked = seal.get("supplemental_text_indices", [])
            if not isinstance(linked, list):
                return []
            fragments = []
            for word_index in dict.fromkeys(index for index in linked if type(index) is int and index >= 0):
                word = words.get(word_index)
                if word is None or word_index in ambiguous or not isinstance(word.get("text"), str) or not word["text"].strip():
                    continue
                word_bounds = word.get("pdf_bbox")
                if not isinstance(word_bounds, list) or not valid_pdf_bbox(word_bounds, width, height):
                    continue
                owners = [
                    candidate
                    for candidate in page.get("seals", [])
                    if candidate.get("detected") is True
                    and valid_pdf_bbox(candidate.get("pdf_bbox"), width, height)
                    and candidate["pdf_bbox"][0] <= word_bounds[0]
                    and candidate["pdf_bbox"][1] <= word_bounds[1]
                    and candidate["pdf_bbox"][2] >= word_bounds[2]
                    and candidate["pdf_bbox"][3] >= word_bounds[3]
                ]
                if len(owners) != 1 or owners[0] is not seal:
                    continue
                fragments.append(
                    {
                        "source": "paddle_overall_ocr",
                        "index": word_index,
                        "text": word["text"],
                        "page": target.page_number,
                        "pdf_bbox": word_bounds,
                        "coordinate_system": "pdf_points_unrotated",
                        "association": "geometric_containment_only",
                    }
                )
                locator = locate(word, word_index, "ocr_word")
                locator.update(source="paddle_overall_ocr", seal_index=seal_index, association="geometric_containment_only")
                sources.append(EvidenceSource(kind="visual", locator=json.dumps(locator, separators=(",", ":")), excerpt=word["text"][:500]))
            return fragments

        if kind == "seal":
            for index, seal in enumerate(page.get("seals", [])):
                if seal.get("detected") is True:
                    fact = {"detected": True, "text": seal.get("recognized_text", []), "text_status": seal.get("text_status", "unreadable")}
                    if add(fact, seal, index):
                        fragments = region_fragments(seal, index)
                        if fragments:
                            fact["region_text_fragments"] = fragments
        else:
            for index, table in enumerate(page.get("tables", [])):
                for row_number, row in enumerate(table.get("rows", []), 1):
                    for column, text in enumerate(row[:-1]):
                        if not header(text, labels):
                            continue
                        merged = next((span for span in table.get("merges", []) if span[0] <= row_number <= span[2] and span[1] <= column + 1 <= span[3]), None)
                        # Merges use one-based inclusive columns. The first
                        # column after their end is the zero-based value index.
                        value_column = merged[3] if merged else column + 1
                        if value_column < len(row) and str(row[value_column]).strip():
                            add({"field": "零件号", "source_label": str(text).strip(), "value": str(row[value_column]).strip()}, table, index)
            if len({f["value"] for f in facts}) > 1:
                # Conflicting alternatives remain unresolved rather than
                # lending one candidate to an authoritative field.
                return self._record(target, "unknown", "field_ambiguous", diagnostics=diagnostics, reused=reused, elapsed=attempt.elapsed_seconds)
        element = "seal" if kind == "seal" else "table"
        failed = not facts and any(d.get("element") == element and d.get("outcome", "error") != "unknown" for d in value.get("diagnostics", []))
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


def observe_psw_shadow(agent, rule_no: int) -> None:
    session = _CURRENT.get()
    if session is not None:
        session.observe_psw(agent, rule_no)
