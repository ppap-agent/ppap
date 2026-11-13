"""Whole-audit manifest for the existing low-privilege ContainerRuntime guest.

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

from audit.checker_core import ExecutionSnapshot, checker_digest
from audit.protocol import ControllerFault
from pydantic import BaseModel, ConfigDict, Field

from agent.auditor import FAULTS, SemanticRuntime
from agent.client import DeepError
from agent.execution.budget import DEEP_REVIEW_SECONDS, MAX_INPUT_FILES, MAX_INPUT_TOTAL
from agent.execution.secure_files import read_json, read_regular
from agent.run import ExecutionContext

MAX_MANIFEST = 4 * 1024 * 1024
MAX_BUNDLE = 16 * 1024 * 1024
AnalysisMode = Literal["standard", "deep"]


class AuditJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["execute-audit"] = "execute-audit"
    execution_id: UUID
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    executor_revision: str = Field(min_length=1)
    analysis_mode: AnalysisMode = "standard"
    allow_model_calls: bool = True
    unavailable_sources: tuple[str, ...] = ()

    @classmethod
    def from_snapshot(cls, snapshot: ExecutionSnapshot, allow_model_calls: bool = True, unavailable_sources: frozenset[str] = frozenset(), analysis_mode: AnalysisMode = "standard") -> AuditJobSpec:
        unavailable = tuple(sorted(unavailable_sources))
        # A restored object cannot replay the outcome of an unavailable attempt.
        availability = ":" + json.dumps(unavailable, separators=(",", ":")) if unavailable else ""
        # The mode is part of the identity; standard ids are unchanged from before deep mode returned.
        return cls(
            execution_id=uuid5(NAMESPACE_URL, "ppap-audit:" + analysis_mode + ":" + str(allow_model_calls) + ":" + snapshot.input_digest + availability),
            input_digest=snapshot.input_digest,
            executor_revision=snapshot.runtime_revision,
            analysis_mode=analysis_mode,
            allow_model_calls=allow_model_calls,
            unavailable_sources=unavailable,
        )


def write_audit_manifest(
    inputs: Path,
    snapshot: ExecutionSnapshot,
    sources: dict[str, bytes],
    programs: dict[str, dict[str, bytes]],
    *,
    analysis_mode: AnalysisMode = "standard",
    allow_model_calls: bool = True,
    unavailable_sources: frozenset[str] = frozenset(),
) -> AuditJobSpec:
    if analysis_mode == "deep" and not allow_model_calls:
        raise DeepError("deep_requires_model")
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
    job = AuditJobSpec.from_snapshot(snapshot, allow_model_calls, unavailable_sources, analysis_mode)
    raw = json.dumps(
        {"operation": "execute-audit", "spec": job.model_dump(mode="json"), "snapshot": snapshot.to_dict(), "source_paths": source_paths, "program_paths": program_paths}, ensure_ascii=False
    ).encode()
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
    if set(manifest) != {"operation", "spec", "snapshot", "source_paths", "program_paths"} or manifest["operation"] != "execute-audit":
        raise ValueError("audit_manifest_invalid")
    snapshot = ExecutionSnapshot.from_dict(manifest["snapshot"])
    job = AuditJobSpec.model_validate(manifest["spec"])
    unavailable = frozenset(job.unavailable_sources)
    if job != AuditJobSpec.from_snapshot(snapshot, job.allow_model_calls, unavailable, job.analysis_mode):
        raise ValueError("audit_manifest_identity_mismatch")
    if job.analysis_mode == "deep" and not job.allow_model_calls:
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
    with grant.access_scope() if grant is not None else model_access_scope(None):
        # The validated Suite programs always run first; deep review only reads their results.
        result = run_audit(snapshot, sources, programs, runtime=CheckerRuntime(allow_model_tools=job.allow_model_calls), unavailable_sources=unavailable)
    deep = None
    if job.analysis_mode == "deep":
        assert grant is not None
        from agent.deep_review import review_targets
        from agent.execution.activity import ACTIVITY_FILE
        from agent.execution.guest_model import PiRunner

        runner = PiRunner(grant.model_dump(mode="json"))
        # Live steps for viewers; the Controller forwards them and nothing stores them.
        runner.activity = work / ACTIVITY_FILE
        deep = review_targets(snapshot, sources, programs, result["findings"], runner=runner, work=work / "deep-review", seconds=min(runner.wall_seconds, DEEP_REVIEW_SECONDS), unavailable=unavailable)
    payload = {
        **result,
        "operation": "execute-audit",
        "execution_id": str(job.execution_id),
        "executor_revision": snapshot.runtime_revision,
        "accepted_input_digest": snapshot.input_digest,
    }
    if deep is not None:
        payload["deep_reviews"] = deep
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    if len(encoded) > MAX_BUNDLE:
        raise ValueError("audit_output_limit")
    (work / "result").mkdir(parents=True, exist_ok=True)
    (work / "result" / "bundle.json").write_bytes(encoded)


class CoreRuntime(SemanticRuntime):
    """Trusted host adapter: resolve immutable content, then run one audit guest."""

    def __init__(self, runtime: Any, snapshot_for_request, suite_for_revision, *, objects=None, timeout: float = 2400) -> None:
        self.runtime, self.snapshot_for_request, self.suite_for_revision = runtime, snapshot_for_request, suite_for_revision
        self.timeout = timeout
        self.objects = objects

    def execution_revision(self) -> str:
        from audit.checker_core import runtime_revision
        from audit.standard_suites import library_revision

        return runtime_revision(self.runtime.engine_revision(), library_revision())

    def ready(self, operation: str = "audit") -> bool:
        return self.runtime.ready(operation)

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
        """One guest run. Returns the validated (and, in deep mode, reviewed) result, per-target deep states and clause review records."""
        from agent.execution.audit_runtime import _validate_output

        context.raise_if_cancelled()
        with tempfile.TemporaryDirectory(prefix="ppap-core-host-") as root_name:
            root = Path(root_name)
            work, inputs = root / "work", root / "inputs"
            work.mkdir()
            job = write_audit_manifest(inputs, snapshot, sources, programs, analysis_mode=analysis_mode, allow_model_calls=allow_model_calls, unavailable_sources=context.unavailable_sources)
            try:
                # Only a deep review has live steps to show.
                live = {"on_activity": context.activity} if analysis_mode == "deep" else {}
                self.runtime.run(job, inputs, work, time.monotonic() + self.timeout, context.cancelled, **live)
                result = read_json(work, ("result", "bundle.json"), MAX_BUNDLE)
            except ControllerFault as exc:
                # A failed paid operation is not automatically repeated: the
                # existing runtime journal and attempt marker own reconciliation.
                raise DeepError(FAULTS.get(exc.code, "deep_execution_failed")) from exc
            if result.get("execution_id") != str(job.execution_id) or result.get("executor_revision") != snapshot.runtime_revision or result.get("operation") != "execute-audit":
                raise DeepError("deep_output_identity_mismatch")
            context.raise_if_cancelled()
            if result.get("accepted_input_digest") != snapshot.input_digest:
                raise DeepError("deep_output_identity_mismatch")
            raw_deep = result.pop("deep_reviews", None)
            if (analysis_mode == "standard") != (raw_deep is None):
                raise DeepError("deep_output_invalid")
            # The program result is validated exactly as in a standard audit, then checked corrections apply.
            validated = _validate_output(snapshot, result, unavailable_sources=context.unavailable_sources)
            if raw_deep is None:
                return validated, None, {}
            from agent.deep_review import merge_reviews

            try:
                return merge_reviews(snapshot, validated, raw_deep)
            except ValueError as exc:
                raise DeepError("deep_output_invalid") from exc

    def execute(self, spec, files: dict[str, bytes], *, context: ExecutionContext):
        from agent.core_result import core_response
        from agent.execution.audit_runtime import AuditRunError

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
        context.progress({"phase": "checking"})
        if spec.analysis_mode != "deep":
            result, _, _ = self.execute_snapshot(snapshot, files, programs, context)
            context.progress({"phase": "validating"})
            return core_response(spec, snapshot, result)
        try:
            result, deep, reviews = self.execute_snapshot(snapshot, files, programs, context, analysis_mode="deep")
        except (DeepError, AuditRunError, ValueError) as exc:
            # The whole deep guest failed. Its attempt marker stays, so this exact
            # deep execution is never repeated; the Suite programs still give a result.
            context.raise_if_cancelled()
            if isinstance(exc, DeepError) and exc.code in {"deep_input_unauthorized", "deep_integrity_mismatch", "model_unavailable", "deep_lease_lost"}:
                raise
            result, _, _ = self.execute_snapshot(snapshot, files, programs, context)
            context.progress({"phase": "validating"})
            degraded = {target.file_version_id: {"state": "degraded", "reason": "deep_runtime_failed"} for target in snapshot.targets}
            return core_response(spec, snapshot, result, deep=degraded)
        context.progress({"phase": "validating"})
        return core_response(spec, snapshot, result, deep=deep, reviews=reviews)
