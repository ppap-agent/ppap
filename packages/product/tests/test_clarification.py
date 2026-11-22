"""The clarification loop, decided from the task's state and the confirmed facts alone."""

import pytest
from product import clarification as c
from product import rulebooks

TASK = c.task_id_for("tenant", "uaes", "v9.0")
OPERATOR = {"prepare": True, "confirm": True}


def revision(number, texts, *, clarified=False, readings=None):
    clauses = [{"id": clause, "category": "", "text": text, "digest": rulebooks.clause_digest(text), "change": None, "reading": (readings or {}).get(clause)} for clause, text in texts.items()]
    return {"version": "v9.0", "revision": number, "clarified": clarified, "documents": [{"key": "A", "title": "A 文件", "clauses": clauses}]}


def facts(rev, cards=(), roles=OPERATOR):
    return {"roles": roles, "revision": rev, "cards": list(cards)}


def review(findings=()):
    return {"requirements": [{"quote": "已签名", "role": "necessary", "scope": "签名", "meaning": "要有签名。"}], "findings": list(findings)}


AMBIGUITY = {
    "id": "1",
    "kind": "ambiguity",
    "quotes": ["已签名"],
    "title": "签字方式",
    "question": "电子签名算已签名吗？",
    "example": "批准人为电子签名。",
    "challenge": "原文未限定签名介质，其余要求在两种理解下均满足。",
    "readings": [
        {"label": "电子签名也算", "meaning": "电子签名构成已签名。", "basis": "原文未限定载体。", "verdict": "通过"},
        {"label": "仅手写算", "meaning": "电子签名不能代替手写。", "basis": "签名通常指手写。", "verdict": "不通过"},
    ],
}


def act(state, found, code, action, value, clause=None, by="审查员"):
    issue = next(i for i in c.issues(TASK, state, found) if i["code"] == code and (clause is None or i["subject"]["key"] == clause))
    assert action in {a["id"] for a in issue["allowed_actions"]}
    return c.apply(state, c.decide(state, found, issue, action, value, by))


def answered(key, rev, *answers):
    return {
        "card": {"key": key, "items": [key], "question": "q", "clauses": ["A 文件 A:1"]},
        "revision": rev,
        "closed": False,
        "answers": [{"answerer": name, "choice": choice, "text": None} for name, choice in answers],
    }


def test_a_round_from_review_to_the_next_revision():
    state, r0 = c.new_state("uaes", "v9.0"), revision(0, {"A:1": "须已签名", "A:2": "须盖章"})
    view = c.project(TASK, state, facts(r0))
    assert view["phase"] == "reviewing" and view["next"]["tool"] == "get_issue"

    # A review that misquotes the clause is refused whole, with everything wrong with it; the other clause is untouched.
    with pytest.raises(c.Invalid) as refused:
        act(state, facts(r0), "CLAUSE_REVIEW", "submit_review", review([{**AMBIGUITY, "quotes": ["已盖章"], "challenge": "短"}]), "A:1")
    assert len(refused.value.problems) == 2
    state = act(state, facts(r0), "CLAUSE_REVIEW", "submit_review", review([AMBIGUITY]), "A:1")
    assert c.project(TASK, state, facts(r0))["phase"] == "reviewing"
    state = act(state, facts(r0), "CLAUSE_REVIEW", "submit_review", {**review(), "requirements": [{"quote": "须盖章", "role": "necessary", "scope": "盖章", "meaning": "要盖章。"}]}, "A:2")

    # The clause without findings raises no card; the one question waits for the operator to send it.
    view = c.project(TASK, state, facts(r0))
    assert view["phase"] == "ready_to_send" and view["result"]["progress"]["reviewed_without_findings"] == 1
    (card,) = c._standing(state, facts(r0), r0)["unsent"]
    assert card["key"] == "A:1#1" and [o["label"] for o in card["options"]] == ["理解一：电子签名也算", "理解二：仅手写算"]
    # One generator: the card is exactly the one the offline questionnaire build makes for the same finding.
    questionnaire = c._script("questionnaire")
    (finding,) = state["reviews"]["A:1"]["findings"]
    assert card == questionnaire.clause_card(finding, questionnaire.clause_name("A 文件", "A:1"), "须已签名", 0)
    assert questionnaire.card_key("A:1#1", 2) == "A:1#r2.1"
    assert not next(i for i in c.issues(TASK, state, facts(r0, roles={"prepare": True, "confirm": False})) if i["code"] == "CARDS_READY")["allowed_actions"]

    sent = facts(r0, [answered("A:1#1", 0)])
    view = c.project(TASK, state, sent)
    assert view["phase"] == "awaiting_answers" and view["next"]["kind"] == "wait"

    # Two people answer differently: the task says so and leaves the choice to a person.
    split = facts(r0, [answered("A:1#1", 0, ("张三", "A"), ("李四", "B"))])
    issue = next(i for i in c.issues(TASK, state, split) if i["code"] == "CLAUSE_ANSWERED")
    assert "答复不一致" in issue["message"]
    both = facts(r0, [answered("A:1#1", 0, ("张三", "A"), ("李四", "A"))])
    with pytest.raises(c.Invalid):
        act(state, both, "CLAUSE_ANSWERED", "propose_text", {"text": "须已签名", "kind": "changed", "note": "x", "cards": ["A:1#1"]}, "A:1")
    state = act(state, both, "CLAUSE_ANSWERED", "propose_text", {"text": "须已签名，手写或电子均可", "kind": "changed", "note": "电子签名也算", "cards": ["A:1#1"]}, "A:1")
    view = c.project(TASK, state, both)
    assert view["phase"] == "ready_to_revise" and view["next"]["kind"] == "user_action"

    # The request that writes the next revision is one the revision store accepts: named grounds, same clause names.
    body = c.next_revision(state, r0)
    assert body["changes"]["A:1"]["sources"] == [{"question_key": "A:1#1", "answerers": ["张三", "李四"]}]
    assert rulebooks.revision_problems(body["documents"], body["changes"], r0["documents"]) == []

    # On the next revision only the reworded clause is reviewed again, and the settled question is not asked again.
    r1 = revision(1, {"A:1": "须已签名，手写或电子均可", "A:2": "须盖章"})
    closed = [{**answered("A:1#1", 0, ("张三", "A")), "closed": True}]
    view = c.project(TASK, state, facts(r1, closed))
    assert [i["subject"]["key"] for i in view["issues"]] == ["A:1"] and view["phase"] == "reviewing"
    state = act(state, facts(r1, closed), "CLAUSE_REVIEW", "submit_review", review(), "A:1")
    view = c.project(TASK, state, facts(r1, closed))
    assert view["phase"] == "ready_to_clarify" and view["result"]["progress"]["cards"] == {"unsent": 0, "waiting": 0, "answered": 0, "settled": 0}


def test_an_unanswered_card_is_asked_again_and_an_open_answer_is_followed_up():
    state, r0 = c.new_state("uaes", "v9.0"), revision(0, {"A:1": "须已签名"})
    state = act(state, facts(r0), "CLAUSE_REVIEW", "submit_review", review([AMBIGUITY]), "A:1")
    half = facts(r0, [answered("A:1#1", 0, ("张三", None))])
    state = act(state, half, "CLAUSE_ANSWERED", "ask_further", {"follows": "A:1#1", "question": "盖章能代替签名吗？", "why": "答复只说了电子签名。"}, "A:1")
    (card,) = c._standing(state, half, r0)["unsent"]
    assert (card["key"], card["follows"]) == ("A:1#1.f1", "A:1#1")
    # A card asked on an earlier revision and never answered goes to the current revision's questionnaire again.
    r1 = revision(1, {"A:1": "须已签名"})
    earlier = [answered("A:1#1", 0, ("张三", None)), answered("A:1#1.f1", 0)]
    assert [card["key"] for card in c._standing(state, facts(r1, earlier), r1)["unsent"]] == ["A:1#1.f1"]


def test_readings_follow_a_clarified_revision_and_keep_the_clause_terms():
    state, text = c.new_state("uaes", "v9.0"), "须“已签名”，3 人"
    r1 = revision(1, {"A:1": text}, clarified=True)
    with pytest.raises(c.Invalid):
        act(state, facts(r1), "CLAUSE_READING", "submit_reading", {"checks": ["签名栏"], "passes": ["有签名"], "fails": ["无签名"], "manual": []}, "A:1")
    reading = {"checks": ["签名栏"], "passes": ["3 人均“已签名”"], "fails": ["任一人未签"], "manual": []}
    state = act(state, facts(r1), "CLAUSE_READING", "submit_reading", reading, "A:1")
    view = c.project(TASK, state, facts(r1))
    assert view["phase"] == "ready_to_publish" and c.pending_readings(state, r1)["A:1"]["reading"] == reading
    done = c.project(TASK, state, facts(revision(1, {"A:1": text}, clarified=True, readings={"A:1": reading})))
    assert done["phase"] == "completed" and done["next"]["kind"] == "done"


def test_a_new_version_starts_from_submitted_clauses_and_only_an_operator_confirms():
    state, nothing = c.new_state("uaes", "v9.0"), {"roles": {"prepare": True, "confirm": False}, "cards": []}
    documents = [{"key": "A", "title": "A 文件", "clauses": [{"id": "A:1", "category": "", "text": "须已签名"}]}]
    with pytest.raises(c.Invalid):
        act(state, nothing, "CLAUSES_REQUIRED", "submit_clauses", {"documents": [{**documents[0], "clauses": [{"id": "B:1", "category": "", "text": "x"}]}]})
    state = act(state, nothing, "CLAUSES_REQUIRED", "submit_clauses", {"documents": documents})
    view = c.project(TASK, state, nothing)
    assert view["phase"] == "importing" and view["next"]["kind"] == "blocked"
    assert "confirm_clauses" in {a["id"] for a in c.issues(TASK, state, {**nothing, "roles": OPERATOR})[0]["allowed_actions"]}
