"""Clause text check points and the per-clause state shown to a person. No model and no files."""

import json

import pytest
from product.formalization.text import FormalizationError


def test_a_current_candidate_is_not_blocked_by_an_older_confirmation():
    from product.store.formalizations import _summary

    latest = {"id": "new", "formalization_revision": 2, "completeness": "complete", "unresolved": [], "display_digest": "d", "source_rule_digest": "new-digest"}
    older = {"candidate_id": "old", "formalization_revision": 1, "confirmed_at": "2026-11-10T00:00:00+00:00", "source_rule_digest": "old-digest"}
    assert _summary("c1", latest, older, "new-digest")["state"] == "candidate"
    assert _summary("c1", {**latest, "id": "old", "source_rule_digest": "old-digest"}, {**older, "formalization_revision": 2}, "new-digest")["state"] == "source_changed"


_POINTS = json.dumps({"sections": [{"title": "检查", "items": ["“批号”“数量”条件成立 → 通过；否则 → 不通过"]}], "review": [], "suggested_clause": ""}, ensure_ascii=False)


@pytest.mark.parametrize(
    "clause_text",
    [
        "当“批号”列下内容不为空，且同行的“数量”内容不为空则通过，否则不通过。",
        "如果报告类型为年度，并且报告上有签名则合格，否则不合格",
    ],
)
def test_a_leading_condition_waits_for_the_person_to_choose_requirement_or_scope(clause_text):
    from product.formalization.text import accept_text_points, clarified, text_prompts
    from product.store.formalizations import _summary

    def read(decided=None):
        return accept_text_points(_POINTS, source_rule_version_id="rule-1", clause_id="c1", clause_text=clause_text, decided=decided)

    asked = read()
    question = asked["spec"]["unresolved"][0]
    assert asked["completeness"] == "partial"
    assert [item["id"] for item in question["choices"]] == ["required", "scope"]
    assert all(item["summary"] and item["counterexample"] for item in question["choices"])
    assert question["choices"][0]["counterexample"] != question["choices"][1]["counterexample"]
    row = {
        "id": "a",
        "formalization_revision": 1,
        "completeness": asked["completeness"],
        "unresolved": asked["spec"]["unresolved"],
        "display_digest": asked["display_digest"],
        "source_rule_digest": "d",
    }
    assert _summary("c1", row, None, "d")["state"] == "needs_clarification"

    held = read({question["id"]: "undecided"})
    assert held["completeness"] == "partial"
    assert [item["id"] for item in held["spec"]["unresolved"]] == [question["id"], "undecided"]

    readings = {}
    for choice in question["choices"]:
        decided = clarified({"obligations": asked["spec"]["obligations"]}, {"unresolved_id": question["id"], "choice_id": choice["id"]})
        chosen = read(decided)
        assert chosen["completeness"] == "complete" and chosen["spec"]["unresolved"] == []
        assert choice["summary"] in chosen["cnl_text"]
        # A later revision of the chosen reading keeps the choice without asking again.
        assert read(clarified(chosen["spec"], None))["display_digest"] == chosen["display_digest"]
        assert choice["summary"] in text_prompts(clause_text=clause_text, chosen=[choice["summary"]])[1]
        readings[choice["id"]] = chosen["display_digest"]
    assert readings["required"] != readings["scope"]


@pytest.mark.parametrize("clause_text", ["当“批号”列下内容不为空则通过，否则不通过。", "当报告类型为年度，且报告上有签名则通过，否则不适用。", "报告类型为年度且有签名"])
def test_a_clause_without_a_separable_condition_is_not_asked(clause_text):
    from product.formalization.text import accept_text_points

    read = accept_text_points(_POINTS, source_rule_version_id="rule-1", clause_id="c1", clause_text=clause_text)
    assert read["completeness"] == "complete" and read["spec"]["unresolved"] == []


def test_a_rejection_is_a_confirmable_reading_that_keeps_only_the_defined_parts():
    from product.formalization.text import accept_text_points, carried_rejections, reject_reading, text_prompts
    from product.store.formalizations import _summary

    clause_text = "当“批号”列下内容不为空，且同行的“数量”内容不为空则通过，否则不通过。"
    points = json.dumps({"sections": [{"title": "检查", "items": ["“批号”“数量”非空 → 通过", "数量合理 → 通过"]}], "review": ["何为合理？"], "suggested_clause": ""}, ensure_ascii=False)
    asked = accept_text_points(points, source_rule_version_id="rule-1", clause_id="c1", clause_text=clause_text)
    question = asked["spec"]["unresolved"][0]["id"]
    assert asked["completeness"] == "partial"
    plain = accept_text_points(points, source_rule_version_id="rule-1", clause_id="c1", clause_text="批号与数量非空")
    # A reading with nothing rejected keeps the digest it had before rejections existed.
    assert (
        "rejections" not in plain["spec"]
        and plain["display_digest"] == accept_text_points(points, source_rule_version_id="rule-1", clause_id="c1", clause_text="批号与数量非空", rejections=[])["display_digest"]
    )

    part = reject_reading(asked["spec"], scope="part", reason="何为合理没有判定标准", obligation_ids=["p2"], unresolved_ids=[question], source_rule_version_id="rule-1", clause_id="c1")
    assert part["completeness"] == "complete" and part["spec"]["unresolved"] == []
    assert [item["id"] for item in part["spec"]["obligations"]] == ["p1"]
    assert part["spec"]["rejections"] == [{"id": "x1", "scope": "part", "parts": ["数量合理 → 通过", asked["spec"]["unresolved"][0]["reason"]], "reason": "何为合理没有判定标准"}]
    assert "已拒绝（无可程序化判定标准）：数量合理 → 通过；" in part["cnl_text"] and "待澄清：何为合理？" in part["cnl_text"]
    assert (
        part["formal_digest"]
        != reject_reading(asked["spec"], scope="part", reason="另一个原因", obligation_ids=["p2"], unresolved_ids=[question], source_rule_version_id="rule-1", clause_id="c1")["formal_digest"]
    )
    # A question left open keeps the reading partial; the rejection settles only what it names.
    assert reject_reading(asked["spec"], scope="part", reason="原因", obligation_ids=["p2"], source_rule_version_id="rule-1", clause_id="c1")["completeness"] == "partial"

    # A later rewrite keeps the rejected parts out and the rejection on.
    carried = carried_rejections(part["spec"])
    again = accept_text_points(points, source_rule_version_id="rule-1", clause_id="c1", clause_text="批号与数量非空", rejections=carried)
    assert again["spec"]["rejections"] == part["spec"]["rejections"]
    assert "数量合理 → 通过" in json.loads(text_prompts(clause_text="批号与数量非空", rejected=carried[0]["parts"])[1].split("\n", 1)[1])["rejected_parts"]

    whole = reject_reading(None, scope="clause", reason="原文只问是否满足要求", source_rule_version_id="rule-1", clause_id="c1")
    assert whole["completeness"] == "complete" and whole["spec"]["obligations"] == [] and whole["spec"]["unresolved"] == []
    assert whole["cnl_text"] == "已拒绝（无可程序化判定标准）：整条条款。原因：原文只问是否满足要求"
    assert carried_rejections(whole["spec"]) == []

    for bad in (
        {"scope": "part", "reason": " ", "obligation_ids": ["p2"]},
        {"scope": "part", "reason": "原因"},
        {"scope": "part", "reason": "原因", "obligation_ids": ["p1", "p2"]},
        {"scope": "part", "reason": "原因", "obligation_ids": ["p9"]},
        {"scope": "clause", "reason": "原因", "obligation_ids": ["p1"]},
        {"scope": "maybe", "reason": "原因", "parts": ["x"]},
    ):
        with pytest.raises(FormalizationError) as refused:
            reject_reading(asked["spec"], source_rule_version_id="rule-1", clause_id="c1", **bad)
        assert refused.value.code == "invalid_rejection"

    def row(reading):
        return {
            "id": "a",
            "formalization_revision": 1,
            "completeness": reading["completeness"],
            "unresolved": reading["spec"]["unresolved"],
            "rejections": reading["spec"].get("rejections") or [],
            "display_digest": reading["display_digest"],
            "source_rule_digest": "d",
        }

    current = {"candidate_id": "a", "formalization_revision": 1, "confirmed_at": None, "source_rule_digest": "d"}
    assert _summary("c1", row(whole), None, "d")["state"] == "candidate"
    assert _summary("c1", row(whole), current, "d")["state"] == "rejected"
    assert _summary("c1", row(whole), current, "d")["rejections"][0]["reason"] == "原文只问是否满足要求"
    assert _summary("c1", row(part), current, "d")["state"] == "confirmed"
    assert _summary("c1", row(whole), current, "changed")["state"] == "source_changed"


def test_each_clause_is_counted_by_what_it_waits_for_from_a_person():
    from product.store.formalizations import _attention

    running, ended = {"state": "running", "lease_expired": False}, {"state": "failed", "lease_expired": False}
    ready = {"assumptions": [{"id": "suggestion", "kind": "suggested_clause", "text": "改写"}]}
    open_standard = {"assumptions": [{"id": "r1", "text": "签字须是手签还是电子签？"}]}
    assert _attention("confirmed", ready, running) == "decided"
    assert _attention("rejected", None, None) == "decided"
    assert _attention("candidate", ready, running) == "running"
    # A reading lost with its Worker is not reported as still running.
    assert _attention("not_started", None, {**running, "lease_expired": True}) == "failed"
    assert _attention("needs_clarification", ready, None) == "question"
    assert _attention("candidate", open_standard, ended) == "review"
    # The suggested wording alone asks the person nothing.
    assert _attention("candidate", ready, None) == "ready"
    assert _attention("source_changed", ready, ended) == "failed"
    assert _attention("not_started", None, None) == "waiting"
