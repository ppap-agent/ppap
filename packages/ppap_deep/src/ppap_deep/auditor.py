"""Product versions -> real semantic auditor -> validated product findings."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path
from typing import Literal, Protocol, cast
from uuid import UUID

from ppap_core.files import ObjectExists, ObjectMissing

from ppap_deep.client import DeepError
from ppap_deep.controller import ExecutionContext
from ppap_deep.limits import DEEP_TOTAL_SECONDS
from ppap_deep.protocol import DeepCoverage, DeepFinding, DeepVerification, ExecuteRequest, ExecuteResponse
from ppap_deep.semantic.evidence import EvidenceBudget, resolve_optional
from ppap_deep.semantic.guest import Manifest
from ppap_deep.semantic.protocol import ControllerFault, DocumentRef, ResultBundle, RuleRef, SubmissionSpec
from ppap_deep.semantic.secure_files import read_json
from ppap_deep.semantic.validation import validate_bundle

POLICY_REVISION = hashlib.sha256(b"ppap-effective-rules-full-coverage-v3").hexdigest()
FORMATS = {".xlsx", ".xls", ".xlsm", ".docx", ".pdf"}
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
DIAGNOSTIC_LABELS = {
    "parser_unknown": "部分文件内容无法可靠读取，请核对原件。",
    "unresolved_relation": "资料之间的对应关系尚未确认，请核对零件与版本。",
    "visual_only_evidence": "此项依赖勾选或图像内容，当前无法可靠判读，请人工核对。",
    "budget_exhausted": "已达到本轮执行上限，此项尚未完成。",
    "unsupported_structure": "当前无法可靠识别所需结构，请核对资料或规则。",
    "rule_ambiguity": "规则对这种情况的要求尚不明确，请确认适用条件。",
}


class Objects(Protocol):
    def get(self, key: str) -> bytes: ...
    def put_exclusive(self, key: str, body: bytes, content_type: str) -> None: ...


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def semantic_inputs(spec: ExecuteRequest, engine_revision: str) -> tuple[SubmissionSpec, dict[str, bytes]]:
    """Effective published rules are authoritative; original files are provenance only."""
    documents = []
    rules: dict[str, RuleRef] = {}
    packets: dict[str, bytes] = {}
    targets = {}
    for item in spec.files:
        extension = Path(item.filename).suffix.lower()
        if extension not in FORMATS:
            raise DeepError("deep_input_invalid")
        file_format = cast(Literal[".xlsx", ".xlsm", ".xls", ".docx", ".pdf"], extension)
        role = cast(Literal["target", "context"], "target" if item.role == "target" else "context")
        documents.append(DocumentRef(audit_id=item.file_version_id, filename=item.filename, sha256=item.sha256, format=file_format, role=role))
        if item.role != "target":
            continue
        body = item.rule_body or {}
        clauses = body.get("rules")
        if not item.rule_version_id or not isinstance(clauses, list) or not clauses:
            raise DeepError("deep_rules_empty")
        if any(
            not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip() or not isinstance(row.get("rule_to_check"), str) or not row["rule_to_check"].strip()
            for row in clauses
        ):
            raise DeepError("deep_input_invalid")
        packet = {"kind": "published_rule", "rule_version_id": item.rule_version_id, "rule_body": body, "clauses": clauses}
        if item.rule_source_file_version_id:
            packet["origin"] = {"file_version_id": item.rule_source_file_version_id}
        raw = encoded(packet)
        if item.rule_version_id in packets and packets[item.rule_version_id] != raw:
            raise DeepError("deep_integrity_mismatch")
        digest = hashlib.sha256(raw).hexdigest()
        revision = hashlib.sha256(encoded([digest, POLICY_REVISION, engine_revision])).hexdigest()
        rules[item.rule_version_id] = RuleRef(rule_id=item.rule_version_id, rule_version_id=item.rule_version_id, sha256=digest, format=".json", plan_revision=revision, clauses=clauses)
        packets[item.rule_version_id] = raw
        targets[item.file_version_id] = item.rule_version_id
    try:
        submission = SubmissionSpec(
            execution_id=UUID(int=0),
            submission_id=UUID(spec.round_id),
            project_id=spec.tenant_id,
            documents=documents,
            rules=list(rules.values()),
            targets=targets,
            policy_revision=POLICY_REVISION,
            semantic_engine_revision=engine_revision,
        )
    except ValueError as exc:
        raise DeepError("deep_input_invalid") from exc
    return submission.model_copy(update={"execution_id": submission.expected_execution_id()}), packets


def product_response(spec: ExecuteRequest, bundle: ResultBundle, submission: SubmissionSpec) -> ExecuteResponse:
    expected, _packets = semantic_inputs(spec, bundle.semantic_engine_revision)
    if submission != expected:
        raise ValueError("Validated bundle does not belong to the frozen request")
    evidence_gaps = validate_bundle(bundle, submission)
    files = {item.file_version_id: item for item in spec.files}
    proofs = {(proof.audit_id, proof.rule_id): proof.kind for proof in bundle.trace.proofs}
    findings, rule_items = [], []
    evidence_budget = EvidenceBudget()
    for check in bundle.report.checks:
        target = files[check.audit_id]
        clause = next(row for row in (target.rule_body or {})["rules"] if row["id"] == check.rule_id)
        sources = []
        for citation in check.evidence:
            source = files[citation.document_id]
            observed = resolve_optional(bundle.documents[citation.document_id], citation.locator, evidence_budget)
            if observed is None:
                evidence_gaps.add((check.audit_id, check.rule_id))
                continue
            excerpt = " · ".join(observed.values)
            if len(excerpt) > 4000:
                excerpt = excerpt[:4000] + "…"
            sources.append({"file_version_id": source.file_version_id, "filename": source.filename, "role": source.role, "locator": citation.locator.model_dump(), "excerpt": excerpt})
        semantic = {
            "rule_version_id": target.rule_version_id,
            "clause_id": check.rule_id,
            "rule_text": clause["rule_to_check"],
            "scope": {"total": check.total, "checked": check.checked, "unknown": check.unknown},
            "sources": sources,
        }
        if (check.audit_id, check.rule_id) in evidence_gaps:
            semantic["evidence_incomplete"] = True
        if check.diagnostic:
            semantic["diagnostic_code"] = check.diagnostic
            semantic["diagnostic"] = DIAGNOSTIC_LABELS[check.diagnostic]
        if target.rule_source_file_version_id:
            semantic["rule_source_file_version_id"] = target.rule_source_file_version_id
        item_key = f"{target.file_version_id}:rule:{check.rule_id}"
        findings.append(
            DeepFinding(
                item_key=item_key,
                classification=str(clause.get("category") or "客户规则"),
                item=clause["rule_to_check"],
                priority=str(clause.get("priority") or "P1"),
                status=check.status,
                suggestions=check.reason,
                locator={
                    "kind": "file_version",
                    "file_id": target.file_id,
                    "file_version_id": target.file_version_id,
                    "filename": target.filename,
                    "rule_item_id": check.rule_id,
                    "semantic": semantic,
                },
            )
        )
        rule_items.append(
            {
                "file_version_id": target.file_version_id,
                "filename": target.filename,
                "rule_version_id": target.rule_version_id,
                "rule_item_id": check.rule_id,
                "id": check.rule_id,
                "category": str(clause.get("category") or "客户规则"),
                "rule_to_check": clause["rule_to_check"],
                "item_key": item_key,
                "coverage": "executed",
                "status": check.status,
                "proof_kind": proofs[check.audit_id, check.rule_id],
            }
        )
    return ExecuteResponse(
        protocol_revision=spec.protocol_revision,
        round_id=spec.round_id,
        files=spec.files,
        findings=findings,
        note="deep_semantic_audit",
        usage={
            "input_tokens": bundle.input_tokens,
            "output_tokens": bundle.output_tokens,
            "tool_calls": bundle.tool_calls,
            "completed_tool_calls": bundle.completed_tool_calls,
            "compactions": bundle.compactions,
        },
        coverage=DeepCoverage(
            checked=[item.filename for item in spec.files if item.role == "target"],
            unchecked=[f"{item.filename}（参考资料，不单独作完整审核）" for item in spec.files if item.role == "reference"],
            locator_gap=bool(evidence_gaps) or any(not (row.locator or {})["semantic"]["sources"] for row in findings),
            rule_items=rule_items,
            verification=None
            if evidence_gaps
            else DeepVerification(
                method="host_source_trace_v1",
                round_id=spec.round_id,
                job_id=spec.job_id,
                attempt=spec.attempt,
                executor_revision=spec.executor_revision,
                semantic_engine_revision=bundle.semantic_engine_revision,
            ),
        ),
    )


FAULTS = {
    "INPUT_INTEGRITY_ERROR": "deep_integrity_mismatch",
    "PARSER_LIMIT": "deep_parser_limit",
    "MODEL_TIMEOUT": "deep_timeout",
    "MODEL_PROTOCOL_ERROR": "deep_model_protocol",
    "OUTPUT_VALIDATION_ERROR": "deep_output_invalid",
    "EVIDENCE_VALIDATION_ERROR": "deep_output_invalid",
    "RESOURCE_LIMIT": "deep_budget_exhausted",
    "CANCELLED": "deep_cancelled",
    "ISOLATION_UNAVAILABLE": "deep_runtime_unavailable",
    "CONTROLLER_UNAVAILABLE": "deep_runtime_unavailable",
}


class SemanticRuntime:
    """One adapter, existing storage and queue; no second task or artifact registry."""

    def __init__(self, runtime, objects: Objects, *, timeout: float = DEEP_TOTAL_SECONDS):
        self.runtime, self.objects, self.timeout = runtime, objects, timeout

    @staticmethod
    def _prefix(spec: ExecuteRequest, submission: SubmissionSpec) -> str:
        tenant = hashlib.sha256(spec.tenant_id.encode()).hexdigest()
        return f"deep/{tenant}/{submission.semantic_engine_revision}"

    def _cache(self, key: str, path: Path) -> bool:
        try:
            raw = self.objects.get(key)
        except ObjectMissing:
            return False
        if len(raw) > MAX_BUNDLE_BYTES:
            return False
        path.write_bytes(raw)
        return True

    def _put(self, key: str, value: object) -> None:
        try:
            self.objects.put_exclusive(key, encoded(value), "application/json")
        except ObjectExists:
            pass

    def execute(self, spec: ExecuteRequest, files: dict[str, bytes], *, context: ExecutionContext) -> ExecuteResponse:
        try:
            return self._execute(spec, files, context=context)
        except DeepError:
            raise
        except ControllerFault as exc:
            raise DeepError(FAULTS.get(exc.code, "deep_execution_failed")) from exc
        except TimeoutError as exc:
            raise DeepError("deep_timeout") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise DeepError("deep_output_invalid") from exc

    def _execute(self, spec: ExecuteRequest, files: dict[str, bytes], *, context: ExecutionContext) -> ExecuteResponse:
        submission, packets = semantic_inputs(spec, self.runtime.engine_revision())
        prefix = self._prefix(spec, submission)
        job_key = hashlib.sha256(spec.job_id.encode()).hexdigest()
        artifact_prefix = f"{prefix}/rounds/{spec.round_id}/{job_key}-{spec.attempt}"
        deadline = time.monotonic() + self.timeout
        # Plans may contain template-specific assumptions. Reuse for exactly the
        # same document set, never silently promote them to another template.
        scope = hashlib.sha256(encoded([item.model_dump() for item in submission.documents])).hexdigest()
        with tempfile.TemporaryDirectory(prefix="ppap-semantic-") as temporary:
            root = Path(temporary)
            inputs, work = root / "inputs", root / "work"
            inputs.mkdir()
            work.mkdir()
            names, rule_names, cached_snapshots, cached_plans = {}, {}, {}, {}
            for index, doc in enumerate(submission.documents):
                name = f"document-{index}{doc.format}"
                (inputs / name).write_bytes(files[doc.audit_id])
                names[doc.audit_id] = name
                cached_name = f"snapshot-{index}.json"
                if self._cache(f"{prefix}/snapshots/{doc.sha256}{doc.format}.json", inputs / cached_name):
                    cached_snapshots["document:" + doc.audit_id] = cached_name
            for index, rule in enumerate(submission.rules):
                name = f"rule-{index}.json"
                (inputs / name).write_bytes(packets[rule.rule_id])
                rule_names[rule.rule_id] = name
                cached_name = f"plan-{index}.json"
                if self._cache(f"{prefix}/plans/{scope}/{rule.plan_revision}.json", inputs / cached_name):
                    cached_plans[rule.rule_id] = cached_name
            manifest = Manifest(spec=submission, documents=names, rules=rule_names, cached_snapshots=cached_snapshots, cached_plans=cached_plans)
            (inputs / "manifest.json").write_bytes(manifest.model_dump_json().encode())

            def progress(value):
                state = {"phase": value.phase}
                if value.total_clauses is not None:
                    state["total_count"] = value.total_clauses
                if value.verified_clauses is not None:
                    state["completed_count"] = value.verified_clauses
                context.progress(state)

            try:
                self.runtime.run(submission, inputs, work, deadline, context.cancelled, on_progress=progress)
            except ControllerFault:
                diagnostic = work / "diagnostic.json"
                if diagnostic.is_file():
                    # Private, tenant-scoped diagnostic, not model logs in the UI.
                    try:
                        self._put(artifact_prefix + "/diagnostic.json", read_json(work, ("diagnostic.json",), 256 * 1024))
                    except Exception:
                        pass  # Private diagnostic storage must not mask the execution fault.
                raise
            if context.cancelled.is_set():
                raise DeepError("deep_cancelled")
            try:
                bundle = ResultBundle.model_validate(read_json(work, ("result", "bundle.json"), MAX_BUNDLE_BYTES))
            except OSError as exc:
                raise DeepError("deep_output_invalid") from exc
            response = product_response(spec, bundle, submission)
            for doc in submission.documents:
                self._put(f"{prefix}/snapshots/{doc.sha256}{doc.format}.json", bundle.documents[doc.audit_id])
            for rule in submission.rules:
                self._put(f"{prefix}/plans/{scope}/{rule.plan_revision}.json", bundle.plans[rule.rule_id].model_dump())
            self._put(artifact_prefix + "/bundle.json", bundle.model_dump(mode="json"))
            self._put(artifact_prefix + "/inputs.json", submission.model_dump(mode="json"))
            return response
