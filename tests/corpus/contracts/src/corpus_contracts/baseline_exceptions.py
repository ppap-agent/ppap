"""Known V2 oracle mismatches, including explicitly accepted model differences.

This is not a second expected-status oracle. The human CSV remains the only
PASS/NG authority. Each record freezes one observed baseline behavior at a
(file, file_type, rule) location: CSV expected status plus the canonical
ExecutionOutcome. Suggestion text is not authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from corpus_contracts.execution_outcome import ExecutionOutcome, O


@dataclass(frozen=True)
class ObservedRuleOutcome:
    expected_status: str
    actual: ExecutionOutcome
    detail: str = field(default="", compare=False)

    @property
    def is_mismatch(self) -> bool:
        return str(self.expected_status or "").strip().upper() != self.actual.status


@dataclass(frozen=True)
class BaselineRuleBehavior:
    file_name: str
    file_type: str
    rule_index: int
    expected_status: str
    actual: ExecutionOutcome
    alternatives: frozenset[ExecutionOutcome] = frozenset()

    @property
    def location(self) -> tuple[str, str, int]:
        return (self.file_name, self.file_type, self.rule_index)


def _norm_status(value: str) -> str:
    return str(value or "").strip().upper()


def behavior_matches(known: BaselineRuleBehavior, observed: ObservedRuleOutcome) -> bool:
    return _norm_status(known.expected_status) == _norm_status(observed.expected_status) and observed.actual in {known.actual, *known.alternatives}


def classify_v2_baseline_outcomes(
    file_name: str,
    file_type: str,
    observed: Mapping[int, ObservedRuleOutcome],
    known: Iterable[BaselineRuleBehavior] | None = None,
) -> tuple[frozenset[int], frozenset[int], frozenset[int]]:
    records = {item.rule_index: item for item in (BASELINE_RULE_BEHAVIORS if known is None else known) if item.file_name == file_name and item.file_type == file_type}
    new_mismatches: set[int] = set()
    drifted: set[int] = set()
    mismatched = {rule for rule, outcome in observed.items() if outcome.is_mismatch}
    for rule, outcome in observed.items():
        if not outcome.is_mismatch:
            continue
        record = records.get(rule)
        if record is None:
            new_mismatches.add(rule)
        elif not behavior_matches(record, outcome):
            drifted.add(rule)
    stale = frozenset(records) - frozenset(mismatched)
    return frozenset(new_mismatches), frozenset(drifted), stale


def B(
    file_name: str,
    file_type: str,
    rule_index: int,
    expected_status: str,
    actual_status: str,
    diagnostic_code: str | None = None,
    *,
    execution_class: str | None = None,
    alternatives: tuple[str, ...] = (),
) -> BaselineRuleBehavior:
    actual = O(actual_status, execution_class, diagnostic_code)
    return BaselineRuleBehavior(
        file_name=file_name,
        file_type=file_type,
        rule_index=rule_index,
        expected_status=_norm_status(expected_status),
        actual=actual,
        alternatives=frozenset(O(status) for status in alternatives),
    )


# Observed from the current required V2 corpus. Human CSV remains expected
# authority. ERR records freeze execution_failure without raised:* types.
# Product EvidenceDiagnostic codes may be stored on ExecutionOutcome when stable.
BASELINE_RULE_BEHAVIORS: frozenset[BaselineRuleBehavior] = frozenset()
