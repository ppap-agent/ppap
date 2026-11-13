"""UDS envelopes for Deep execution. extra=forbid; no host path/command/mount/image/env."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.execution.activity import Activity


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
    executor: Literal["ppap-checker-core"]
    executor_revision: str
    tenant_id: str = ""
    initiating_user_id: str = ""
    # Frozen at acceptance. Deep writes a task-specific program per target, starting
    # from the target's Suite program; a target whose development fails runs the Suite.
    analysis_mode: Literal["standard", "deep"] = "standard"
    model_identity: dict[str, str] = Field(default_factory=dict)
    checker_snapshot_digest: str = Field(default="", pattern=r"^$|^[a-f0-9]{64}$")
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


class DeepCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checked: list[str]
    unchecked: list[str]
    locator_gap: bool
    rule_items: list[dict[str, Any]] = Field(default_factory=list)
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


class ActivityEnvelope(BaseModel):
    """A live deep step for viewers; the Worker broadcasts it and nothing stores it."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["activity"]
    activity: Activity


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
