"""Bounded progress records from a guest, without model text."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The guest appends one record per line and never rewrites the file; the host reads what was added.
PROGRESS_FILE = "progress.jsonl"


class Progress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["reading", "planning", "checking", "validating"]
    total_clauses: int | None = Field(default=None, ge=0, le=16384, strict=True)
    verified_clauses: int | None = Field(default=None, ge=0, le=16384, strict=True)
    # Tasks of the round: one per file checked, and one more per file reviewed in deep mode.
    completed_count: int | None = Field(default=None, ge=0, le=1024, strict=True)
    total_count: int | None = Field(default=None, ge=1, le=1024, strict=True)
    # The task under way: checking a file's programs, or reviewing it in deep mode, and which file.
    stage: Literal["check", "review"] | None = None
    target: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")

    @model_validator(mode="after")
    def verified_only_after_validation(self) -> Progress:
        if self.verified_clauses is not None and (self.phase != "validating" or self.total_clauses is None or self.verified_clauses > self.total_clauses):
            raise ValueError("Invalid verified progress")
        if (self.completed_count is None) != (self.total_count is None) or (self.completed_count or 0) > (self.total_count or 0):
            raise ValueError("Invalid task progress")
        if (self.stage is None) != (self.target is None) or (self.stage is not None and self.completed_count is None):
            raise ValueError("Invalid task progress")
        return self


def parse_progress(data: bytes) -> tuple[Progress | None, int]:
    """The latest record among the complete lines in `data`, and how many bytes they used.

    A partial last line waits for its end. Every complete line must be a valid record.
    """
    end = data.rfind(b"\n") + 1
    latest = None
    for line in data[:end].split(b"\n"):
        if line:
            latest = Progress.model_validate_json(line)
    return latest, end
