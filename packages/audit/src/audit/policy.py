"""One non-secret execution identity shared by acceptance and the Deep runtime."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from audit.audits import DEEP_PROTOCOL_REVISION

GUEST_UID = 10001
# The deep review time of one file, in seconds (PPAP_DEEP_REVIEW_SECONDS). The host reads it and hands it to the guest.
DEEP_REVIEW_SECONDS = 600
DEEP_REVIEW_SECONDS_RANGE = (60, 1800)
# How many files of a round are reviewed side by side (PPAP_DEEP_REVIEW_CONCURRENCY); the gateway sends that many requests at once.
DEEP_REVIEW_CONCURRENCY = 1
DEEP_REVIEW_CONCURRENCY_RANGE = (1, 8)
PI_VERSION = "1.5.0"


class DeepExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1, max_length=128)
    model_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    app_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    reasoning_effort: Literal["xhigh", "high", "medium", "low"] = "medium"
    sandbox_profile: Literal["off", "ppap"]
    memory_mib: int = Field(default=1536, ge=512, le=4096, strict=True)
    # Threads uid 10001 may hold, counted across every guest on the node: a round reviewing 4 files peaks near 120.
    max_processes: int = Field(default=512, ge=16, le=1024, strict=True)
    executor_label: str = Field(default=DEEP_PROTOCOL_REVISION, min_length=1)

    def engine_revision(self) -> str:
        manifest = {
            "adapter": "container-only-v1",
            "app_revision": self.app_revision,
            "model": self.model,
            "model_revision": self.model_revision,
            "reasoning_effort": self.reasoning_effort,
            "sandbox": self.sandbox_profile,
            "memory_mib": self.memory_mib,
            "max_processes": self.max_processes,
            "executor_label": self.executor_label,
            "uid": GUEST_UID,
            "pi_version": PI_VERSION,
            "semantic_contract": "rule-logical-document-v10",
        }
        return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def runtime_identity(self) -> dict[str, str]:
        """Public execution identity; credentials and endpoints are excluded."""
        return {"agent": "pi", "agent_version": PI_VERSION, "model": self.model, "reasoning_effort": self.reasoning_effort, "executor_revision": self.engine_revision()}
