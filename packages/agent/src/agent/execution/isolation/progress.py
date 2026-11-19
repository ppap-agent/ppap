"""Bounded progress records from a guest, without model text."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Progress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["reading", "planning", "checking", "validating"]
    total_clauses: int | None = Field(default=None, ge=0, le=16384, strict=True)
    verified_clauses: int | None = Field(default=None, ge=0, le=16384, strict=True)
    # Tasks of the round: one per file checked, and one more per file reviewed in deep mode.
    completed_count: int | None = Field(default=None, ge=0, le=1024, strict=True)
    total_count: int | None = Field(default=None, ge=1, le=1024, strict=True)

    @model_validator(mode="after")
    def verified_only_after_validation(self) -> Progress:
        if self.verified_clauses is not None and (self.phase != "validating" or self.total_clauses is None or self.verified_clauses > self.total_clauses):
            raise ValueError("Invalid verified progress")
        if (self.completed_count is None) != (self.total_count is None) or (self.completed_count or 0) > (self.total_count or 0):
            raise ValueError("Invalid task progress")
        return self
