"""UDS envelopes for Deep execution. extra=forbid; no host path/command/mount/image/env."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_version_id: str
    role: Literal["target", "reference"]
    filename: str
    sha256: str
    content_type: str = ""
    rule_version_id: str | None = None
    rule_title: str | None = None
    rule_body: dict[str, Any] | None = None
    rule_execution: dict[str, Any] | None = None


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_revision: str
    round_id: str
    files: list[FrozenFile]
    executor: Literal["ppap-deep"]
    executor_revision: str


class DeepFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_key: str
    classification: str
    item: str
    priority: str
    status: Literal["PASS", "NG", "ERR"]
    suggestions: str = ""
    locator: dict[str, Any] | None = None


class DeepCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checked: list[str]
    unchecked: list[str]
    locator_gap: bool
    rule_items: list[dict[str, Any]] = Field(default_factory=list)


class ExecuteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_revision: str
    round_id: str
    files: list[FrozenFile]
    findings: list[DeepFinding]
    coverage: DeepCoverage
    note: str = "deep_audit"


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
