"""Product versions -> real semantic auditor -> validated product findings."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast
from uuid import UUID

from ppap_core.files import ObjectExists, ObjectMissing, ObjectTooLarge

from ppap_deep.client import DeepError
from ppap_deep.controller import ExecutionContext
from ppap_deep.limits import DEEP_TOTAL_SECONDS
from ppap_deep.protocol import DeepCoverage, DeepFinding, DeepVerification, ExecuteRequest, ExecuteResponse
from ppap_deep.semantic.document_tools import ParserLimitError, ReadLimits, snapshot
from ppap_deep.semantic.evidence import EvidenceBudget, resolve_optional
from ppap_deep.semantic.guest import CompileManifest, Manifest, validate_plan
from ppap_deep.semantic.models import AuditReport, RulePlan
from ppap_deep.semantic.protocol import CompileBundle, CompileFailureRecord, CompileSpec, ControllerFault, DocumentRef, RecoveryRecord, ResultBundle, RuleRef, SubmissionSpec
from ppap_deep.semantic.secure_files import read_json
from ppap_deep.semantic.trace import derive_report, evaluate_audit
from ppap_deep.semantic.validation import validate_bundle, validate_snapshot

POLICY_REVISION = hashlib.sha256(b"ppap-rule-logical-document-evaluation-v10").hexdigest()
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
    published_bases: dict[str, bytes] = {}
    variants: dict[str, dict[str, tuple[bytes, list[dict]]]] = {}
    target_variants: dict[str, tuple[str, str]] = {}
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
        # A confirmed appendix can differ by target while the published rule
        # remains the same. It cannot silently replace that version's body,
        # metadata or original-file provenance.
        base = encoded({**packet, "rule_body": {key: value for key, value in body.items() if key != "appendix"}})
        if item.rule_version_id in published_bases and published_bases[item.rule_version_id] != base:
            raise DeepError("deep_integrity_mismatch")
        published_bases[item.rule_version_id] = base
        raw = encoded(packet)
        digest = hashlib.sha256(raw).hexdigest()
        variants.setdefault(item.rule_version_id, {})[digest] = raw, clauses
        target_variants[item.file_version_id] = item.rule_version_id, digest
    variant_ids = {}
    for version_id, effective in variants.items():
        for digest, (raw, clauses) in effective.items():
            rule_id = version_id if len(effective) == 1 else "effective-" + hashlib.sha256(encoded([version_id, digest])).hexdigest()
            if rule_id in rules or rule_id != version_id and rule_id in published_bases:
                raise DeepError("deep_integrity_mismatch")
            revision = hashlib.sha256(encoded([digest, POLICY_REVISION, engine_revision])).hexdigest()
            rules[rule_id] = RuleRef(rule_id=rule_id, rule_version_id=version_id, sha256=digest, format=".json", plan_revision=revision, clauses=clauses)
            packets[rule_id] = raw
            variant_ids[version_id, digest] = rule_id
    targets = {audit_id: variant_ids[identity] for audit_id, identity in target_variants.items()}
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
        failed = submission.targets[target.file_version_id] in bundle.failed_rules
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
            checked=[item.filename for item in spec.files if item.role == "target" and submission.targets[item.file_version_id] not in bundle.failed_rules],
            unchecked=[f"{item.filename}（参考资料，不单独作完整审核）" for item in spec.files if item.role == "reference"]
            + [f"{item.filename}（审核计划生成失败，未执行）" for item in spec.files if item.role == "target" and submission.targets[item.file_version_id] in bundle.failed_rules],
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

    @staticmethod
    def _observation(document: dict) -> dict:
        # Cache names are host-local metadata. Every source fact, field state,
        # layout, read parameter and omission remains part of the comparison.
        source = document.get("source")
        if not isinstance(source, dict):
            raise ValueError("Document observation lacks its source")
        return {**document, "source": {key: value for key, value in source.items() if key != "file"}}

    @classmethod
    def _checked_documents(cls, supplied: dict[str, dict], trusted: dict[str, dict]) -> dict[str, dict]:
        if set(supplied) != set(trusted) or any(encoded(cls._observation(supplied[key])) != encoded(cls._observation(value)) for key, value in trusted.items()):
            raise ValueError("Document observation differs from the frozen original")
        return trusted

    @staticmethod
    def _candidate(
        bundle: ResultBundle,
        submission: SubmissionSpec,
        plans: dict[str, RulePlan],
        failed_rules: dict[str, CompileFailureRecord],
        packets: dict[str, bytes],
        documents: dict[str, dict],
    ) -> ResultBundle:
        if bundle.plans != plans or bundle.failed_rules != failed_rules:
            raise ValueError("Recovery changed the frozen compilation")
        for rule in submission.rules:
            source = bundle.rule_sources[rule.rule_id]
            if any(source.get(key) != value for key, value in json.loads(packets[rule.rule_id]).items()):
                raise ValueError("Recovery changed the frozen rule source")
        bundle.documents = SemanticRuntime._checked_documents(bundle.documents, documents)
        targets = {audit_id: plans[rule_id] for audit_id, rule_id in submission.targets.items() if rule_id in plans}
        bundle.report = derive_report(bundle.trace, bundle.documents, targets)
        validate_bundle(bundle, submission)
        return bundle

    def execute(self, spec: ExecuteRequest, files: dict[str, bytes], *, context: ExecutionContext) -> ExecuteResponse:
        try:
            return self._execute(spec, files, context=context)
        except DeepError:
            raise
        except ControllerFault as exc:
            raise DeepError(FAULTS.get(exc.code, "deep_execution_failed")) from exc
        except TimeoutError as exc:
            raise DeepError("deep_timeout") from exc
        except ParserLimitError as exc:
            raise DeepError("deep_parser_limit") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise DeepError("deep_output_invalid") from exc

    def _execute(self, spec: ExecuteRequest, files: dict[str, bytes], *, context: ExecutionContext) -> ExecuteResponse:
        revision = self.runtime.engine_revision()
        if spec.executor_revision != revision:
            raise DeepError("executor_revision_mismatch")
        submission, packets = semantic_inputs(spec, revision)
        if set(files) != {doc.audit_id for doc in submission.documents} or any(hashlib.sha256(files[doc.audit_id]).hexdigest() != doc.sha256 for doc in submission.documents):
            raise DeepError("deep_integrity_mismatch")
        prefix = self._prefix(spec, submission)
        job_key = hashlib.sha256(spec.job_id.encode()).hexdigest()
        artifact_prefix = f"{prefix}/rounds/{spec.round_id}/{job_key}-{spec.attempt}"
        expires_at = datetime.fromtimestamp(time.time() + self.timeout, UTC)
        recovery = None
        if spec.resume_attempt is not None:
            if spec.resume_attempt >= spec.attempt:
                raise ValueError("Recovery requires an earlier attempt")
            source_key = f"{prefix}/rounds/{spec.round_id}/{job_key}-{spec.resume_attempt}/recovery.json"
            with tempfile.TemporaryDirectory(prefix="ppap-recovery-") as source_dir:
                source_root = Path(source_dir)
                if not self._cache(source_key, source_root / "recovery.json"):
                    raise ValueError("Recovery source is unavailable")
                recovery = RecoveryRecord.model_validate(read_json(source_root, ("recovery.json",), MAX_BUNDLE_BYTES))
            if recovery.spec != submission or recovery.job_id != spec.job_id or recovery.attempt != spec.resume_attempt:
                raise ValueError("Recovery source differs from the authorized input")
            # Unknown upstream consumption is a read-back/reconciliation boundary.
            # An explicit resume never silently creates another model request.
            if recovery.uncertain:
                raise DeepError("deep_model_protocol")
            validator = getattr(self.runtime, "validate_recovery", None)
            if not callable(validator):
                raise DeepError("deep_runtime_unavailable")
            validator(
                submission,
                job_id=spec.job_id,
                source_attempt=spec.resume_attempt,
                current_attempt=spec.attempt,
                prior_usage={key: recovery.usage[key] for key in ("model_requests", "input_tokens", "output_tokens")},
            )
            expires_at = min(expires_at, recovery.expires_at)
        deadline = time.monotonic() + max(0, expires_at.timestamp() - time.time())
        if time.monotonic() >= deadline:
            raise TimeoutError("Deep deadline exceeded")
        # Programs contain source-rule meaning only; physical bindings and local
        # answers belong to this execution, never the tenant-scoped plan cache.
        with tempfile.TemporaryDirectory(prefix="ppap-semantic-") as temporary:
            root = Path(temporary)
            plans: dict[str, RulePlan] = {}
            failed_rules: dict[str, CompileFailureRecord] = {}
            usage = dict.fromkeys(("input_tokens", "output_tokens", "tool_calls", "completed_tool_calls", "compactions", "model_requests"), 0)
            if recovery is not None:
                usage.update(recovery.usage)
                failed_rules.update(recovery.failed_rules)

            trusted_documents: dict[str, dict] | None = None

            def native_documents() -> dict[str, dict]:
                nonlocal trusted_documents
                if trusted_documents is None:
                    parsed: dict[tuple[str, str], dict] = {}
                    documents = {}
                    limits = ReadLimits()
                    observation_bytes = 0
                    for index, doc in enumerate(submission.documents):
                        context.raise_if_cancelled()
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Deep deadline exceeded")
                        key = (doc.sha256, doc.format)
                        if key not in parsed:
                            original = root / f"native-{index}{doc.format}"
                            original.write_bytes(files[doc.audit_id])
                            parsed[key] = snapshot(original, limits=limits)
                            validate_snapshot(parsed[key], doc.sha256, doc.format)
                        context.raise_if_cancelled()
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Deep deadline exceeded")
                        documents[doc.audit_id] = parsed[key]
                        observation_bytes += len(encoded(parsed[key]))
                        if observation_bytes > limits.output_bytes:
                            raise ParserLimitError("Document observations exceed the execution output budget")
                        context.raise_if_cancelled()
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Deep deadline exceeded")
                    trusted_documents = documents
                return trusted_documents

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
                    if recovery is not None:
                        if rule.rule_id in failed_rules:
                            continue
                        if rule.rule_id not in cached:
                            raise ValueError("Recovery plan is unavailable")
                        plan = RulePlan.model_validate(read_json(compile_inputs, ("plan.json",), MAX_BUNDLE_BYTES))
                        if recovery.plan_digests.get(rule.rule_id) != hashlib.sha256(encoded(plan.model_dump(mode="json"))).hexdigest():
                            raise ValueError("Recovery plan digest mismatch")
                        source = json.loads(packets[rule.rule_id])
                        source["source"] = {"sha256": rule.sha256, "format": rule.format}
                        validate_plan(plan, rule, source, EvidenceBudget())
                        plans[rule.rule_id] = plan
                        continue
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
            resume_name = None
            if recovery is not None and recovery.trace is not None:
                resume_name = "resume-trace.json"
                (inputs / resume_name).write_text(recovery.trace.model_dump_json())
            manifest = Manifest(
                spec=submission,
                documents=names,
                rules=rule_names,
                cached_snapshots=cached_snapshots,
                cached_plans=cached_plans,
                resume_trace=resume_name,
                runtime_job_id=spec.job_id,
                runtime_attempt=spec.attempt,
                runtime_source_attempt=spec.resume_attempt,
                precompiled_only=True,
                failed_rules=failed_rules,
            )
            (inputs / "manifest.json").write_bytes(manifest.model_dump_json().encode())
            if recovery is not None and recovery.trace is not None:
                documents = {doc.audit_id: read_json(inputs, (cached_snapshots["document:" + doc.audit_id],), MAX_BUNDLE_BYTES) for doc in submission.documents}
                self._checked_documents(documents, native_documents())
                if set(recovery.snapshot_digests) != set(documents) or any(
                    recovery.snapshot_digests[key] != hashlib.sha256(encoded(self._observation(value))).hexdigest() for key, value in documents.items()
                ):
                    raise ValueError("Recovery snapshot digest mismatch")
                sources = {rule.rule_id: {**json.loads(packets[rule.rule_id]), "source": {"sha256": rule.sha256, "format": rule.format}} for rule in submission.rules}
                candidate = ResultBundle(
                    execution_id=submission.execution_id,
                    semantic_engine_revision=revision,
                    documents=documents,
                    rule_sources=sources,
                    plans=plans,
                    failed_rules=failed_rules,
                    report=AuditReport(checks=[]),
                    trace=recovery.trace,
                )
                candidate = self._candidate(candidate, submission, plans, failed_rules, packets, native_documents())
                if all(check.unknown == 0 for check in candidate.report.checks):
                    context.raise_if_cancelled()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Deep deadline exceeded")
                    for name, count in usage.items():
                        setattr(candidate, name, count)
                    response = product_response(spec, candidate, submission)
                    response.usage_source = getattr(self.runtime, "usage_source", "unknown")
                    self._put(artifact_prefix + "/bundle.json", candidate.model_dump(mode="json"))
                    self._put(artifact_prefix + "/inputs.json", submission.model_dump(mode="json"))
                    return response
            try:
                run(submission, inputs, work, "diagnostic.json")
            except (ControllerFault, TimeoutError, DeepError):
                trace = None
                snapshot_digests = {}
                current_usage = dict(usage)
                uncertain = True
                try:
                    diagnostic = read_json(work, ("diagnostic.json",), 256 * 1024)
                    facts = diagnostic.get("usage", {})
                    uncertain = facts.get("uncertain") is not False or facts.get("in_flight") is not False
                    for source, target in (("completed_requests", "model_requests"), ("input_tokens", "input_tokens"), ("output_tokens", "output_tokens")):
                        if type(facts.get(source)) is not int or facts[source] < 0:
                            uncertain = True
                        else:
                            current_usage[target] += facts[source]
                except (OSError, ValueError, TypeError):
                    pass
                try:
                    candidate = ResultBundle.model_validate(read_json(work, ("candidate.json",), MAX_BUNDLE_BYTES))
                    candidate = self._candidate(candidate, submission, plans, failed_rules, packets, native_documents())
                    trace = candidate.trace
                    snapshot_digests = {key: hashlib.sha256(encoded(self._observation(value))).hexdigest() for key, value in candidate.documents.items()}
                    for name in ("tool_calls", "completed_tool_calls", "compactions"):
                        current_usage[name] += getattr(candidate, name)
                    for doc in submission.documents:
                        self._put(f"{prefix}/snapshots/{doc.sha256}{doc.format}.json", candidate.documents[doc.audit_id])
                except (OSError, ValueError, KeyError, TypeError, TimeoutError, DeepError):
                    pass  # Invalid or incomplete writes never enter trusted caches.
                saved = RecoveryRecord(
                    spec=submission,
                    job_id=spec.job_id,
                    attempt=spec.attempt,
                    expires_at=expires_at,
                    usage=current_usage,
                    uncertain=uncertain,
                    failed_rules=failed_rules,
                    snapshot_digests=snapshot_digests,
                    plan_digests={key: hashlib.sha256(encoded(plan.model_dump(mode="json"))).hexdigest() for key, plan in plans.items()},
                    trace=trace,
                    trace_sha256=hashlib.sha256(encoded(trace.model_dump(mode="json"))).hexdigest() if trace is not None else None,
                )
                self._put(artifact_prefix + "/recovery.json", saved.model_dump(mode="json"))
                raise
            try:
                bundle = ResultBundle.model_validate(read_json(work, ("result", "bundle.json"), MAX_BUNDLE_BYTES))
            except OSError as exc:
                raise DeepError("deep_output_invalid") from exc
            if bundle.plans != plans or bundle.failed_rules != failed_rules:
                raise ValueError("Audit changed the frozen compilation results")
            for name, count in usage.items():
                setattr(bundle, name, getattr(bundle, name) + count)
            bundle.documents = self._checked_documents(bundle.documents, native_documents())
            response = product_response(spec, bundle, submission)
            context.raise_if_cancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError("Deep deadline exceeded")
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
