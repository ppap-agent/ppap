"""UDS envelopes for Deep execution. extra=forbid; no host path/command/mount/image/env."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    executor: Literal["ppap-deep"]
    executor_revision: str
    tenant_id: str = ""
    job_id: str = ""
    attempt: int = Field(default=1, ge=1)


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


class ExecuteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_revision: str
    round_id: str
    files: list[FrozenFile]
    findings: list[DeepFinding]
    coverage: DeepCoverage
    note: str = "deep_audit"
    usage: dict[str, int] = Field(default_factory=dict)


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
