"""Original-file questions and model-free evaluation of frozen observations.

These are source facts, not rule interpretations. Evaluation never imports a
Checker or reads a document: only the explicitly recorded predicate is eligible.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from audit.models import public_locator


class FactQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file_version_id: str = Field(min_length=1, max_length=128)
    field: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=1, max_length=1000)
    answer_type: Literal["boolean", "text"]
    locator: dict[str, Any]

    @field_validator("locator")
    @classmethod
    def located(cls, value):
        location = public_locator(value)
        if location is None:
            raise ValueError("fact_question_location_required")
        return location


def fingerprint(question: dict) -> str:
    shown = {key: question[key] for key in ("file_version_id", "field", "prompt", "answer_type", "locator", "clause_ids", "item_keys")}
    return hashlib.sha256(json.dumps(shown, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class BooleanFactEvaluation(BaseModel):
    """An all-required visual check, frozen before any human answer exists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["all_true"] = "all_true"
    file_version_id: str = Field(min_length=1)
    facts: dict[str, bool | None] = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1000)

    @field_validator("facts", mode="before")
    @classmethod
    def strict_facts(cls, value):
        if not isinstance(value, dict) or any(not isinstance(key, str) or not key or (item is not None and type(item) is not bool) for key, item in value.items()):
            raise ValueError("invalid_frozen_fact")
        return value

    def evaluate(self, answers: dict[str, bool | str | None]) -> tuple[str, str]:
        facts = {field: answers.get(field) if observed is None else observed for field, observed in self.facts.items()}
        if any(value is False for value in facts.values()):
            return "NG", "人工确认原件存在不满足项：" + self.description
        if all(value is True for value in facts.values()):
            return "PASS", "人工确认原件事实并确定性复算：" + self.description
        return "UNABLE", "原件仍有未确认事实，尚不能判断：" + self.description
