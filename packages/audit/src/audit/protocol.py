"""Business-only controller protocol. No caller-selected paths or commands."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from .models import Artifact, Locator

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
FileFormat = Literal[".xlsx", ".xlsm", ".xls", ".docx", ".pdf"]
RuleFormat = Literal[".xlsx", ".xlsm", ".xls", ".docx", ".pdf", ".json"]
BusinessId = Annotated[str, Field(min_length=1, max_length=256)]
_SOURCE_LOCATORS = TypeAdapter(Annotated[list[Locator], Field(min_length=1, max_length=256)])
_SOURCE_KINDS = {
    ".xlsx": {"xlsx_cell", "xlsx_range"},
    ".xlsm": {"xlsx_cell", "xlsx_range"},
    ".xls": {"xlsx_cell", "xlsx_range"},
    ".docx": {"docx_paragraph", "docx_cell"},
    ".pdf": {"pdf_block", "pdf_cell"},
}


class DocumentRef(Artifact):
    audit_id: BusinessId
    filename: str = ""
    sha256: Sha256
    format: FileFormat
    role: Literal["target", "context"]


class RuleRef(Artifact):
    rule_id: BusinessId
    sha256: Sha256
    format: RuleFormat
    rule_version_id: BusinessId | None = None
    plan_revision: Sha256
    # Frozen clause text and, for native files, its allowed typed source locations.
    clauses: list[dict[str, Any]] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def published_source_identity(self) -> RuleRef:
        if any(not isinstance(clause.get("rule_to_check"), str) or not clause["rule_to_check"].strip() for clause in self.clauses):
            raise ValueError("Frozen clause requires nonempty rule_to_check text")
        if self.format == ".json":
            if self.rule_version_id is None:
                raise ValueError("Published rule requires a rule version identity")
            return self
        for clause in self.clauses:
            locators = _SOURCE_LOCATORS.validate_python(clause.get("source_locators"), strict=True)
            if any(locator.kind not in _SOURCE_KINDS[self.format] for locator in locators):
                raise ValueError("Native clause source locator format mismatch")
            if len({locator.model_dump_json() for locator in locators}) != len(locators):
                raise ValueError("Duplicate native clause source locator")
            clause["source_locators"] = [locator.model_dump(mode="json") for locator in locators]
        return self


class SubmissionSpec(Artifact):
    execution_id: UUID
    submission_id: UUID
    project_id: BusinessId
    documents: list[DocumentRef] = Field(min_length=1, max_length=64)
    rules: list[RuleRef] = Field(min_length=1, max_length=32)
    targets: dict[BusinessId, BusinessId]
    policy_revision: Sha256
    semantic_engine_revision: Sha256

    def expected_execution_id(self) -> UUID:
        payload = self.model_dump(mode="json", exclude={"execution_id"})
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return uuid5(NAMESPACE_URL, hashlib.sha256(data).hexdigest())

    @model_validator(mode="after")
    def exact_inputs(self) -> SubmissionSpec:
        docs = {item.audit_id: item for item in self.documents}
        rules = {item.rule_id: item for item in self.rules}
        if len(docs) != len(self.documents) or len(rules) != len(self.rules):
            raise ValueError("Duplicate input identity")
        if not self.targets or set(self.targets) != {item.audit_id for item in self.documents if item.role == "target"}:
            raise ValueError("Targets must exactly match target documents")
        if set(self.targets.values()) != set(rules):
            raise ValueError("Rules must exactly match target rules")
        for rule in self.rules:
            ids = [str(item.get("id") or i) for i, item in enumerate(rule.clauses, 1)]
            if len(set(ids)) != len(ids):
                raise ValueError("Duplicate rule clause identity")
        return self


class CompileSpec(Artifact):
    execution_id: UUID
    submission_id: UUID | None = None
    project_id: BusinessId
    rules: list[RuleRef] = Field(min_length=1, max_length=32)
    policy_revision: Sha256
    semantic_engine_revision: Sha256

    def expected_execution_id(self) -> UUID:
        payload = self.model_dump(mode="json", exclude={"execution_id"})
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return uuid5(NAMESPACE_URL, "compile:" + hashlib.sha256(data).hexdigest())

    @model_validator(mode="after")
    def exact_rules(self) -> CompileSpec:
        identities = {item.rule_id for item in self.rules}
        if len(identities) != len(self.rules):
            raise ValueError("Duplicate rule identity")
        for rule in self.rules:
            ids = [str(item.get("id") or i) for i, item in enumerate(rule.clauses, 1)]
            if len(set(ids)) != len(ids):
                raise ValueError("Duplicate rule clause identity")
        return self


class SnapshotSpec(Artifact):
    execution_id: UUID
    submission_id: UUID
    project_id: BusinessId
    sha256: Sha256
    format: FileFormat
    semantic_engine_revision: Sha256

    def expected_execution_id(self) -> UUID:
        payload = self.model_dump(mode="json", exclude={"execution_id"})
        return uuid5(NAMESPACE_URL, "snapshot:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())


class SnapshotBundle(Artifact):
    execution_id: UUID
    semantic_engine_revision: Sha256
    snapshot: dict[str, Any]


class SnapshotRequest(Artifact):
    method: Literal["snapshot_source"]
    spec: SnapshotSpec
    consumer_id: UUID


ErrorCode = Literal[
    "CONTROLLER_UNAVAILABLE",
    "ISOLATION_UNAVAILABLE",
    "INPUT_INTEGRITY_ERROR",
    "PARSER_LIMIT",
    "MODEL_TIMEOUT",
    "MODEL_PROTOCOL_ERROR",
    "OUTPUT_VALIDATION_ERROR",
    "EVIDENCE_VALIDATION_ERROR",
    "CANCELLED",
    "RESOURCE_LIMIT",
    "INVALID_REQUEST",
]


CompileValidationReason = Literal["coverage_mismatch", "source_locator_unresolved", "source_locator_empty", "plan_schema_invalid"]


class CompileDiagnostic(Artifact):
    """Small reason for a failed compile repair. Not the rejected plan."""

    stage: Literal["compile_repair_validation"]
    reason: CompileValidationReason
    rule_id: BusinessId
    clause_id: str | None = Field(default=None, max_length=256)


class CompileFailureRecord(Artifact):
    """A compile-repair failure bound to one frozen rule and execution policy."""

    rule_id: BusinessId
    source_sha256: Sha256
    plan_revision: Sha256
    engine_revision: Sha256
    stage: Literal["compile_repair_validation"]
    reason: CompileValidationReason
    clause_id: str | None = Field(default=None, max_length=256)


class GuestFault(Artifact):
    """Bounded guest diagnostics; no exception text or caller-controlled payload."""

    ok: Literal[False] = False
    code: Literal["INPUT_INTEGRITY_ERROR", "PARSER_LIMIT", "MODEL_TIMEOUT", "MODEL_PROTOCOL_ERROR", "OUTPUT_VALIDATION_ERROR", "EVIDENCE_VALIDATION_ERROR", "RESOURCE_LIMIT"]
    diagnostic: CompileDiagnostic | None = None


class ControllerFault(RuntimeError):
    def __init__(self, code: ErrorCode, diagnostic: CompileDiagnostic | None = None) -> None:
        self.code = code
        self.diagnostic = diagnostic
        super().__init__(code)


class RecoveryRecord(Artifact):
    """Host-written source for one explicitly resumed technical attempt."""

    spec: SubmissionSpec
    job_id: BusinessId
    attempt: int = Field(ge=1, strict=True)
    expires_at: AwareDatetime
    usage: dict[str, Annotated[int, Field(ge=0, strict=True)]]
    uncertain: bool = Field(strict=True)
    failed_rules: dict[str, CompileFailureRecord] = Field(default_factory=dict, max_length=32)
    snapshot_digests: dict[str, Sha256] = Field(default_factory=dict, max_length=64)
    plan_digests: dict[str, Sha256] = Field(default_factory=dict, max_length=32)
    @model_validator(mode="after")
    def exact_usage(self) -> RecoveryRecord:
        if set(self.usage) != {"input_tokens", "output_tokens", "tool_calls", "completed_tool_calls", "compactions", "model_requests"}:
            raise ValueError("Invalid recovery usage")
        return self


class HealthRequest(Artifact):
    method: Literal["health"]
    operation: Literal["snapshot", "compile", "audit"] = "audit"


class RunRequest(Artifact):
    method: Literal["run_submission"]
    spec: SubmissionSpec
    consumer_id: UUID


class CompileRequest(Artifact):
    method: Literal["compile_rules"]
    spec: CompileSpec
    consumer_id: UUID


class CancelRequest(Artifact):
    method: Literal["cancel_submission"]
    execution_id: UUID
    consumer_id: UUID


Request = Annotated[HealthRequest | RunRequest | CompileRequest | CancelRequest | SnapshotRequest, Field(discriminator="method")]


class Response(Artifact):
    ok: bool
    code: ErrorCode | None = None
    ready: bool | None = None
    engine_revision: Sha256 | None = None
    snapshot_result: SnapshotBundle | None = None
