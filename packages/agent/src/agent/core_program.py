"""Whole-audit manifest for the existing low-privilege ContainerRuntime guest.

No command, host path, provider credential, or unsigned program comes from RPC.
The controller resolves the immutable Suite and stages a bounded flat directory.
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from audit.checker_core import ExecutionSnapshot, checker_digest
from audit.protocol import ControllerFault
from pydantic import BaseModel, ConfigDict, Field

from agent.auditor import FAULTS, SemanticRuntime
from agent.checker_spec import CheckerArtifact
from agent.client import DeepError
from agent.execution.budget import DEEP_DEVELOPMENT_SECONDS, MAX_INPUT_FILES, MAX_INPUT_TOTAL
from agent.execution.secure_files import read_json, read_regular
from agent.run import ExecutionContext

MAX_MANIFEST = 4 * 1024 * 1024
MAX_BUNDLE = 16 * 1024 * 1024
AnalysisMode = Literal["standard", "deep"]
logger = logging.getLogger(__name__)
# Why one target's deep review did not complete; that target ran its Suite program.
DEEP_DEGRADE_REASONS = frozenset({"deep_timeout", "deep_development_failed", "deep_input_missing", "deep_runtime_failed"})


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
        raise ValueError("audit_generation_requires_model")
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
    artifacts: dict[str, CheckerArtifact] = {}
    deep: dict[str, dict[str, str]] = {}
    effective = snapshot
    with grant.access_scope() if grant is not None else model_access_scope(None):
        if job.analysis_mode == "deep":
            assert grant is not None
            artifacts, deep = _develop_targets(snapshot, sources, programs, grant, work, unavailable)
            effective = generated_snapshot(snapshot, sources, artifacts)
            programs = _effective_programs(effective, programs, artifacts)
        result = run_audit(effective, sources, programs, runtime=CheckerRuntime(allow_model_tools=job.allow_model_calls), unavailable_sources=unavailable)
    payload = {
        **result,
        "operation": "execute-audit",
        "execution_id": str(job.execution_id),
        "executor_revision": snapshot.runtime_revision,
        "accepted_input_digest": snapshot.input_digest,
    }
    if job.analysis_mode == "deep":
        payload["generated_artifacts"] = {identity: value.model_dump(mode="json") for identity, value in artifacts.items()}
        payload["deep_targets"] = deep
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    if len(encoded) > MAX_BUNDLE:
        raise ValueError("audit_output_limit")
    (work / "result").mkdir(parents=True, exist_ok=True)
    (work / "result" / "bundle.json").write_bytes(encoded)


def _develop_targets(snapshot, sources, programs, grant, work: Path, unavailable: frozenset[str]):
    """Write one task-specific program per target, starting from its Suite program.

    A target whose development fails or runs out of time keeps its Suite program
    and is reported as not deep-reviewed; it is never left without a result.
    """
    from agent.execution.checker_guest import develop_checker
    from agent.execution.guest_model import PiRunner

    deep = {target.file_version_id: {"state": "degraded", "reason": "deep_input_missing"} for target in snapshot.targets}
    if unavailable:
        # Generation reads every frozen source; a missing one cannot be traced.
        return {}, deep
    runner = PiRunner(grant.model_dump(mode="json"))
    deadline = time.monotonic() + min(runner.wall_seconds, DEEP_DEVELOPMENT_SECONDS)
    development = snapshot_development_inputs(snapshot, sources, directory=work / "development-sources")
    artifacts: dict[str, CheckerArtifact] = {}
    for index, target in enumerate(snapshot.targets):
        if time.monotonic() >= deadline:
            deep[target.file_version_id] = {"state": "degraded", "reason": "deep_timeout"}
            continue
        item = development[target.file_version_id]
        target_work = work / f"development-{index}"
        target_work.mkdir()
        try:
            artifact = develop_checker(
                audit_file=item.audit_file,
                rule=item.rule,
                group_context=item.group_context,
                digest=item.digest,
                executor_revision=snapshot.runtime_revision,
                runner=runner,
                work=target_work,
                deadline=deadline,
                prior_files={name: body.decode("utf-8") for name, body in programs[target.binding.checker_digest].items()},
                require_independent=True,
            )
        except Exception as exc:
            # One target's failed development never stops the audit of the others. The short
            # detail is for the operator log only; the product record keeps state and reason.
            deep[target.file_version_id] = {
                "state": "degraded",
                "reason": "deep_timeout" if time.monotonic() >= deadline else "deep_development_failed",
                "detail": f"{type(exc).__name__}: {exc}"[:300],
            }
            continue
        artifacts[target.file_version_id] = artifact
        deep[target.file_version_id] = {"state": "completed"}
    return artifacts, deep


def _effective_programs(effective, programs, artifacts):
    by_digest = {artifact.program_digest: dict(artifact.files) for artifact in artifacts.values()}
    return {target.binding.checker_digest: by_digest.get(target.binding.checker_digest) or programs[target.binding.checker_digest] for target in effective.targets}


class CoreRuntime(SemanticRuntime):
    """Trusted host adapter: resolve immutable content, then run one audit guest."""

    def __init__(self, runtime: Any, snapshot_for_request, suite_for_revision, *, objects=None, save_round_artifacts=None, timeout: float = 2400) -> None:
        self.runtime, self.snapshot_for_request, self.suite_for_revision = runtime, snapshot_for_request, suite_for_revision
        self.timeout = timeout
        self.objects = objects
        self.save_round_artifacts = save_round_artifacts

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
    ) -> tuple[dict, dict[str, CheckerArtifact], dict[str, dict[str, str]]]:
        """One guest run. Returns the validated result, generated programs and per-target deep states."""
        from agent.execution.audit_runtime import _validate_output

        context.raise_if_cancelled()
        with tempfile.TemporaryDirectory(prefix="ppap-core-host-") as root_name:
            root = Path(root_name)
            work, inputs = root / "work", root / "inputs"
            work.mkdir()
            job = write_audit_manifest(inputs, snapshot, sources, programs, analysis_mode=analysis_mode, allow_model_calls=allow_model_calls, unavailable_sources=context.unavailable_sources)
            try:
                self.runtime.run(job, inputs, work, time.monotonic() + self.timeout, context.cancelled)
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
            raw_artifacts, raw_deep = result.pop("generated_artifacts", None), result.pop("deep_targets", None)
            if analysis_mode == "standard":
                if raw_artifacts is not None or raw_deep is not None:
                    raise DeepError("deep_output_invalid")
                return _validate_output(snapshot, result, unavailable_sources=context.unavailable_sources), {}, {}
            artifacts, deep = _deep_output(snapshot, raw_artifacts, raw_deep)
            # The host re-derives every generated identity; it never imports generated code.
            effective = generated_snapshot(snapshot, sources, artifacts)
            return _validate_output(effective, result, unavailable_sources=context.unavailable_sources), artifacts, deep

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
        if self.save_round_artifacts is None:
            raise DeepError("deep_artifact_persistence_unavailable")
        try:
            result, artifacts, deep = self.execute_snapshot(snapshot, files, programs, context, analysis_mode="deep")
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
        effective = generated_snapshot(snapshot, files, artifacts)
        if artifacts:
            adopted = self.save_round_artifacts(spec, snapshot, artifacts)
            if set(adopted) != set(artifacts):
                raise DeepError("deep_output_identity_mismatch")
            # History names the exact generated program of each deep-reviewed target.
            effective = replace(
                effective,
                targets=tuple(
                    replace(target, suite_revision_id=adopted[target.file_version_id]["suite_revision_id"], suite_digest=adopted[target.file_version_id]["suite_digest"])
                    if target.file_version_id in adopted
                    else target
                    for target in effective.targets
                ),
            )
        context.progress({"phase": "validating"})
        return core_response(spec, effective, result, deep=deep)


def _deep_output(snapshot, raw_artifacts, raw_deep) -> tuple[dict[str, CheckerArtifact], dict[str, dict[str, str]]]:
    targets = {target.file_version_id for target in snapshot.targets}
    if not isinstance(raw_artifacts, dict) or not isinstance(raw_deep, dict) or set(raw_deep) != targets or not set(raw_artifacts) <= targets:
        raise DeepError("deep_output_invalid")
    artifacts = {identity: CheckerArtifact.model_validate(raw) for identity, raw in raw_artifacts.items()}
    deep: dict[str, dict[str, str]] = {}
    for identity, state in raw_deep.items():
        if state == {"state": "completed"} and identity in artifacts:
            deep[identity] = {"state": "completed"}
        elif (
            isinstance(state, dict)
            and set(state) - {"detail"} == {"state", "reason"}
            and state["state"] == "degraded"
            and state["reason"] in DEEP_DEGRADE_REASONS
            and isinstance(state.get("detail", ""), str)
            and identity not in artifacts
        ):
            if state.get("detail"):
                logger.warning("deep_review_degraded %s %s: %s", identity, state["reason"], state["detail"][:300])
            deep[identity] = {"state": "degraded", "reason": state["reason"]}
        else:
            raise DeepError("deep_output_invalid")
    return artifacts, deep


def snapshot_development_inputs(snapshot, sources, *, directory=None):
    """Use one canonical input identity on both trusted host and guest."""
    from audit.contracts import AuditInput, GroupContextInput, RuleInput

    from agent.checker_development import FrozenDevelopmentInput

    snapshot.validate_sources(sources)
    if directory is not None:
        directory.mkdir()
    audits, by_type = {}, {}
    rules = {
        source.file_version_id: RuleInput.model_validate({**source.to_dict()["rules"], "rule_id": source.rule_revision_id, "appendix": source.to_dict()["appendix"]})
        for source in snapshot.inputs
        if source.rules is not None
    }
    rules.update(
        {target.file_version_id: RuleInput.model_validate({**target.to_dict()["rules"], "rule_id": target.suite_revision_id, "appendix": target.to_dict()["appendix"]}) for target in snapshot.targets}
    )
    for index, source in enumerate(snapshot.inputs):
        filename = source.filename
        if directory is not None:
            path = directory / f"source-{index}{Path(filename).suffix.lower()}"
            path.write_bytes(sources[source.file_version_id])
            path.chmod(0o400)
            filename = str(path)
        audit = AuditInput(
            audit_id=source.file_version_id,
            file_url=filename,
            project_id=source.project_id,
            customer=source.customer,
            model=source.model,
            part=source.part,
            file_metadata={"file_version_id": source.file_version_id, "sha256": source.sha256, "role": source.role, "original_filename": source.filename},
        )
        audits[source.file_version_id] = audit
        if source.document_type:
            by_type.setdefault(source.document_type, []).append(audit)
    group = GroupContextInput(task_group_id=snapshot.round_id, by_audit_id=audits, by_doc_type=by_type, rules_by_id=rules)
    return {
        target.file_version_id: FrozenDevelopmentInput(audits[target.file_version_id], rules[target.file_version_id], sources[target.file_version_id], "target", group, dict(sources))
        for target in snapshot.targets
    }


def generated_snapshot(snapshot, sources, artifacts):
    """Bind each deep-reviewed target to its generated program; the others keep their Suite.

    The host independently checks generated code identity; it never imports it.
    """
    from audit.checker_core import RuleBinding, clause_ids

    if not artifacts:
        return snapshot
    expected = snapshot_development_inputs(snapshot, sources)
    if not set(artifacts) <= set(expected):
        raise DeepError("deep_output_identity_mismatch")
    targets = []
    for target in snapshot.targets:
        artifact = artifacts.get(target.file_version_id)
        if artifact is None:
            targets.append(target)
            continue
        if artifact.input_digest != expected[target.file_version_id].digest or artifact.executor_revision != snapshot.runtime_revision or artifact.program_digest != checker_digest(artifact.files):
            raise DeepError("deep_output_identity_mismatch")
        targets.append(
            replace(
                target,
                binding=RuleBinding(target.suite_key, artifact.program_digest, {clause: "checker:create_audit" for clause in clause_ids(target.rules)}),
                verification="unverified",
                samples_not_rerun=False,
            )
        )
    return replace(snapshot, targets=tuple(targets))
