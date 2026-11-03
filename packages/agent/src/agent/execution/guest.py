"""Fixed guest entrypoint. Never called by the API or audit worker."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from audit.document_tools import ParserLimitError, ReadLimits, snapshot
from audit.evidence import EvidenceLimitError
from audit.models import Artifact
from audit.protocol import CompileDiagnostic, CompileFailureRecord, CompileValidationReason, GuestFault, SnapshotBundle, SnapshotSpec, SubmissionSpec
from audit.validation import validate_snapshot
from pydantic import Field, model_validator

from agent.execution.guest_model import ModelBudgetError, ModelProtocolError
from agent.execution.secure_files import read_json, read_regular


class InputIntegrityError(ValueError):
    """The immutable input does not match the manifest."""


class Manifest(Artifact):
    spec: SubmissionSpec
    documents: dict[str, str]
    rules: dict[str, str]
    cached_snapshots: dict[str, str]
    cached_plans: dict[str, str]
    resume_trace: str | None = None
    runtime_job_id: str | None = Field(default=None, min_length=1, max_length=256)
    runtime_attempt: int | None = Field(default=None, ge=1, strict=True)
    runtime_source_attempt: int | None = Field(default=None, ge=1, strict=True)
    precompiled_only: bool = Field(default=False, strict=True)
    failed_rules: dict[str, CompileFailureRecord] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def precompiled_rule_set(self) -> Manifest:
        if (self.runtime_job_id is None) != (self.runtime_attempt is None):
            raise InputIntegrityError("Incomplete technical attempt identity")
        if self.runtime_source_attempt is not None:
            if self.runtime_attempt is None or self.runtime_source_attempt >= self.runtime_attempt:
                raise InputIntegrityError("Invalid technical recovery source")
        if not self.precompiled_only:
            if self.failed_rules:
                raise InputIntegrityError("Compile failures require a precompiled audit")
            return self
        plans, failed = set(self.cached_plans), set(self.failed_rules)
        if plans & failed or plans | failed != {rule.rule_id for rule in self.spec.rules}:
            raise InputIntegrityError("Precompiled rules do not cover the frozen rule set")
        for rule in self.spec.rules:
            failure = self.failed_rules.get(rule.rule_id)
            if failure is None:
                continue
            if (failure.rule_id, failure.source_sha256, failure.plan_revision, failure.engine_revision) != (rule.rule_id, rule.sha256, rule.plan_revision, self.spec.semantic_engine_revision):
                raise InputIntegrityError("Compile failure differs from frozen rule")
            if failure.clause_id is not None and failure.clause_id not in {str(clause.get("id") or index) for index, clause in enumerate(rule.clauses, 1)}:
                raise InputIntegrityError("Compile failure clause outside frozen rule")
        return self


class PlanValidationError(ValueError):
    """Deterministic RulePlan rejection. The message stays free of model output."""

    def __init__(self, reason: CompileValidationReason, rule_id: str, clause_id: str | None, message: str, *, schema_errors: list[dict[str, Any]] | None = None) -> None:
        self.reason = reason
        self.rule_id = rule_id
        self.clause_id = clause_id
        self.repair_failed = False
        self.schema_errors = schema_errors or []
        super().__init__(message)


def parse_source(inputs: Path, work: Path, spec: SnapshotSpec) -> SnapshotBundle:
    if spec.execution_id != spec.expected_execution_id():
        raise ValueError("Snapshot execution mismatch")
    path = inputs / ("source" + spec.format)
    raw = read_regular(inputs, (path.name,), ReadLimits().source_bytes)
    if hashlib.sha256(raw).hexdigest() != spec.sha256:
        raise InputIntegrityError("Source integrity mismatch")
    data = snapshot(path)
    validate_snapshot(data, spec.sha256, spec.format)
    bundle = SnapshotBundle(execution_id=spec.execution_id, semantic_engine_revision=spec.semantic_engine_revision, snapshot=data)
    encoded = bundle.model_dump_json().encode()
    if len(encoded) > ReadLimits().output_bytes:
        raise ValueError("Snapshot bundle exceeds byte limit")
    (work / "result").mkdir(parents=True)
    with (work / "result/bundle.json").open("xb") as stream:
        stream.write(encoded)
    return bundle


def run_entrypoint(inputs: Path, work: Path, credentials: Path) -> None:
    from agent.execution.guest_model import PiRunner

    manifest = read_json(inputs, ("manifest.json",), 1024 * 1024)
    if manifest.get("operation") == "snapshot":
        parse_source(inputs, work, SnapshotSpec.model_validate(manifest["spec"]))
        return
    if manifest.get("operation") == "execute-checker":
        from agent.checker_program import run_execute_manifest

        run_execute_manifest(inputs, work)
        return
    # Runtime owns these fixed mountpoints; there are no CLI path/command overrides.
    grant = read_json(credentials, ("model.json",), 16384)
    runner = PiRunner(grant)
    if manifest.get("operation") == "adapt-checker":
        from agent.checker_program import run_adapt_manifest

        run_adapt_manifest(inputs, work, runner, time.monotonic() + runner.wall_seconds)
        return
    if manifest.get("operation") in {"compile", "execute", None} or "operation" not in manifest:
        raise ValueError("RulePlan execution is removed")
    raise ValueError("Unsupported guest operation")


def run_with_faults(inputs: Path, work: Path, credentials: Path) -> None:
    try:
        run_entrypoint(inputs, work, credentials)
        return
    except ParserLimitError:
        fault = GuestFault(code="PARSER_LIMIT")
    except InputIntegrityError:
        fault = GuestFault(code="INPUT_INTEGRITY_ERROR")
    except ModelProtocolError:
        fault = GuestFault(code="MODEL_PROTOCOL_ERROR")
    except (ModelBudgetError, EvidenceLimitError):
        fault = GuestFault(code="RESOURCE_LIMIT")
    except TimeoutError:
        fault = GuestFault(code="MODEL_TIMEOUT")
    except PlanValidationError as error:
        diagnostic = None
        if error.repair_failed:
            diagnostic = CompileDiagnostic(stage="compile_repair_validation", reason=error.reason, rule_id=error.rule_id, clause_id=error.clause_id)
        fault = GuestFault(code="OUTPUT_VALIDATION_ERROR", diagnostic=diagnostic)
    except (ValueError, KeyError, TypeError, RecursionError):
        fault = GuestFault(code="OUTPUT_VALIDATION_ERROR")
    # Unexpected failures (including OOM / killed processes) produce no envelope.
    # The host then reports ISOLATION_UNAVAILABLE.
    (work / "result").mkdir(parents=True, exist_ok=True)
    payload = fault.model_dump(mode="json", exclude_none=True)
    if fault.diagnostic is not None:
        payload["diagnostic"] = fault.diagnostic.model_dump(mode="json")
    (work / "result/fault.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def main() -> None:
    run_with_faults(Path("/inputs"), Path("/work"), Path("/run/ppap"))


if __name__ == "__main__":
    main()
