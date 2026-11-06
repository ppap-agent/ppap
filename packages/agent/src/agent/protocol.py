"""UDS envelopes for Deep execution. extra=forbid; no host path/command/mount/image/env."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_version_id: str
    file_id: str = ""
    role: Literal["target", "reference"]
    filename: str
    sha256: str
    content_type: str = ""
    rule_version_id: str | None = None
    rule_title: str | None = None
    rule_body: dict[str, Any] | None = None
    rule_execution: dict[str, Any] | None = None
    rule_source_file_version_id: str | None = None


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_revision: str
    round_id: str
    files: list[FrozenFile]
    executor: Literal["ppap-deep", "ppap-checker-core"]
    executor_revision: str
    tenant_id: str = ""
    initiating_user_id: str = ""
    analysis_mode: Literal["standard", "deep"] = "standard"
    model_identity: dict[str, str] = Field(default_factory=dict)
    checker_snapshot_digest: str = Field(default="", pattern=r"^$|^[a-f0-9]{64}$")
    job_id: str = ""
    attempt: int = Field(default=1, ge=1)
    resume_attempt: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def prior_attempt(self) -> ExecuteRequest:
        if self.resume_attempt is not None and self.resume_attempt >= self.attempt:
            raise ValueError("Recovery requires an earlier attempt of the same job")
        return self


class DeepFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_key: str
    classification: str
    item: str
    priority: str
    status: Literal["PASS", "NG", "ERR", "UNABLE", "NA"]
    suggestions: str = ""
    locator: dict[str, Any] | None = None


class DeepVerification(BaseModel):
    """Host source/trace validation, not proof of business interpretation."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["host_source_trace_v1"]
    round_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    attempt: int = Field(ge=1, strict=True)
    executor_revision: str = Field(min_length=1)
    semantic_engine_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class DeepCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checked: list[str]
    unchecked: list[str]
    locator_gap: bool
    rule_items: list[dict[str, Any]] = Field(default_factory=list)
    verification: DeepVerification | None = None
    checker_execution: dict[str, Any] | None = None
    clarifications: list[dict[str, Any]] = Field(default_factory=list, max_length=256)


class ExecuteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_revision: str
    round_id: str
    files: list[FrozenFile]
    findings: list[DeepFinding]
    coverage: DeepCoverage
    note: str = "deep_audit"
    usage: dict[str, int] = Field(default_factory=dict)
    usage_source: Literal["unknown", "provider", "controlled"] = "unknown"


class DeepProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["reading", "planning", "checking", "validating"]
    completed_count: int | None = Field(default=None, ge=0)
    total_count: int | None = Field(default=None, ge=0)


class ProgressEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["progress"]
    progress: DeepProgress


class RequestEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["execute"]
    spec: ExecuteRequest


class ResultEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["result"]
    result: ExecuteResponse


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["error"]
    code: str = Field(min_length=1)


class DevelopmentEnvelope(BaseModel):
    """Only durable identities cross the worker/controller optimization boundary."""

    model_config = ConfigDict(extra="forbid")
    op: Literal["develop"]
    operation_id: str = Field(min_length=36, max_length=36)
    job_id: str = Field(min_length=36, max_length=36)
    tenant_id: str = Field(min_length=36, max_length=36)
    attempt: int = Field(ge=1, strict=True)
    lease_owner: str = Field(min_length=1, max_length=200)


class PrecheckEnvelope(BaseModel):
    """A leased persistent task only; programs and originals never enter RPC."""

    model_config = ConfigDict(extra="forbid")
    op: Literal["precheck"]
    job_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    tenant_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    attempt: int = Field(ge=1, strict=True)
    lease_owner: str = Field(min_length=1, max_length=200)
