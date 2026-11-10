"""Deterministic published-rule impact, including unchanged clauses."""

import json
from types import SimpleNamespace

from product.rules import diff_rules
from product.store.rule_impact import rule_impact


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


def test_rule_impact_notices_semantic_change_without_text_change():
    rules = {"rules": [{"id": "c1", "rule_to_check": "同一句原文"}]}

    def suite(formalizations=None, revision="before"):
        body = dict(rules)
        if formalizations is not None:
            body["formalizations"] = formalizations
        files = {"rules.json": json.dumps(body).encode(), "appendix.json": b"[]"}
        return SimpleNamespace(files=files, revision=SimpleNamespace(id=revision))

    same = rule_impact(suite(), suite(revision="after"))
    changed = rule_impact(suite(), suite({"c1": {"completeness": "complete", "cnl_text": "恰好一条"}}, revision="after"))
    assert same["changed"] is False
    assert changed["changed"] is True
    assert changed["clauses"][0]["kind"] == "unchanged"
    from audit.checker_core import rule_digest
    from product.store.rule_impact import semantic_rule_packet

    text = {"rules": [{"id": "c1", "rule_to_check": "同一句原文"}]}
    confirmed = {**text, "formalizations": {"c1": {"completeness": "complete", "cnl_text": "恰好一条"}}}
    assert rule_digest(semantic_rule_packet(text), []) == rule_digest(semantic_rule_packet({"rules": text["rules"]}), [])
    assert rule_digest(semantic_rule_packet(text), []) != rule_digest(semantic_rule_packet(confirmed), [])


def test_source_review_follows_checked_clauses_only():
    from product.rules import source_review

    def context(*checked):
        clauses = [{"id": str(index), "checked": flag} for index, flag in enumerate(checked, start=1)]
        return {"registration": json.dumps({"basis": {"clauses": clauses}})}

    assert source_review(None) == "unchecked"
    assert source_review(context(False, False)) == "unchecked"
    assert source_review(context(True, False)) == "partial"
    assert source_review(context(True, True)) == "checked"
