"""Non-secret identity of the accepted Standard execution configuration."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def config_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class StandardExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    executor_label: Literal["in-repo-engine-v1"] = "in-repo-engine-v1"
    app_revision: str | None
    model: str
    model_config_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    tool_config_revision: str = Field(pattern=r"^[a-f0-9]{64}$")

    def engine_revision(self) -> str:
        return "standard-v1:" + config_digest(self.model_dump(mode="json"))
