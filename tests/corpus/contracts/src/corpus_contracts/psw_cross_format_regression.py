"""Explainable PSW cross-format regression comparison helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from audit.contracts import AuditResultItem


class DifferenceKind(StrEnum):
    EQUIVALENT = "equivalent"
    CORRECT_FORMAT_DIFFERENCE = "correct_format_difference"
    CONVERSION_LOSS = "conversion_loss"
    AUDIT_LOGIC_REGRESSION = "audit_logic_regression"


@dataclass(frozen=True, slots=True)
class PswAuditSnapshot:
    source_format: str
    key_fields: dict[str, str]
    results: tuple[AuditResultItem, ...]


@dataclass(frozen=True, slots=True)
class PswCrossFormatDiff:
    kind: DifferenceKind
    key_field_mismatches: tuple[str, ...]
    rule_status_mismatches: tuple[int, ...]


def _diagnostic_codes(snapshot: PswAuditSnapshot) -> set[str]:
    return {diagnostic.code for result in snapshot.results if result.evidence is not None for diagnostic in result.evidence.diagnostics}


def compare_psw_snapshots(
    reference: PswAuditSnapshot,
    candidate: PswAuditSnapshot,
    *,
    allowed_format_rule_differences: frozenset[int] = frozenset(),
) -> PswCrossFormatDiff:
    """Classify differences without changing a rule result or expectation."""
    field_names = sorted(set(reference.key_fields) | set(candidate.key_fields))
    key_mismatches = tuple(name for name in field_names if reference.key_fields.get(name, "") != candidate.key_fields.get(name, ""))
    rule_count = max(len(reference.results), len(candidate.results))
    status_mismatches = tuple(
        index
        for index in range(1, rule_count + 1)
        if (reference.results[index - 1].status if index <= len(reference.results) else "MISSING") != (candidate.results[index - 1].status if index <= len(candidate.results) else "MISSING")
    )

    if not key_mismatches and not status_mismatches:
        kind = DifferenceKind.EQUIVALENT
    elif not key_mismatches and set(status_mismatches) <= allowed_format_rule_differences:
        kind = DifferenceKind.CORRECT_FORMAT_DIFFERENCE
    elif any(code.startswith(("psw.doc.conversion_", "psw.pdf.conversion_", "psw.structure.")) for code in _diagnostic_codes(candidate)):
        kind = DifferenceKind.CONVERSION_LOSS
    else:
        kind = DifferenceKind.AUDIT_LOGIC_REGRESSION

    return PswCrossFormatDiff(
        kind=kind,
        key_field_mismatches=key_mismatches,
        rule_status_mismatches=status_mismatches,
    )


__all__ = [
    "DifferenceKind",
    "PswAuditSnapshot",
    "PswCrossFormatDiff",
    "compare_psw_snapshots",
]
