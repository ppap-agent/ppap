"""Deterministic published-rule impact, including unchanged clauses."""

from product.rules import diff_rules


def body(*rows):
    return {"rules": [{"id": ident, "rule_to_check": text} for ident, text in rows], "checker_binding": {"R1": "checker:threshold", "R2": "checker:shared"}}


def test_clause_identity_text_order_and_numbering_have_explicit_diffs():
    previous = body(("R1", "至少 10"), ("R2", "必须有签字"), ("R3", "批号"))
    current = body(("R4", "批号"), ("R2", "必须有签字"), ("R1", "不得少于 10"))
    changes = diff_rules(previous, current)
    assert [(row.clause_id, row.kind, row.before, row.after) for row in changes] == [
        ("R1", "changed", "至少 10", "不得少于 10"),
        ("R2", "unchanged", "必须有签字", "必须有签字"),
        ("R3", "removed", "批号", None),
        ("R4", "added", None, "批号"),
    ]
    assert changes[0].checker == "checker:threshold"
    assert changes[1].checker == "checker:shared"
    assert changes[3].checker is None
    reordered = {**previous, "rules": list(reversed(previous["rules"]))}
    assert [row.kind for row in diff_rules(previous, reordered)] == ["unchanged"] * 3
