"""Product versions -> real semantic auditor -> validated product findings."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path
from typing import Literal, Protocol, cast
from uuid import UUID

from ppap_core.files import ObjectExists, ObjectMissing, ObjectTooLarge

from ppap_deep.client import DeepError
from ppap_deep.controller import ExecutionContext
from ppap_deep.limits import DEEP_TOTAL_SECONDS
from ppap_deep.protocol import DeepCoverage, DeepFinding, DeepVerification, ExecuteRequest, ExecuteResponse
from ppap_deep.semantic.evidence import EvidenceBudget, resolve_optional
from ppap_deep.semantic.guest import CompileManifest, Manifest, validate_plan
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.protocol import CompileBundle, CompileFailureRecord, CompileSpec, ControllerFault, DocumentRef, ResultBundle, RuleRef, SubmissionSpec
from ppap_deep.semantic.secure_files import read_json
from ppap_deep.semantic.trace import evaluate_audit
from ppap_deep.semantic.validation import validate_bundle, validate_snapshot

POLICY_REVISION = hashlib.sha256(b"ppap-rule-logical-document-evaluation-v9").hexdigest()
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
    def get(self, key: str, *, max_bytes: int | None = None) -> bytes: ...
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
    if bundle.semantic_engine_revision != spec.executor_revision:
        raise ValueError("Validated bundle does not belong to the frozen execution policy")
    expected, _packets = semantic_inputs(spec, bundle.semantic_engine_revision)
    if submission != expected:
        raise ValueError("Validated bundle does not belong to the frozen request")
    evidence_gaps = validate_bundle(bundle, submission)
    files = {item.file_version_id: item for item in spec.files}
    checks = {(check.audit_id, check.rule_id): check for check in bundle.report.checks}
    from .semantic.holes import TypedAnswer
    from .semantic.trace import program_has_semantics

    evaluation = evaluate_audit(bundle.trace, bundle.documents, {audit_id: bundle.plans[rule_id] for audit_id, rule_id in submission.targets.items() if rule_id in bundle.plans})
    inferred = {answer.need_id for answer in bundle.trace.answers if isinstance(answer, TypedAnswer) and answer.origin == "model"}
    inferred_rules = {(need.audit_id, need.rule_id) for need in evaluation.needs if need.id in inferred}
    proofs = {
        (audit_id, rule.id): "semantic" if program_has_semantics(rule.program) or (audit_id, rule.id) in inferred_rules else "query"
        for audit_id, rule_file_id in submission.targets.items()
        if rule_file_id in bundle.plans
        for rule in bundle.plans[rule_file_id].rules
    }
    findings, rule_items = [], []
    evidence_budget = EvidenceBudget()
    for target, clause in ((target, clause) for target in spec.files if target.role == "target" for clause in (target.rule_body or {})["rules"]):
        clause_id = clause["id"]
        failed = target.rule_version_id in bundle.failed_rules
        check = None if failed else checks[target.file_version_id, clause_id]
        status: Literal["PASS", "NG", "ERR", "UNABLE", "NA"] = check.status if check else "ERR"
        reason = check.reason if check else "审核计划生成失败，本项未执行。原文件与已确认规则未变。"
        sources = []
        for citation in check.evidence if check else []:
            source = files[citation.document_id]
            observed = resolve_optional(bundle.documents[citation.document_id], citation.locator, evidence_budget)
            if observed is None or citation.locator is None:
                evidence_gaps.add((target.file_version_id, clause_id))
                continue
            excerpt = " · ".join(observed.values)
            if len(excerpt) > 4000:
                excerpt = excerpt[:4000] + "…"
            sources.append({"file_version_id": source.file_version_id, "filename": source.filename, "role": source.role, "locator": citation.locator.model_dump(), "excerpt": excerpt})
        semantic = {
            "rule_version_id": target.rule_version_id,
            "clause_id": clause_id,
            "rule_text": clause["rule_to_check"],
            "scope": {"total": check.total, "checked": check.checked, "unknown": check.unknown} if check else {"total": 0, "checked": 0, "unknown": 0},
            "sources": sources,
        }
        if (target.file_version_id, clause_id) in evidence_gaps:
            semantic["evidence_incomplete"] = True
        if failed:
            semantic["diagnostic_code"] = "deep_output_invalid"
            semantic["diagnostic"] = reason
        elif check and check.diagnostic:
            semantic["diagnostic_code"] = check.diagnostic
            semantic["diagnostic"] = DIAGNOSTIC_LABELS[check.diagnostic]
        if target.rule_source_file_version_id:
            semantic["rule_source_file_version_id"] = target.rule_source_file_version_id
        item_key = f"{target.file_version_id}:rule:{clause_id}"
        findings.append(
            DeepFinding(
                item_key=item_key,
                classification=str(clause.get("category") or "客户规则"),
                item=clause["rule_to_check"],
                priority=str(clause.get("priority") or "P1"),
                status=status,
                suggestions=reason,
                locator={
                    "kind": "file_version",
                    "file_id": target.file_id,
                    "file_version_id": target.file_version_id,
                    "filename": target.filename,
                    "rule_item_id": clause_id,
                    "semantic": semantic,
                },
            )
        )
        rule_items.append(
            {
                "file_version_id": target.file_version_id,
                "filename": target.filename,
                "rule_version_id": target.rule_version_id,
                "rule_item_id": clause_id,
                "id": clause_id,
                "category": str(clause.get("category") or "客户规则"),
                "rule_to_check": clause["rule_to_check"],
                "item_key": item_key,
                "coverage": "execution_error" if failed else "executed",
                "status": status,
                **({"reason": reason} if failed else {"proof_kind": proofs[target.file_version_id, clause_id]}),
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
            clarifications=evaluation.clarifications,
            checked=[item.filename for item in spec.files if item.role == "target" and item.rule_version_id not in bundle.failed_rules],
            unchecked=[f"{item.filename}（参考资料，不单独作完整审核）" for item in spec.files if item.role == "reference"]
            + [f"{item.filename}（审核计划生成失败，未执行）" for item in spec.files if item.role == "target" and item.rule_version_id in bundle.failed_rules],
            locator_gap=bool(evidence_gaps) or any(not (row.locator or {})["semantic"]["sources"] for row in findings),
            rule_items=rule_items,
            verification=None
            if evidence_gaps or not bundle.plans
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

    def ready(self) -> bool:
        return self.runtime.ready()

    @staticmethod
    def _prefix(spec: ExecuteRequest, submission: SubmissionSpec) -> str:
        tenant = hashlib.sha256(spec.tenant_id.encode()).hexdigest()
        return f"deep/{tenant}/{submission.semantic_engine_revision}"

    def _cache(self, key: str, path: Path) -> bool:
        try:
            raw = self.objects.get(key, max_bytes=MAX_BUNDLE_BYTES)
        except (ObjectMissing, ObjectTooLarge):
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
        revision = self.runtime.engine_revision()
        if spec.executor_revision != revision:
            raise DeepError("executor_revision_mismatch")
        submission, packets = semantic_inputs(spec, revision)
        prefix = self._prefix(spec, submission)
        job_key = hashlib.sha256(spec.job_id.encode()).hexdigest()
        artifact_prefix = f"{prefix}/rounds/{spec.round_id}/{job_key}-{spec.attempt}"
        deadline = time.monotonic() + self.timeout
        # Programs contain source-rule meaning only; physical bindings and local
        # answers belong to this execution, never the tenant-scoped plan cache.
        with tempfile.TemporaryDirectory(prefix="ppap-semantic-") as temporary:
            root = Path(temporary)
            plans: dict[str, RulePlan] = {}
            failed_rules: dict[str, CompileFailureRecord] = {}
            usage = dict.fromkeys(("input_tokens", "output_tokens", "tool_calls", "completed_tool_calls", "compactions", "model_requests"), 0)

            def progress(value):
                state = {"phase": value.phase}
                if value.total_clauses is not None:
                    state["total_count"] = value.total_clauses
                if value.verified_clauses is not None:
                    state["completed_count"] = value.verified_clauses
                context.progress(state)

            def run(operation, inputs, work, diagnostic_name):
                context.raise_if_cancelled()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Deep deadline exceeded")
                prior = {name: usage[name] for name in ("input_tokens", "output_tokens", "model_requests")}
                try:
                    self.runtime.run(operation, inputs, work, deadline, context.cancelled, on_progress=progress, prior_usage=prior if any(prior.values()) else None)
                except ControllerFault:
                    diagnostic = work / "diagnostic.json"
                    if diagnostic.is_file():
                        try:
                            self._put(artifact_prefix + "/" + diagnostic_name, read_json(work, ("diagnostic.json",), 256 * 1024))
                        except Exception:
                            pass  # Private diagnostic storage must not mask the execution fault.
                    raise
                context.raise_if_cancelled()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Deep deadline exceeded")

            # The root is host-owned mode 0700. Compile each rule before staging
            # any supplier bytes, and remove that rule's run before the next one.
            for index, rule in enumerate(submission.rules):
                context.raise_if_cancelled()
                with tempfile.TemporaryDirectory(prefix=f"compile-{index}-", dir=root) as compilation:
                    compile_root = Path(compilation)
                    compile_inputs, compile_work = compile_root / "inputs", compile_root / "work"
                    compile_inputs.mkdir()
                    compile_work.mkdir()
                    (compile_inputs / "rule.json").write_bytes(packets[rule.rule_id])
                    cached = {}
                    if self._cache(f"{prefix}/plans/{rule.plan_revision}.json", compile_inputs / "plan.json"):
                        cached[rule.rule_id] = "plan.json"
                    compile_spec = CompileSpec(
                        execution_id=UUID(int=0),
                        submission_id=submission.submission_id,
                        project_id=submission.project_id,
                        rules=[rule],
                        policy_revision=submission.policy_revision,
                        semantic_engine_revision=submission.semantic_engine_revision,
                    )
                    compile_spec = compile_spec.model_copy(update={"execution_id": compile_spec.expected_execution_id()})
                    compile_manifest = CompileManifest(operation="compile", spec=compile_spec, rules={rule.rule_id: "rule.json"}, cached_snapshots={}, cached_plans=cached)
                    (compile_inputs / "manifest.json").write_text(compile_manifest.model_dump_json())
                    run(compile_spec, compile_inputs, compile_work, f"compile-{index}-diagnostic.json")
                    try:
                        compiled = CompileBundle.model_validate(read_json(compile_work, ("result", "bundle.json"), MAX_BUNDLE_BYTES))
                    except OSError as exc:
                        raise DeepError("deep_output_invalid") from exc
                    if (
                        compiled.execution_id != compile_spec.execution_id
                        or compiled.semantic_engine_revision != revision
                        or set(compiled.rule_sources) != {rule.rule_id}
                        or set(compiled.plans) & set(compiled.failed_rules)
                        or set(compiled.plans) | set(compiled.failed_rules) != {rule.rule_id}
                    ):
                        raise ValueError("Compilation differs from frozen rule input")
                    compiled_source = compiled.rule_sources[rule.rule_id]
                    validate_snapshot(compiled_source, rule.sha256, rule.format)
                    if any(compiled_source.get(key) != value for key, value in json.loads(packets[rule.rule_id]).items()):
                        raise ValueError("Compilation changed the published rule source")
                    if rule.rule_id in compiled.plans:
                        plan = compiled.plans[rule.rule_id]
                        validate_plan(plan, rule, compiled_source, EvidenceBudget())
                        plans[rule.rule_id] = plan
                        self._put(f"{prefix}/plans/{rule.plan_revision}.json", plan.model_dump(mode="json"))
                    else:
                        failure = compiled.failed_rules[rule.rule_id]
                        if (failure.rule_id, failure.source_sha256, failure.plan_revision, failure.engine_revision) != (rule.rule_id, rule.sha256, rule.plan_revision, revision):
                            raise ValueError("Failed compilation differs from frozen rule input")
                        if failure.clause_id is not None and failure.clause_id not in {str(clause.get("id") or position) for position, clause in enumerate(rule.clauses, 1)}:
                            raise ValueError("Failed compilation clause differs from frozen input")
                        failed_rules[rule.rule_id] = failure
                    for name in usage:
                        usage[name] += getattr(compiled, name)

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
                if rule.rule_id in plans:
                    cached_name = f"plan-{index}.json"
                    (inputs / cached_name).write_text(plans[rule.rule_id].model_dump_json())
                    cached_plans[rule.rule_id] = cached_name
            manifest = Manifest(spec=submission, documents=names, rules=rule_names, cached_snapshots=cached_snapshots, cached_plans=cached_plans, precompiled_only=True, failed_rules=failed_rules)
            (inputs / "manifest.json").write_bytes(manifest.model_dump_json().encode())
            run(submission, inputs, work, "diagnostic.json")
            try:
                bundle = ResultBundle.model_validate(read_json(work, ("result", "bundle.json"), MAX_BUNDLE_BYTES))
            except OSError as exc:
                raise DeepError("deep_output_invalid") from exc
            if bundle.plans != plans or bundle.failed_rules != failed_rules:
                raise ValueError("Audit changed the frozen compilation results")
            for name, count in usage.items():
                setattr(bundle, name, getattr(bundle, name) + count)
            response = product_response(spec, bundle, submission)
            source = getattr(self.runtime, "usage_source", "unknown")
            response.usage_source = "controlled" if source == "controlled" else "provider" if source == "provider" else "unknown"
            for doc in submission.documents:
                self._put(f"{prefix}/snapshots/{doc.sha256}{doc.format}.json", bundle.documents[doc.audit_id])
            for rule in submission.rules:
                if rule.rule_id in bundle.plans:
                    self._put(f"{prefix}/plans/{rule.plan_revision}.json", bundle.plans[rule.rule_id].model_dump())
            self._put(artifact_prefix + "/bundle.json", bundle.model_dump(mode="json"))
            self._put(artifact_prefix + "/inputs.json", submission.model_dump(mode="json"))
            return response
