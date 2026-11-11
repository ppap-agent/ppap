"""Trusted staging for free preparation in the existing low-privilege guest."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from audit.checker_core import ExecutionSnapshot
from audit.precheck.contract import input_digest, validate_result
from pydantic import BaseModel, ConfigDict, Field

from agent.core_program import MAX_BUNDLE, MAX_MANIFEST, _read_staged, write_audit_manifest
from agent.execution.budget import MAX_INPUT_TOTAL
from agent.execution.secure_files import read_json


class PrecheckJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["execute-precheck"] = "execute-precheck"
    execution_id: UUID
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @classmethod
    def for_payload(cls, payload):
        digest = input_digest(payload)
        return cls(execution_id=uuid5(NAMESPACE_URL, "ppap-precheck:" + digest), input_digest=digest)


def write_precheck_manifest(inputs, payload, snapshot, sources, programs):
    write_audit_manifest(inputs, snapshot, sources, programs)
    manifest = read_json(inputs, ("manifest.json",), MAX_MANIFEST)
    spec = PrecheckJobSpec.for_payload(payload)
    manifest.update(operation="execute-precheck", spec=spec.model_dump(mode="json"), payload=payload)
    encoded = json.dumps(manifest, ensure_ascii=False).encode()
    if len(encoded) > MAX_MANIFEST or sum(path.stat().st_size for path in inputs.iterdir() if path.name != "manifest.json") + len(encoded) > MAX_INPUT_TOTAL:
        raise ValueError("precheck_manifest_limit")
    (inputs / "manifest.json").write_bytes(encoded)
    return spec


def run_precheck_manifest(inputs: Path, work: Path):
    """Guest-only entrypoint. A free operation never reads a model grant."""
    from agent.execution.precheck_runtime import run_precheck

    manifest = read_json(inputs, ("manifest.json",), MAX_MANIFEST)
    if set(manifest) != {"operation", "spec", "snapshot", "source_paths", "program_paths", "payload"} or manifest["operation"] != "execute-precheck":
        raise ValueError("precheck_manifest_invalid")
    payload = manifest["payload"]
    spec = PrecheckJobSpec.model_validate(manifest["spec"])
    if spec != PrecheckJobSpec.for_payload(payload):
        raise ValueError("precheck_manifest_identity_mismatch")
    snapshot = ExecutionSnapshot.from_dict(manifest["snapshot"])
    if snapshot.tenant_id != payload["tenant_id"] or snapshot.initiating_user_id != payload["initiating_user_id"] or snapshot.model_identity or snapshot.round_id:
        raise ValueError("precheck_manifest_identity_mismatch")
    total = 0

    def read(name):
        nonlocal total
        body = _read_staged(inputs, name, MAX_INPUT_TOTAL - total)
        total += len(body)
        return body

    sources = {key: read(name) for key, name in manifest["source_paths"].items()}
    programs = {key: {name: read(path) for name, path in paths.items()} for key, paths in manifest["program_paths"].items()}
    result = run_precheck(payload, snapshot, sources, programs)
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > MAX_BUNDLE:
        raise ValueError("precheck_output_limit")
    (work / "result").mkdir(parents=True, exist_ok=True)
    (work / "result" / "bundle.json").write_bytes(encoded)


def execute_precheck(runtime, payload, snapshot, sources, programs, context, *, timeout=240):
    """The host can stage bytes but must never import an enterprise program."""
    context.raise_if_cancelled()
    with tempfile.TemporaryDirectory(prefix="ppap-precheck-host-") as directory:
        root = Path(directory)
        inputs, work = root / "inputs", root / "work"
        work.mkdir()
        spec = write_precheck_manifest(inputs, payload, snapshot, sources, programs)
        runtime.run(spec, inputs, work, time.monotonic() + timeout, context.cancelled)
        context.raise_if_cancelled()
        return validate_result(payload, read_json(work, ("result", "bundle.json"), MAX_BUNDLE))
