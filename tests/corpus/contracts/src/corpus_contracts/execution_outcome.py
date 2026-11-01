"""Canonical V2 corpus execution outcome.

Human CSV remains expected-status authority. Engine execution is normalized
here once and reused by the single-format baseline and PDF↔Excel contracts.
Suggestion text, traceback, and Python exception types are not part of equality.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EXECUTION_RESULT = "result"
EXECUTION_FAILURE = "execution_failure"
MISSING_RULE = "missing_rule"

VALID_STATUSES = frozenset({"PASS", "NG", "ERR"})
VALID_EXECUTION_CLASSES = frozenset({EXECUTION_RESULT, EXECUTION_FAILURE, MISSING_RULE})


@dataclass(frozen=True)
class ExecutionOutcome:
    status: str
    execution_class: str
    diagnostic_code: str | None = None

    def __post_init__(self) -> None:
        status = str(self.status or "").strip().upper()
        object.__setattr__(self, "status", status)
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid execution status: {status!r}")
        if self.execution_class not in VALID_EXECUTION_CLASSES:
            raise ValueError(f"invalid execution class: {self.execution_class!r}")
        if self.execution_class == EXECUTION_RESULT and status == "ERR":
            raise ValueError("ERR cannot use execution_class=result")
        if self.execution_class != EXECUTION_RESULT and status != "ERR":
            raise ValueError(f"{self.execution_class} requires status ERR")

    @property
    def semantic(self) -> tuple[str, str, str | None]:
        return (self.status, self.execution_class, self.diagnostic_code)


MISSING_OUTCOME = ExecutionOutcome(status="ERR", execution_class=MISSING_RULE)


def O(  # noqa: E743 - short outcome constructor used by corpus tables
    status: str,
    execution_class: str | None = None,
    diagnostic_code: str | None = None,
) -> ExecutionOutcome:
    normalized = str(status or "").strip().upper()
    if execution_class is None:
        execution_class = EXECUTION_FAILURE if normalized == "ERR" else EXECUTION_RESULT
    return ExecutionOutcome(
        status=normalized,
        execution_class=execution_class,
        diagnostic_code=diagnostic_code,
    )


def stable_diagnostic_code(result: Any) -> str | None:
    evidence = getattr(result, "evidence", None)
    diagnostics = getattr(evidence, "diagnostics", ()) or ()
    codes = [str(getattr(item, "code", "") or "") for item in diagnostics if str(getattr(item, "severity", "") or "") == "error" and str(getattr(item, "code", "") or "")]
    return codes[0] if codes else None


def outcome_from_agent_rule(
    *,
    rule_index: int,
    agent_rule_count: int,
    result: Any | None = None,
    raised: BaseException | None = None,
) -> ExecutionOutcome:
    if rule_index > agent_rule_count:
        return ExecutionOutcome(status="ERR", execution_class=MISSING_RULE)
    if raised is not None or result is None:
        return ExecutionOutcome(status="ERR", execution_class=EXECUTION_FAILURE)
    status = (getattr(result, "status", "") or "").upper() or "ERR"
    if status == "ERR":
        return ExecutionOutcome(
            status="ERR",
            execution_class=EXECUTION_FAILURE,
            diagnostic_code=stable_diagnostic_code(result),
        )
    return ExecutionOutcome(status=status, execution_class=EXECUTION_RESULT)
