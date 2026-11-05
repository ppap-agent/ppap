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

from agent.client import DeepError
from agent.execution.budget import MAX_INPUT_FILES, MAX_INPUT_TOTAL
from agent.execution.secure_files import read_json, read_regular
from agent.run import ExecutionContext

MAX_MANIFEST = 4 * 1024 * 1024
MAX_BUNDLE = 16 * 1024 * 1024


class AuditJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["execute-audit"] = "execute-audit"
    execution_id: UUID
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    executor_revision: str = Field(min_length=1)

    @classmethod
    def from_snapshot(cls, snapshot: ExecutionSnapshot) -> AuditJobSpec:
        return cls(execution_id=uuid5(NAMESPACE_URL, "ppap-audit:" + snapshot.input_digest), input_digest=snapshot.input_digest, executor_revision=snapshot.runtime_revision)


def write_audit_manifest(inputs: Path, snapshot: ExecutionSnapshot, sources: dict[str, bytes], programs: dict[str, dict[str, bytes]]) -> AuditJobSpec:
    snapshot.validate_sources(sources)
    expected = {target.binding.checker_digest for target in snapshot.targets}
    if set(programs) != expected or any(checker_digest(files) != digest for digest, files in programs.items()):
        raise ValueError("checker_program_digest_mismatch")
    if 1 + len(sources) + sum(len(files) for files in programs.values()) > MAX_INPUT_FILES:
        raise ValueError("audit_input_limit")
    inputs.mkdir()
    source_paths, program_paths = {}, {}
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
    job = AuditJobSpec.from_snapshot(snapshot)
    raw = json.dumps({"operation": "execute-audit", "spec": job.model_dump(mode="json"), "snapshot": snapshot.to_dict(), "source_paths": source_paths, "program_paths": program_paths}, ensure_ascii=False).encode()
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
    from documents.model_access import ModelAccess, model_access_scope
    from pydantic import SecretStr

    from agent.execution.audit_runtime import run_audit
    from agent.execution.guest_model import ModelGrant

    manifest = read_json(inputs, ("manifest.json",), MAX_MANIFEST)
    if set(manifest) != {"operation", "spec", "snapshot", "source_paths", "program_paths"} or manifest["operation"] != "execute-audit":
        raise ValueError("audit_manifest_invalid")
    snapshot = ExecutionSnapshot.from_dict(manifest["snapshot"])
    job = AuditJobSpec.model_validate(manifest["spec"])
    if job != AuditJobSpec.from_snapshot(snapshot):
        raise ValueError("audit_manifest_identity_mismatch")
    total = 0

    def read(name: str) -> bytes:
        nonlocal total
        raw = _read_staged(inputs, name, MAX_INPUT_TOTAL - total)
        total += len(raw)
        return raw

    sources = {key: read(name) for key, name in manifest["source_paths"].items()}
    programs = {key: {name: read(path) for name, path in paths.items()} for key, paths in manifest["program_paths"].items()}
    grant = ModelGrant.model_validate(read_json(credentials, ("model.json",), 16384))
    # A temporary local capability is the only model credential the guest sees.
    from urllib.parse import urlsplit

    endpoint = urlsplit(grant.base_url)
    if endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"} or endpoint.username or endpoint.password or grant.model != snapshot.model_identity.get("model"):
        raise ValueError("audit_model_grant_mismatch")
    access = ModelAccess(provider="deepseek", base_url=grant.base_url, model=grant.model, api_key=SecretStr(grant.token))
    if snapshot.model_identity.get("provider") not in {None, access.provider}:
        raise ValueError("audit_model_grant_mismatch")
    with model_access_scope(access):
        result = run_audit(snapshot, sources, programs)
    payload = {**result, "operation": "execute-audit", "execution_id": str(job.execution_id), "executor_revision": snapshot.runtime_revision}
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    if len(encoded) > MAX_BUNDLE:
        raise ValueError("audit_output_limit")
    (work / "result").mkdir(parents=True, exist_ok=True)
    (work / "result" / "bundle.json").write_bytes(encoded)


class CoreRuntime:
    """Trusted host adapter: resolve immutable content, then run one audit guest."""

    def __init__(self, runtime: Any, snapshot_for_request, suite_for_revision, *, timeout: float = 2400) -> None:
        self.runtime, self.snapshot_for_request, self.suite_for_revision = runtime, snapshot_for_request, suite_for_revision
        self.timeout = timeout

    def ready(self, operation: str = "audit") -> bool:
        return self.runtime.ready(operation)

    def execute_snapshot(self, snapshot: ExecutionSnapshot, sources: dict[str, bytes], programs: dict[str, dict[str, bytes]], context: ExecutionContext) -> dict:
        from agent.execution.audit_runtime import _validate_output

        context.raise_if_cancelled()
        with tempfile.TemporaryDirectory(prefix="ppap-core-host-") as root_name:
            root = Path(root_name)
            work, inputs = root / "work", root / "inputs"
            work.mkdir()
            job = write_audit_manifest(inputs, snapshot, sources, programs)
            try:
                self.runtime.run(job, inputs, work, time.monotonic() + self.timeout, context.cancelled)
                result = read_json(work, ("result", "bundle.json"), MAX_BUNDLE)
            except ControllerFault as exc:
                # A failed paid operation is not automatically repeated: the
                # existing runtime journal and attempt marker own reconciliation.
                raise DeepError("deep_model_protocol" if exc.code == "MODEL_PROTOCOL_ERROR" else "deep_execution_failed") from exc
            if result.get("execution_id") != str(job.execution_id) or result.get("executor_revision") != snapshot.runtime_revision or result.get("operation") != "execute-audit":
                raise DeepError("deep_output_identity_mismatch")
            context.raise_if_cancelled()
            return _validate_output(snapshot, result)

    def execute(self, spec, files: dict[str, bytes], *, context: ExecutionContext):
        from agent.core_result import core_response

        snapshot = self.snapshot_for_request(spec)
        if snapshot is None or snapshot.input_digest != spec.checker_snapshot_digest or snapshot.tenant_id != spec.tenant_id or snapshot.round_id != spec.round_id or snapshot.runtime_revision != spec.executor_revision or snapshot.initiating_user_id != spec.initiating_user_id:
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
        result = self.execute_snapshot(snapshot, files, programs, context)
        context.progress({"phase": "validating"})
        return core_response(spec, snapshot, result)
