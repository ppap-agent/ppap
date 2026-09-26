"""Bounded progress records from a guest, without model text."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Progress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["reading", "planning", "checking", "validating"]
    total_clauses: int | None = Field(default=None, ge=0, le=16384, strict=True)
    verified_clauses: int | None = Field(default=None, ge=0, le=16384, strict=True)

    @model_validator(mode="after")
    def verified_only_after_validation(self) -> Progress:
        if self.verified_clauses is not None and (
            self.phase != "validating"
            or self.total_clauses is None
            or self.verified_clauses > self.total_clauses
        ):
            raise ValueError("Invalid verified progress")
        return self
