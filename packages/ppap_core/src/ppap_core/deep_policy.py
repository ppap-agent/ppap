"""One non-secret execution identity shared by acceptance and the Deep runtime."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ppap_core.audits import DEEP_PROTOCOL_REVISION

GUEST_UID = 10001
GROK_VERSION = "1.0.70"


class DeepExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1, max_length=128)
    model_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    app_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    sandbox_profile: Literal["off", "ppap"]
    memory_mib: int = Field(default=1536, ge=512, le=4096, strict=True)
    max_processes: int = Field(default=256, ge=16, le=1024, strict=True)
    executor_label: str = Field(default=DEEP_PROTOCOL_REVISION, min_length=1)

    def engine_revision(self) -> str:
        manifest = {
            "adapter": "container-only-v1",
            "app_revision": self.app_revision,
            "model": self.model,
            "model_revision": self.model_revision,
            "sandbox": self.sandbox_profile,
            "memory_mib": self.memory_mib,
            "max_processes": self.max_processes,
            "executor_label": self.executor_label,
            "uid": GUEST_UID,
            "grok_version": GROK_VERSION,
            "semantic_contract": "rule-logical-document-v10",
        }
        return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
