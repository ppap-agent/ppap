"""Deterministic, advisory comparisons of immutable audit results. No model calls."""

from __future__ import annotations

from dataclasses import dataclass

MAX_REVIEW_HINTS = 3
RESULTS = frozenset({"PASS", "NG", "UNABLE", "NA"})


@dataclass(frozen=True)
class HintResult:
    round_id: str
    file_version_id: str
    item_key: str
    clause_id: str
    suite_revision_id: str
    sha256: str
    status: str
    filename: str
    result_attempt: int = 1


def review_hints(current: list[HintResult], earlier: list[HintResult], pairs: set[frozenset[str]], handled: dict[str, dict]) -> list[dict]:
    """Inputs are already authorized; earlier results are newest first."""
    hints = []
    for result in current:
        if result.status not in RESULTS:
            continue
        comparison = next(
            (
                item
                for item in earlier
                if item.round_id != result.round_id
                and item.sha256 == result.sha256
                and item.suite_revision_id == result.suite_revision_id
                and item.clause_id == result.clause_id
                and item.status in RESULTS
                and item.status != result.status
            ),
            None,
        )
        reason = "same_file_changed"
        if comparison is None:
            reason = "cross_format_differs"
            comparison = next(
                (
                    item
                    for item in current
                    if frozenset({result.file_version_id, item.file_version_id}) in pairs
                    and item.suite_revision_id == result.suite_revision_id
                    and item.clause_id == result.clause_id
                    and item.status in RESULTS
                    and item.status != result.status
                ),
                None,
            )
        if comparison:
            hint = {
                "item_key": result.item_key,
                "reason": reason,
                "compared_round_id": comparison.round_id,
                "compared_file_version_id": comparison.file_version_id,
                "compared_item_key": comparison.item_key,
                "clause_id": result.clause_id,
                "suite_revision_id": result.suite_revision_id,
                "current_status": result.status,
                "compared_status": comparison.status,
                "compared_filename": comparison.filename,
                "result_attempt": result.result_attempt,
                "compared_result_attempt": comparison.result_attempt,
            }
            if handled.get(result.item_key) != hint:
                hints.append(hint)
        if len(hints) == MAX_REVIEW_HINTS:
            break
    return hints
