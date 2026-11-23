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


DRAFT = [{"key": "A", "title": "A 文件", "clauses": [{"id": "A:1", "category": "", "text": "须已签名"}, {"id": "A:2", "category": "", "text": "须盖章"}]}]
SIGNED = {"checks": ["签名栏"], "passes": ["有签名"], "fails": ["没有签名"], "manual": []}
STAMPED = {"checks": ["盖章处"], "passes": ["有章"], "fails": ["无章"], "manual": []}


def take(found, steps, chosen=None):
    """What the rulebook store does with the chosen steps, as the facts the task reads next."""
    chosen = [step["id"] for step in steps] if chosen is None else chosen
    rev, cards = found.get("revision"), list(found.get("cards", []))
    for step in steps:
        if step["id"] not in chosen:
            continue
        body = step["body"]
        if step["id"] == "revision":
            rev = revision(body["revision"], {clause["id"]: clause["text"] for document in body["documents"] for clause in document["clauses"]})
            cards = [{**row, "closed": True} if row["revision"] == body["revision"] - 1 else row for row in cards]
        elif step["id"] == "cards":
            cards += [{"card": card, "revision": body["rulebook"]["revision"], "closed": False, "answers": []} for card in body["cards"]]
        elif step["id"] == "clarified":
            rev = {**rev, "clarified": True}
        else:
            rev = {**rev, "documents": [{**d, "clauses": [{**c, "reading": body["readings"].get(c["id"], c["reading"])} for c in d["clauses"]]} for d in rev["documents"]]}
    return {**found, "revision": rev, "cards": cards}


def answer(found, key, *answers):
    rows = [{**row, "answers": [{"answerer": name, "choice": choice, "text": None} for name, choice in answers]} if row["card"]["key"] == key else row for row in found["cards"]]
    return {**found, "cards": rows}


def drafted():
    nothing = {"roles": OPERATOR, "cards": []}
    return act(c.new_state("uaes", "v9.0"), nothing, "CLAUSES_REQUIRED", "submit_clauses", {"documents": DRAFT}), nothing


def plan_of(state, found):
    view = c.project(TASK, state, found)
    assert view["phase"] == "ready_to_confirm" and view["next"]["kind"] == "user_action", view["phase"]
    return c.plan(state, found)


def test_a_version_with_nothing_to_ask_is_done_in_one_confirmation():
    state, found = drafted()
    # Reviews are prepared on the draft itself: the wording that becomes revision 0.
    view = c.project(TASK, state, found)
    assert view["phase"] == "reviewing" and view["result"]["next_revision"] == {"version": "v9.0", "revision": 0}
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", review(), "A:1")
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", {**review(), "requirements": [{"quote": "须盖章", "role": "necessary", "scope": "盖章", "meaning": "要盖章。"}]}, "A:2")
    # Nothing to ask, so the revision will be clarified at once: its readings are prepared before anyone is asked.
    assert c.project(TASK, state, found)["phase"] == "reading"
    state = act(state, found, "CLAUSE_READING", "submit_reading", SIGNED, "A:1")
    state = act(state, found, "CLAUSE_READING", "submit_reading", STAMPED, "A:2")
    steps = plan_of(state, found)
    assert [(s["id"], s["external"], s["depends_on"]) for s in steps] == [("revision", False, []), ("clarified", False, ["revision"]), ("readings", False, ["clarified"])]
    done = take(found, steps)
    assert c.project(TASK, state, done)["phase"] == "completed"


def test_one_round_of_questions_takes_two_confirmations():
    state, found = drafted()
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", review([AMBIGUITY]), "A:1")
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", {**review(), "requirements": [{"quote": "须盖章", "role": "necessary", "scope": "盖章", "meaning": "要盖章。"}]}, "A:2")
    # First confirmation: revision 0 and the question to the customer, which is marked as reaching the customer.
    first = plan_of(state, found)
    assert [(s["id"], s["external"], s["depends_on"]) for s in first] == [("revision", False, []), ("cards", True, ["revision"])]
    (card,) = first[1]["body"]["cards"]
    assert card["key"] == "A:1#1" and [o["label"] for o in card["options"]] == ["理解一：电子签名也算", "理解二：仅手写算"]
    # One generator: the card is exactly the one the offline questionnaire build makes for the same finding.
    questionnaire = c._script("questionnaire")
    (finding,) = state["reviews"]["A:1"]["findings"]
    assert card == questionnaire.clause_card(finding, questionnaire.clause_name("A 文件", "A:1"), "须已签名", 0)
    found = take(found, first)
    assert c.project(TASK, state, found)["phase"] == "awaiting_answers"

    # Two people answer differently: the task says so and leaves the choice to a person.
    split = answer(found, "A:1#1", ("张三", "A"), ("李四", "B"))
    assert "答复不一致" in next(i for i in c.issues(TASK, state, split) if i["code"] == "CLAUSE_ANSWERED")["message"]
    found = answer(found, "A:1#1", ("张三", "A"), ("李四", "A"))
    with pytest.raises(c.Invalid):
        act(state, found, "CLAUSE_ANSWERED", "propose_text", {"text": "须已签名", "kind": "changed", "note": "x", "cards": ["A:1#1"]}, "A:1")
    state = act(state, found, "CLAUSE_ANSWERED", "propose_text", {"text": "须已签名，手写或电子均可", "kind": "changed", "note": "电子签名也算", "cards": ["A:1#1"]}, "A:1")

    # Only the reworded clause is reviewed again, on the wording revision 1 will have; then every reading is prepared.
    view = c.project(TASK, state, found)
    assert view["phase"] == "reviewing" and [i["subject"]["key"] for i in view["issues"]] == ["A:1"]
    assert view["result"]["next_revision"] == {"version": "v9.0", "revision": 1}
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", review(), "A:1")
    state = act(state, found, "CLAUSE_READING", "submit_reading", {**SIGNED, "passes": ["有签名，手写或电子均可"]}, "A:1")
    state = act(state, found, "CLAUSE_READING", "submit_reading", STAMPED, "A:2")
    second = plan_of(state, found)
    assert [s["id"] for s in second] == ["revision", "clarified", "readings"]
    body = second[0]["body"]
    assert body["changes"]["A:1"]["sources"] == [{"question_key": "A:1#1", "answerers": ["张三", "李四"]}]
    assert rulebooks.revision_problems(body["documents"], body["changes"], found["revision"]["documents"]) == []
    assert c.project(TASK, state, take(found, second))["phase"] == "completed"


def test_a_new_question_on_the_rewording_makes_the_second_confirmation_a_revision_and_new_cards():
    state, found = drafted()
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", review([AMBIGUITY]), "A:1")
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", {**review(), "requirements": [{"quote": "须盖章", "role": "necessary", "scope": "盖章", "meaning": "要盖章。"}]}, "A:2")
    found = answer(take(found, c.plan(state, found)), "A:1#1", ("张三", "A"))
    state = act(state, found, "CLAUSE_ANSWERED", "propose_text", {"text": "须已签名，手写或电子均可", "kind": "changed", "note": "电子签名也算", "cards": ["A:1#1"]}, "A:1")
    again = {**AMBIGUITY, "quotes": ["手写或电子均可"], "question": "盖章算不算？"}
    state = act(
        state,
        found,
        "CLAUSE_REVIEW",
        "submit_review",
        {**review([again]), "requirements": [{"quote": "须已签名，手写或电子均可", "role": "necessary", "scope": "签名", "meaning": "要有签名。"}]},
        "A:1",
    )
    second = plan_of(state, found)
    assert [(s["id"], s["external"]) for s in second] == [("revision", False), ("cards", True)]
    assert [card["key"] for card in second[1]["body"]["cards"]] == ["A:1#r1.1"]


def test_an_unchecked_step_comes_back_as_the_next_plan_with_what_was_prepared():
    state, found = drafted()
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", review(), "A:1")
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", {**review(), "requirements": [{"quote": "须盖章", "role": "necessary", "scope": "盖章", "meaning": "要盖章。"}]}, "A:2")
    state = act(state, found, "CLAUSE_READING", "submit_reading", SIGNED, "A:1")
    state = act(state, found, "CLAUSE_READING", "submit_reading", STAMPED, "A:2")
    # Only the first step checked: exactly what confirming one step at a time did.
    found = take(found, c.plan(state, found), ["revision"])
    rest = plan_of(state, found)
    assert [(s["id"], s["depends_on"]) for s in rest] == [("clarified", []), ("readings", ["clarified"])]
    assert rest[1]["body"]["readings"] == {"A:1": SIGNED, "A:2": STAMPED}
    # Readings on their own once the revision is clarified.
    assert [s["id"] for s in plan_of(state, take(found, rest, ["clarified"]))] == ["readings"]


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
    assert view["phase"] == "ready_to_confirm" and [step["id"] for step in c.plan(state, facts(r1))] == ["readings"]
    assert c.pending_readings(state, r1)["A:1"]["reading"] == reading
    done = c.project(TASK, state, facts(revision(1, {"A:1": text}, clarified=True, readings={"A:1": reading})))
    assert done["phase"] == "completed" and done["next"]["kind"] == "done"


def test_a_new_version_starts_from_submitted_clauses_and_only_an_operator_confirms():
    state, nothing = c.new_state("uaes", "v9.0"), {"roles": {"prepare": True, "confirm": False}, "cards": []}
    documents = [{"key": "A", "title": "A 文件", "clauses": [{"id": "A:1", "category": "", "text": "须已签名"}]}]
    with pytest.raises(c.Invalid):
        act(state, nothing, "CLAUSES_REQUIRED", "submit_clauses", {"documents": [{**documents[0], "clauses": [{"id": "B:1", "category": "", "text": "x"}]}]})
    state = act(state, nothing, "CLAUSES_REQUIRED", "submit_clauses", {"documents": documents})
    # The draft is reviewed before it is frozen, and can still be submitted again.
    (issue,) = c.issues(TASK, state, nothing)
    assert issue["code"] == "CLAUSE_REVIEW" and {a["id"] for a in issue["allowed_actions"]} == {"submit_review", "submit_clauses"}
    state = act(state, nothing, "CLAUSE_REVIEW", "submit_review", review(), "A:1")
    state = act(state, nothing, "CLAUSE_READING", "submit_reading", SIGNED, "A:1")
    # Only an operator confirms; a reviewer sees what is ready and that it is not theirs to confirm.
    view = c.project(TASK, state, nothing)
    assert view["phase"] == "ready_to_confirm" and view["next"]["kind"] == "blocked"
    plan_issue = next(i for i in c.issues(TASK, state, {**nothing, "roles": OPERATOR}) if i["code"] == "PLAN_READY")
    assert "confirm_plan" in {a["id"] for a in plan_issue["allowed_actions"]}
