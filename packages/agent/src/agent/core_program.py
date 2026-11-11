"""Whole-audit manifest for the existing low-privilege ContainerRuntime guest.

No command, host path, provider credential, or unsigned program comes from RPC.
The controller resolves the immutable Suite and stages a bounded flat directory.
"""

from __future__ import annotations

import json
import tempfile
import time
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from audit.checker_core import ExecutionSnapshot, checker_digest
from audit.protocol import ControllerFault
from pydantic import BaseModel, ConfigDict, Field

from agent.auditor import FAULTS, SemanticRuntime
from agent.checker_spec import CheckerArtifact, SuiteCheckerProgram
from agent.client import DeepError
from agent.execution.budget import MAX_INPUT_FILES, MAX_INPUT_TOTAL
from agent.execution.secure_files import read_json, read_regular
from agent.run import ExecutionContext

MAX_MANIFEST = 4 * 1024 * 1024
MAX_BUNDLE = 16 * 1024 * 1024
_ALLOW_MODEL_CALLS: ContextVar[bool] = ContextVar("core_allow_model_calls", default=True)


class AuditJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["execute-audit"] = "execute-audit"
    execution_id: UUID
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    executor_revision: str = Field(min_length=1)
    analysis_mode: Literal["standard", "deep"] = "standard"
    allow_model_calls: bool = True
    unavailable_sources: tuple[str, ...] = ()

    @classmethod
    def from_snapshot(
        cls, snapshot: ExecutionSnapshot, analysis_mode: Literal["standard", "deep"] = "standard", allow_model_calls: bool = True, unavailable_sources: frozenset[str] = frozenset()
    ) -> AuditJobSpec:
        unavailable = tuple(sorted(unavailable_sources))
        # A restored object cannot replay the outcome of an unavailable attempt.
        availability = ":" + json.dumps(unavailable, separators=(",", ":")) if unavailable else ""
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
    analysis_mode: Literal["standard", "deep"] = "standard",
    allow_model_calls: bool = True,
    unavailable_sources: frozenset[str] = frozenset(),
) -> AuditJobSpec:
    snapshot.validate_sources(sources, unavailable=unavailable_sources)
    if analysis_mode == "deep" and unavailable_sources:
        raise DeepError("deep_input_missing")
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
    job = AuditJobSpec.from_snapshot(snapshot, analysis_mode, allow_model_calls, unavailable_sources)
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
    if job != AuditJobSpec.from_snapshot(snapshot, job.analysis_mode, job.allow_model_calls, unavailable):
        raise ValueError("audit_manifest_identity_mismatch")
    if job.analysis_mode == "deep" and unavailable:
        raise ValueError("audit_generation_requires_complete_sources")
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
    elif job.analysis_mode == "deep":
        raise ValueError("audit_generation_requires_model")
    artifacts: dict[str, CheckerArtifact] = {}
    effective = snapshot
    snapshot.validate_sources(sources, unavailable=unavailable)
    with grant.access_scope() if grant is not None else model_access_scope(None):
        if job.analysis_mode == "deep":
            from agent.execution.checker_guest import develop_checker
            from agent.execution.guest_model import PiRunner

            assert grant is not None
            runner = PiRunner(grant.model_dump(mode="json"))
            deadline = time.monotonic() + runner.wall_seconds
            development = snapshot_development_inputs(snapshot, sources, directory=work / "development-sources")
            for index, target in enumerate(snapshot.targets):
                item = development[target.file_version_id]
                target_work = work / f"development-{index}"
                target_work.mkdir()
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
                artifacts[target.file_version_id] = artifact
            effective = generated_snapshot(snapshot, sources, artifacts)
            programs = {artifact.program_digest: dict(artifact.files) for artifact in artifacts.values()}
        result = run_audit(effective, sources, programs, runtime=CheckerRuntime(allow_model_tools=job.allow_model_calls), unavailable_sources=unavailable)
    payload = {
        **result,
        "operation": "execute-audit",
        "execution_id": str(job.execution_id),
        "executor_revision": snapshot.runtime_revision,
        "accepted_input_digest": snapshot.input_digest,
        "generated_artifacts": {identity: value.model_dump(mode="json") for identity, value in artifacts.items()},
    }
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    if len(encoded) > MAX_BUNDLE:
        raise ValueError("audit_output_limit")
    (work / "result").mkdir(parents=True, exist_ok=True)
    (work / "result" / "bundle.json").write_bytes(encoded)


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
        analysis_mode: Literal["standard", "deep"] = "standard",
        allow_model_calls: bool = True,
    ) -> dict:
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
            raw_artifacts = result.get("generated_artifacts")
            if not isinstance(raw_artifacts, dict) or analysis_mode != "deep" and raw_artifacts:
                raise DeepError("deep_output_invalid")
            artifacts = {identity: CheckerArtifact.model_validate(raw) for identity, raw in raw_artifacts.items()}
            effective = generated_snapshot(snapshot, sources, artifacts) if analysis_mode == "deep" else snapshot
            return _validate_output(effective, result, unavailable_sources=context.unavailable_sources)

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
        context.progress({"phase": "checking"})
        result = self.execute_snapshot(snapshot, files, programs, context, analysis_mode=spec.analysis_mode)
        artifacts = {identity: CheckerArtifact.model_validate(raw) for identity, raw in result["generated_artifacts"].items()}
        if artifacts:
            if self.save_round_artifacts is None:
                raise DeepError("deep_artifact_persistence_unavailable")
            effective = generated_snapshot(snapshot, files, artifacts)
            adopted = self.save_round_artifacts(spec, snapshot, artifacts)
            if set(adopted) != set(artifacts):
                raise DeepError("deep_output_identity_mismatch")
            effective = replace(
                effective,
                targets=tuple(
                    replace(target, suite_revision_id=adopted[target.file_version_id]["suite_revision_id"], suite_digest=adopted[target.file_version_id]["suite_digest"])
                    for target in effective.targets
                ),
            )
        else:
            effective = snapshot
        context.progress({"phase": "validating"})
        return core_response(spec, effective, result)

    def develop_and_run(self, request, *, spec, context):
        # Fixed Oracle evaluation must never give the model hidden documents.
        # Adaptation remains the explicitly authorized model operation; every
        # candidate/baseline trial is credential-free once an Oracle is present.
        token = _ALLOW_MODEL_CALLS.set(getattr(request, "allow_model_calls", True) and not request.oracle)
        try:
            return super().develop_and_run(request, spec=spec, context=context)
        finally:
            _ALLOW_MODEL_CALLS.reset(token)

    def _checker_job(self, spec, audit_file, rule, file_bytes, *, role, context, group_context, operation, prior=None, artifact=None, related_bytes=None, development_feedback=None, source_files=None):
        # Generation uses the existing granted guest. Every fixed execution,
        # including independent holdout, uses exactly the whole-audit Core.
        if operation == "adapt-checker":
            return super()._checker_job(
                spec,
                audit_file,
                rule,
                file_bytes,
                role=role,
                context=context,
                group_context=group_context,
                operation=operation,
                prior=prior,
                artifact=artifact,
                related_bytes=related_bytes,
                development_feedback=development_feedback,
                source_files=source_files,
            )
        from agent.checker_development import FrozenDevelopmentInput
        from agent.checker_spec import CheckerJobSpec, CheckerProgramBundle

        if artifact is None:
            raise DeepError("deep_output_invalid")
        inputs = FrozenDevelopmentInput(audit_file, rule, file_bytes, role, group_context, related_bytes or {})
        if artifact.input_digest != inputs.digest or artifact.executor_revision != spec.executor_revision:
            raise DeepError("deep_output_identity_mismatch")
        snapshot, sources = development_snapshot(inputs, artifact, spec)
        result = self.execute_snapshot(snapshot, sources, {artifact.program_digest: dict(artifact.files)}, context, allow_model_calls=_ALLOW_MODEL_CALLS.get())
        job = CheckerJobSpec(execution_id=UUID(int=0), operation="execute-checker", input_digest=inputs.digest, executor_revision=spec.executor_revision)
        return CheckerProgramBundle(
            operation="execute-checker",
            execution_id=job.expected_execution_id(),
            input_digest=inputs.digest,
            executor_revision=spec.executor_revision,
            results=result["findings"],
            **{key: result[key] for key in ("input_tokens", "output_tokens", "model_requests") if type(result.get(key)) is int},
        )


def development_snapshot(inputs, artifact, spec):
    """Convert authorized trial inputs to the same immutable clause contract."""
    import hashlib

    from audit.checker_core import FrozenInput, FrozenTarget, RuleBinding, clause_ids
    from audit.standard_suites import library_revision

    target_id = str(inputs.audit_file.file_metadata.get("file_version_id") or inputs.audit_file.audit_id)
    source_rows = {target_id: inputs.audit_file}
    source_types = {target_id: inputs.rule.type}
    source_rules = {target_id: inputs.rule}
    if inputs.group_context is not None:
        source_rows.update({str(row.file_metadata.get("file_version_id") or row.audit_id): row for row in inputs.group_context.by_audit_id.values()})
        for kind, rows in inputs.group_context.by_doc_type.items():
            for row in rows:
                identity = str(row.file_metadata.get("file_version_id") or row.audit_id)
                if identity in source_types and source_types[identity] != kind:
                    raise DeepError("deep_input_invalid")
                source_types[identity] = kind
        source_rules.update(inputs.group_context.rules_by_id)
        source_rules[target_id] = inputs.rule
    sources = dict(inputs.related_bytes)
    sources[target_id] = inputs.file_bytes
    if set(source_rows) != set(sources):
        raise DeepError("deep_input_invalid")
    frozen = tuple(
        FrozenInput(
            identity,
            hashlib.sha256(sources[identity]).hexdigest(),
            "target" if identity == target_id else "reference",
            str(row.file_metadata.get("original_filename") or Path(row.file_url).name),
            document_type=source_types.get(identity),
            customer=row.customer,
            model=row.model,
            part=row.part,
            project_id=row.project_id,
            rule_revision_id=source_rules[identity].rule_id if identity in source_rules else "",
            rules=source_rules[identity].model_dump(mode="json") if identity in source_rules else None,
            appendix=tuple(source_rules[identity].model_dump(mode="json").get("appendix") or ()) if identity in source_rules else (),
        )
        for identity, row in source_rows.items()
    )
    rules = inputs.rule.model_dump(mode="json")
    binding = artifact.binding if isinstance(artifact, SuiteCheckerProgram) else None
    key = binding.suite_key if binding is not None else str(rules.get("suite_key") or inputs.rule.rule_id or "development")
    target = FrozenTarget(
        target_id,
        str(inputs.rule.rule_id or "development"),
        key,
        artifact.program_digest,
        rules,
        binding or RuleBinding(key, artifact.program_digest, {clause: "checker:create_audit" for clause in clause_ids(rules)}),
        appendix=tuple(rules.get("appendix") or ()),
    )
    return ExecutionSnapshot(
        tenant_id=spec.tenant_id,
        initiating_user_id=spec.initiating_user_id,
        inputs=frozen,
        targets=(target,),
        runtime_revision=spec.executor_revision,
        library_revision=library_revision(),
        model_identity=dict(spec.model_identity),
        round_id=spec.round_id,
    ), sources


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
    """Host independently checks generated code identity; never imports it."""
    from audit.checker_core import RuleBinding, clause_ids

    expected = snapshot_development_inputs(snapshot, sources)
    if set(artifacts) != set(expected):
        raise DeepError("deep_output_identity_mismatch")
    targets = []
    for target in snapshot.targets:
        artifact = artifacts[target.file_version_id]
        if artifact.input_digest != expected[target.file_version_id].digest or artifact.executor_revision != snapshot.runtime_revision:
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
