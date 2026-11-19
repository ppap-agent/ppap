"""Per-file task manifests for the existing low-privilege ContainerRuntime guest.

No command, host path, provider credential, or unsigned program comes from RPC.
The controller resolves the immutable Suite and stages a bounded flat directory.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from audit.checker_core import ExecutionSnapshot, checker_digest, validate_results
from audit.protocol import ControllerFault
from pydantic import BaseModel, ConfigDict, Field

from agent.client import DeepError
from agent.execution.budget import CHECK_SECONDS, DEEP_TOTAL_SECONDS, MAX_INPUT_FILES, MAX_INPUT_TOTAL
from agent.execution.secure_files import read_json, read_regular
from agent.run import ExecutionContext

MAX_MANIFEST = 4 * 1024 * 1024
MAX_BUNDLE = 16 * 1024 * 1024
AnalysisMode = Literal["standard", "deep"]
Operation = Literal["execute-audit", "review-target"]
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


class AuditJobSpec(BaseModel):
    """One task for one target file: run its Suite programs, or deep-review their results."""

    model_config = ConfigDict(extra="forbid")
    operation: Operation = "execute-audit"
    execution_id: UUID
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    executor_revision: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    allow_model_calls: bool = True
    unavailable_sources: tuple[str, ...] = ()

    @classmethod
    def for_target(
        cls, snapshot: ExecutionSnapshot, target_id: str, operation: Operation = "execute-audit", allow_model_calls: bool = True, unavailable_sources: frozenset[str] = frozenset()
    ) -> AuditJobSpec:
        if target_id not in {target.file_version_id for target in snapshot.targets}:
            raise ValueError("audit_target_unknown")
        unavailable = tuple(sorted(unavailable_sources))
        # A restored object cannot replay the outcome of an unavailable attempt.
        availability = ":" + json.dumps(unavailable, separators=(",", ":")) if unavailable else ""
        return cls(
            operation=operation,
            execution_id=uuid5(NAMESPACE_URL, f"ppap-{operation}:{allow_model_calls}:{snapshot.input_digest}:{target_id}{availability}"),
            input_digest=snapshot.input_digest,
            executor_revision=snapshot.runtime_revision,
            target_id=target_id,
            allow_model_calls=allow_model_calls,
            unavailable_sources=unavailable,
        )


def write_audit_manifest(
    inputs: Path,
    snapshot: ExecutionSnapshot,
    sources: dict[str, bytes],
    programs: dict[str, dict[str, bytes]],
    target_id: str,
    *,
    operation: Operation = "execute-audit",
    findings: list[dict[str, Any]] | None = None,
    allow_model_calls: bool = True,
    unavailable_sources: frozenset[str] = frozenset(),
) -> AuditJobSpec:
    """Stage one task. A review also carries the validated program findings of its target."""
    if operation == "review-target" and (not allow_model_calls or findings is None):
        raise DeepError("deep_requires_model")
    if operation == "execute-audit" and findings is not None:
        raise ValueError("audit_manifest_invalid")
    snapshot.validate_sources(sources, unavailable=unavailable_sources)
    expected = {target.binding.checker_digest for target in snapshot.targets}
    if set(programs) != expected or any(checker_digest(files) != digest for digest, files in programs.items()):
        raise ValueError("checker_program_digest_mismatch")
    if 1 + len(sources) + sum(len(files) for files in programs.values()) > MAX_INPUT_FILES:
        raise ValueError("audit_input_limit")
    inputs.mkdir()
    source_paths: dict[str, str] = {}
    program_paths: dict[str, dict[str, str]] = {}
    total = 0

    def stage(name: str, body: bytes) -> None:
        nonlocal total
        total += len(body)
        if total > MAX_INPUT_TOTAL:
            raise ValueError("audit_input_limit")
        (inputs / name).write_bytes(body)

    for index, (identity, body) in enumerate(sorted(sources.items())):
        name = f"source-{index}.bin"
        stage(name, body)
        source_paths[identity] = name
    for index, (digest, files) in enumerate(sorted(programs.items())):
        if "checker.py" not in files or set(files) - {"checker.py", "resources.py"}:
            raise ValueError("invalid_checker_program_files")
        program_paths[digest] = {}
        for name, body in sorted(files.items()):
            staged = f"program-{index}-{name}"
            stage(staged, body)
            program_paths[digest][name] = staged
    job = AuditJobSpec.for_target(snapshot, target_id, operation, allow_model_calls, unavailable_sources)
    manifest: dict[str, Any] = {"operation": operation, "spec": job.model_dump(mode="json"), "snapshot": snapshot.to_dict(), "source_paths": source_paths, "program_paths": program_paths}
    if findings is not None:
        manifest["findings"] = findings
    raw = json.dumps(manifest, ensure_ascii=False).encode()
    if len(raw) > MAX_MANIFEST:
        raise ValueError("audit_manifest_limit")
    stage("manifest.json", raw)
    return job


def _read_staged(inputs: Path, name: object, remaining: int) -> bytes:
    if not isinstance(name, str) or "/" in name or "\\" in name or name in {"", ".", ".."}:
        raise ValueError("audit_manifest_path_invalid")
    return read_regular(inputs, (name,), remaining)


def run_audit_manifest(inputs: Path, work: Path, credentials: Path) -> None:
    """Called only by the existing guest entrypoint, after UID/capability drop."""
    from documents.model_access import model_access_scope

    from agent.execution.audit_runtime import run_audit
    from agent.execution.checker_runtime import CheckerRuntime
    from agent.execution.guest_model import ModelGrant

    manifest = read_json(inputs, ("manifest.json",), MAX_MANIFEST)
    operation = manifest.get("operation")
    keys = {"operation", "spec", "snapshot", "source_paths", "program_paths"} | ({"findings"} if operation == "review-target" else set())
    if set(manifest) != keys or operation not in {"execute-audit", "review-target"}:
        raise ValueError("audit_manifest_invalid")
    snapshot = ExecutionSnapshot.from_dict(manifest["snapshot"])
    job = AuditJobSpec.model_validate(manifest["spec"])
    unavailable = frozenset(job.unavailable_sources)
    if job.operation != operation or job != AuditJobSpec.for_target(snapshot, job.target_id, job.operation, job.allow_model_calls, unavailable):
        raise ValueError("audit_manifest_identity_mismatch")
    if operation == "review-target" and not job.allow_model_calls:
        raise ValueError("audit_review_requires_model")
    total = 0

    def read(name: str) -> bytes:
        nonlocal total
        raw = _read_staged(inputs, name, MAX_INPUT_TOTAL - total)
        total += len(raw)
        return raw

    sources = {key: read(name) for key, name in manifest["source_paths"].items()}
    programs = {key: {name: read(path) for name, path in paths.items()} for key, paths in manifest["program_paths"].items()}
    grant = None
    if job.allow_model_calls:
        grant = ModelGrant.model_validate(read_json(credentials, ("model.json",), 16384))
        # A temporary local capability is the only model credential the guest sees.
        from urllib.parse import urlsplit

        endpoint = urlsplit(grant.base_url)
        if endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"} or endpoint.username or endpoint.password or not grant.matches(snapshot.model_identity):
            raise ValueError("audit_model_grant_mismatch")
    snapshot.validate_sources(sources, unavailable=unavailable)
    payload: dict[str, Any]
    if operation == "execute-audit":
        with grant.access_scope() if grant is not None else model_access_scope(None):
            payload = run_audit(snapshot, sources, programs, runtime=CheckerRuntime(allow_model_tools=job.allow_model_calls), unavailable_sources=unavailable, target_id=job.target_id)
    else:
        assert grant is not None
        from agent.deep_review import review_target
        from agent.execution.activity import ACTIVITY_FILE
        from agent.execution.guest_model import PiRunner

        target = next(target for target in snapshot.targets if target.file_version_id == job.target_id)
        findings = manifest["findings"]
        if not isinstance(findings, list) or any(not isinstance(item, dict) or item.get("file_version_id") != job.target_id for item in findings):
            raise ValueError("audit_manifest_invalid")
        runner = PiRunner(grant.model_dump(mode="json"))
        # Live steps for viewers; the Controller forwards them and nothing stores them.
        runner.activity = work / ACTIVITY_FILE
        deadline = time.monotonic() + min(runner.wall_seconds, DEEP_TOTAL_SECONDS)
        payload = {"review": review_target(snapshot, sources, programs, target, findings, runner=runner, work=work / "deep-review", deadline=deadline, unavailable=unavailable)}
    payload.update(
        operation=operation,
        execution_id=str(job.execution_id),
        executor_revision=snapshot.runtime_revision,
        accepted_input_digest=snapshot.input_digest,
        target_id=job.target_id,
    )
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    if len(encoded) > MAX_BUNDLE:
        raise ValueError("audit_output_limit")
    (work / "result").mkdir(parents=True, exist_ok=True)
    (work / "result" / "bundle.json").write_bytes(encoded)


def _combine(parts: list[dict[str, Any]], unavailable: frozenset[str], input_digest: str) -> dict[str, Any]:
    """One round's result from its per-file results. Usage is known only when every task reported it."""
    combined: dict[str, Any] = {
        "input_digest": input_digest,
        "findings": [row for part in parts for row in part["findings"]],
        "coverage": [row for part in parts for row in part["coverage"]],
        "diagnostics": [row for part in parts for row in part.get("diagnostics", [])],
        "unavailable_sources": sorted(unavailable),
    }
    for key in ("input_tokens", "output_tokens", "model_requests"):
        values = [part.get(key) for part in parts]
        if all(type(value) is int and value >= 0 for value in values):
            combined[key] = sum(values)
    return combined


class CoreRuntime:
    """Trusted host adapter: resolve immutable content, then run one guest task per file."""

    def __init__(self, runtime: Any, snapshot_for_request, suite_for_revision, *, objects=None) -> None:
        self.runtime, self.snapshot_for_request, self.suite_for_revision = runtime, snapshot_for_request, suite_for_revision
        self.objects = objects

    def ready(self, operation: str = "audit") -> bool:
        return self.runtime.ready(operation)

    def _task(self, snapshot: ExecutionSnapshot, job: AuditJobSpec, inputs: Path, work: Path, seconds: float, context: ExecutionContext, **live: Any) -> dict[str, Any]:
        self.runtime.run(job, inputs, work, time.monotonic() + seconds, context.cancelled, **live)
        result = read_json(work, ("result", "bundle.json"), MAX_BUNDLE)
        if (
            result.get("execution_id") != str(job.execution_id)
            or result.get("executor_revision") != snapshot.runtime_revision
            or result.get("operation") != job.operation
            or result.get("target_id") != job.target_id
            or result.get("accepted_input_digest") != snapshot.input_digest
        ):
            raise DeepError("deep_output_identity_mismatch")
        context.raise_if_cancelled()
        return result

    def check_target(
        self, snapshot: ExecutionSnapshot, sources: dict[str, bytes], programs: dict[str, dict[str, bytes]], target_id: str, context: ExecutionContext, *, allow_model_calls: bool = True
    ) -> dict[str, Any]:
        """One file's Suite programs in their own guest, within CHECK_SECONDS. Returns its validated result."""
        from agent.execution.audit_runtime import _error, _evidence_references, _validate_output

        context.raise_if_cancelled()
        target = next(target for target in snapshot.targets if target.file_version_id == target_id)
        with tempfile.TemporaryDirectory(prefix="ppap-core-host-") as root_name:
            root = Path(root_name)
            work, inputs = root / "work", root / "inputs"
            work.mkdir()
            job = write_audit_manifest(inputs, snapshot, sources, programs, target_id, allow_model_calls=allow_model_calls, unavailable_sources=context.unavailable_sources)
            try:
                result = self._task(snapshot, job, inputs, work, CHECK_SECONDS, context)
            except ControllerFault as exc:
                if exc.code != "MODEL_TIMEOUT":
                    # A failed paid operation is not automatically repeated: the
                    # existing runtime journal and attempt marker own reconciliation.
                    raise DeepError(FAULTS.get(exc.code, "deep_execution_failed")) from exc
                # This file ran out of time: each of its clauses is a technical error, never a verdict.
                rows = [_evidence_references(_error(clause, "ERR", "checker_target_timeout_external_usage_unknown"), target_id) for clause in target.binding.entrypoints]
                checked = validate_results(rows, target=target, inputs=snapshot.inputs)
                result = {"input_digest": snapshot.input_digest, **checked, "diagnostics": [], "unavailable_sources": sorted(context.unavailable_sources)}
        return _validate_output(snapshot, result, unavailable_sources=context.unavailable_sources, target_id=target_id)

    def review_target(
        self,
        snapshot: ExecutionSnapshot,
        sources: dict[str, bytes],
        programs: dict[str, dict[str, bytes]],
        target_id: str,
        findings: list[dict[str, Any]],
        context: ExecutionContext,
        *,
        allow_model_calls: bool = True,
    ) -> dict[str, Any]:
        """One deep task: review one file in its own guest, within DEEP_TOTAL_SECONDS. Its failure only leaves this file unreviewed."""
        if target_id in context.unavailable_sources or not findings:
            return {"state": "degraded", "reason": "deep_input_missing"}
        if not allow_model_calls:
            return {"state": "degraded", "reason": "deep_review_failed", "detail": "deep_requires_model"}
        context.raise_if_cancelled()
        with tempfile.TemporaryDirectory(prefix="ppap-core-host-") as root_name:
            root = Path(root_name)
            work, inputs = root / "work", root / "inputs"
            work.mkdir()
            job = write_audit_manifest(inputs, snapshot, sources, programs, target_id, operation="review-target", findings=findings, unavailable_sources=context.unavailable_sources)
            try:
                return self._task(snapshot, job, inputs, work, DEEP_TOTAL_SECONDS, context, on_activity=context.activity)["review"]
            except ControllerFault as exc:
                if exc.code == "CANCELLED":
                    raise DeepError("deep_cancelled") from exc
                return {"state": "degraded", "reason": "deep_timeout" if exc.code == "MODEL_TIMEOUT" else "deep_review_failed", "detail": exc.code}
            except DeepError as exc:
                if exc.code == "deep_cancelled":
                    raise
                return {"state": "degraded", "reason": "deep_review_invalid", "detail": exc.code}

    def execute_snapshot(
        self,
        snapshot: ExecutionSnapshot,
        sources: dict[str, bytes],
        programs: dict[str, dict[str, bytes]],
        context: ExecutionContext,
        *,
        analysis_mode: AnalysisMode = "standard",
        allow_model_calls: bool = True,
    ) -> tuple[dict, dict[str, dict[str, str]] | None, dict[tuple[str, str], dict]]:
        """Check every file, then in deep mode review every file; one task each, in order.

        Returns the validated (and, in deep mode, reviewed) result, per-target deep states and clause review records.
        """
        total = len(snapshot.targets)
        parts = []
        for index, target in enumerate(snapshot.targets):
            context.progress({"phase": "checking", "completed_count": index, "total_count": total})
            parts.append(self.check_target(snapshot, sources, programs, target.file_version_id, context, allow_model_calls=allow_model_calls))
        result = _combine(parts, context.unavailable_sources, snapshot.input_digest)
        if analysis_mode == "standard":
            return result, None, {}
        from agent.deep_review import merge_reviews

        states = {}
        for index, target in enumerate(snapshot.targets):
            context.progress({"phase": "checking", "completed_count": index, "total_count": total})
            findings = [row for row in result["findings"] if row["file_version_id"] == target.file_version_id]
            states[target.file_version_id] = self.review_target(snapshot, sources, programs, target.file_version_id, findings, context, allow_model_calls=allow_model_calls)
        return merge_reviews(snapshot, result, states)

    def execute(self, spec, files: dict[str, bytes], *, context: ExecutionContext):
        from agent.core_result import core_response

        snapshot = self.snapshot_for_request(spec)
        if (
            snapshot is None
            or snapshot.input_digest != spec.checker_snapshot_digest
            or snapshot.tenant_id != spec.tenant_id
            or snapshot.round_id != spec.round_id
            or snapshot.runtime_revision != spec.executor_revision
            or snapshot.initiating_user_id != spec.initiating_user_id
            or snapshot.model_identity != spec.model_identity
        ):
            raise DeepError("deep_input_unauthorized")
        programs: dict[str, dict[str, bytes]] = {}
        for target in snapshot.targets:
            suite = self.suite_for_revision(target.suite_revision_id, snapshot.tenant_id)
            if suite.revision.resolved_digest != target.suite_digest:
                raise DeepError("deep_integrity_mismatch")
            code = {name: body for name, body in suite.files.items() if name in {"checker.py", "resources.py"}}
            if checker_digest(code) != target.binding.checker_digest:
                raise DeepError("deep_integrity_mismatch")
            programs[target.binding.checker_digest] = code
        analysis_mode: AnalysisMode = "deep" if spec.analysis_mode == "deep" else "standard"
        result, deep, reviews = self.execute_snapshot(snapshot, files, programs, context, analysis_mode=analysis_mode)
        context.progress({"phase": "validating"})
        return core_response(spec, snapshot, result, deep=deep, reviews=reviews)
