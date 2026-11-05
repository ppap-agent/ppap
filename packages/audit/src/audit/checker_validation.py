"""Host-owned independent checker evaluation, separate from publication state."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClauseEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clause_id: str
    expected: Literal["PASS", "NG", "NA"]
    actual: str | None
    evidence_locatable: bool
    checks_performed: int = Field(ge=0)
    baseline_actual: str | None = None

    @property
    def matched(self) -> bool:
        return self.actual == self.expected and self.evidence_locatable and self.checks_performed > 0

    @property
    def kind(self) -> str:
        if self.actual is None:
            return "missing"
        if self.actual in {"UNABLE", "ERR"}:
            return self.actual
        if self.expected == "NG" and self.actual == "PASS":
            return "missed_detection"
        if self.expected == "PASS" and self.actual == "NG":
            return "false_alarm"
        return "match" if self.matched else "mismatch"

    @property
    def regression(self) -> bool:
        return self.baseline_actual == self.expected and not self.matched


class CaseEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    ordinal: int = Field(ge=1)
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    split: Literal["training", "holdout"]
    coverage: bool
    clauses: tuple[ClauseEvaluation, ...]
    error_code: str | None = None

    @property
    def passed(self) -> bool:
        return self.coverage and self.error_code is None and bool(self.clauses) and all(row.matched for row in self.clauses)


class SuiteValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_revision_id: str | None = None
    suite_digest: str | None = None
    library_revision: str = ""
    related_suite_digests: dict[str, str] = Field(default_factory=dict)
    program_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    oracle_revision: str | None = None
    related_revision_ids: tuple[str, ...] = ()
    cases: tuple[CaseEvaluation, ...] = ()
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unique_cases(self) -> SuiteValidationReport:
        if len({row.case_id for row in self.cases}) != len(self.cases) or len({row.ordinal for row in self.cases}) != len(self.cases):
            raise ValueError("duplicate oracle sample identity")
        if any(row.split != ("holdout" if row.ordinal % 4 == 0 else "training") for row in self.cases):
            raise ValueError("oracle split does not match frozen sample ordinal")
        return self

    @property
    def verified(self) -> bool:
        return (
            bool(self.oracle_revision)
            and len(self.cases) >= 4
            and any(row.split == "holdout" for row in self.cases)
            and any(row.split == "training" for row in self.cases)
            and not self.reasons
            and all(row.passed for row in self.cases)
        )

    @property
    def total_samples(self) -> int:
        return len(self.cases)

    @property
    def passed_samples(self) -> int:
        return sum(row.passed for row in self.cases)

    @property
    def new_errors(self) -> int:
        return sum(clause.regression for row in self.cases for clause in row.clauses)

    @property
    def proof_digest(self) -> str:
        body = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        return hashlib.sha256(body).hexdigest()

    def receipt(self) -> dict:
        value = self.model_dump(mode="json")
        value.update(verified=self.verified, proof_digest=self.proof_digest, total_samples=self.total_samples, passed_samples=self.passed_samples, new_errors=self.new_errors)
        for case, serialized in zip(self.cases, value["cases"], strict=True):
            serialized["passed"] = case.passed
            for clause, result in zip(case.clauses, serialized["clauses"], strict=True):
                result.update(kind=clause.kind, regression=clause.regression)
        return value
