"""One validation case per frozen document/context, with independent clause answers."""

from __future__ import annotations


def feedback_cases(samples: list[dict]) -> list[list[dict]]:
    """Same round and file implies the same immutable reference context.

    Keep every feedback identity and its revision. The case ordinal is inherited
    from the rank of its first historical feedback, including deleted groups,
    never renumbered when an answer is removed.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for sample in samples:
        groups.setdefault((sample["round_id"], sample["file_version_id"]), []).append(sample)
    for members in groups.values():
        expected: dict[str, str] = {}
        for sample in members:
            clause = sample["rule_clause_id"]
            if clause in expected and expected[clause] != sample["expected_status"]:
                raise ValueError("checker_feedback_conflicting_answers")
            expected[clause] = sample["expected_status"]
    return sorted(groups.values(), key=lambda rows: rows[0].get("case_ordinal", rows[0]["ordinal"]))
