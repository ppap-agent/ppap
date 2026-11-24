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
    waiting = c.project(TASK, state, found)
    assert waiting["phase"] == "awaiting_answers" and "飞书通知" in waiting["next"]["message"]

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


def test_a_common_card_answers_each_clause_it_asks_about_and_a_general_card_decides_none():
    """A questionnaire loaded offline asks some findings once, on a common card, and some questions on no clause."""
    state, r0 = c.new_state("uaes", "v9.0"), revision(0, {"A:1": "须已签名", "A:2": "须盖章"})
    stamp = {**AMBIGUITY, "quotes": ["须盖章"], "question": "一枚章够吗？"}
    state = act(state, facts(r0), "CLAUSE_REVIEW", "submit_review", review([AMBIGUITY]), "A:1")
    state = act(state, facts(r0), "CLAUSE_REVIEW", "submit_review", {**review([stamp]), "requirements": [{"quote": "须盖章", "role": "necessary", "scope": "盖章", "meaning": "要盖章。"}]}, "A:2")
    common = {"card": {"key": "common:签章", "group": "common", "items": ["A:1#1", "A:2#1"], "question": "q", "clauses": ["A 文件 A:1", "A 文件 A:2"]}, "revision": 0, "closed": False}
    general = {"card": {"key": "general:1", "group": "general", "question": "附录与正文按哪处？"}, "revision": 0, "closed": False, "answers": [{"answerer": "张三", "choice": "A", "text": None}]}
    # Asked but not answered: both findings wait on the common card; nothing is sent again.
    waiting = c._standing(state, facts(r0, [{**common, "answers": []}, general]), r0)
    assert (waiting["unsent"], [card["key"] for card in waiting["waiting"]]) == ([], ["A:1#1", "A:2#1"])
    found = facts(r0, [{**common, "answers": [{"answerer": "张三", "choice": "A", "text": None}]}, general])
    assert sorted(i["subject"]["key"] for i in c.issues(TASK, state, found) if i["code"] == "CLAUSE_ANSWERED") == ["A:1", "A:2"]
    issue = next(i for i in c.issues(TASK, state, found) if i["subject"]["key"] == "A:2")
    assert [card["key"] for card in c.issue_detail(issue, state, found)["detail"]["cards"]] == ["common:签章"]
    # Each clause deals with the answer on its own; the change names the card that was answered.
    state = act(state, found, "CLAUSE_ANSWERED", "propose_text", {"text": "须已签名，手写或电子均可", "kind": "changed", "note": "电子签名也算", "cards": ["A:1#1"]}, "A:1")
    assert state["proposals"]["A:1"]["sources"] == [{"question_key": "common:签章", "answerers": ["张三"]}]
    assert [i["subject"]["key"] for i in c.issues(TASK, state, found) if i["code"] == "CLAUSE_ANSWERED"] == ["A:2"]
    state = act(state, found, "CLAUSE_ANSWERED", "keep_text", {"reason": "一枚章即可，原文已允许。"}, "A:2")
    assert not [i for i in c.issues(TASK, state, found) if i["code"] == "CLAUSE_ANSWERED"]


def test_a_questionnaire_answered_before_every_clause_was_reviewed_keeps_its_keys():
    """The rest of the revision is reviewed alongside the answers, so its findings meet the cards already answered."""
    state, r0 = c.new_state("uaes", "v9.0"), revision(0, {"A:1": "须已签名", "A:2": "须盖章"})
    stamp = {**AMBIGUITY, "quotes": ["须盖章"], "question": "一枚章够吗？"}
    found = facts(r0, [answered("A:1#1", 0, ("张三", "A")), {**answered("A:2#1", 0, ("张三", "B")), "card": {"key": "A:2#1", "items": ["A:2#1"], "question": "q", "clauses": ["A 文件 A:2"]}}])
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", review([AMBIGUITY]), "A:1")
    assert {(i["code"], i["subject"]["key"]) for i in c.issues(TASK, state, found)} == {("CLAUSE_ANSWERED", "A:1"), ("CLAUSE_REVIEW", "A:2")}
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", {**review([stamp]), "requirements": [{"quote": "须盖章", "role": "necessary", "scope": "盖章", "meaning": "要盖章。"}]}, "A:2")
    assert state["reviews"]["A:2"]["revision"] == 0
    assert [card["key"] for card in c._standing(state, found, r0)["answered"]] == ["A:1#1", "A:2#1"]
    assert {i["code"] for i in c.issues(TASK, state, found)} == {"CLAUSE_ANSWERED"}


def test_a_clause_is_reworded_on_a_platform_ruling_and_reviewed_before_it_is_frozen():
    """A clause that leans on a confirmed platform ruling takes it into its own words; the ruling is the ground shown."""
    state, r0 = c.new_state("uaes", "v9.0"), revision(0, {"A:1": "须已签名", "A:2": "须盖章"})
    stamp = {"quote": "须盖章", "role": "necessary", "scope": "盖章", "meaning": "要盖章。"}
    found = facts(r0, [answered("A:1#1", 0)])  # a card on A:1 still waits for its answer
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", review([AMBIGUITY]), "A:1")
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", {**review(), "requirements": [stamp]}, "A:2")
    ruling = "签署区有一处有效印章即可，不要求每栏都盖。"
    # Nothing to ask: only the reword is offered beside the confirmation, and it needs a ground.
    with pytest.raises(c.Invalid):
        act(state, found, "CLAUSE_REWORD", "propose_text", {"clause": "A:2", "text": "须盖章，一处有效印章即可", "kind": "same", "note": "写明印章口径"})
    state = act(state, found, "CLAUSE_REWORD", "propose_text", {"clause": "A:2", "text": "须盖章，签署区有一处有效印章即可", "kind": "same", "note": "写明印章口径", "rulings": [ruling]})
    assert state["proposals"]["A:2"]["sources"] == [{"ruling": ruling}]
    # The new wording is reviewed before revision 1 freezes; the reword waits until it is.
    view = c.project(TASK, state, found)
    assert view["phase"] == "reviewing" and [i["subject"]["key"] for i in view["issues"]] == ["A:2"]
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", {**review(), "requirements": [{**stamp, "quote": "须盖章，签署区有一处有效印章即可"}]}, "A:2")
    body = plan_of(state, found)[0]["body"]
    assert body["changes"]["A:2"] == {"kind": "same", "note": "写明印章口径", "sources": [{"ruling": ruling}]}
    assert rulebooks.revision_problems(body["documents"], body["changes"], r0["documents"]) == []


def test_a_finding_added_to_a_frozen_revision_becomes_a_card_the_next_confirmation_sends():
    state, found = drafted()
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", review([AMBIGUITY]), "A:1")
    state = act(state, found, "CLAUSE_REVIEW", "submit_review", {**review(), "requirements": [{"quote": "须盖章", "role": "necessary", "scope": "盖章", "meaning": "要盖章。"}]}, "A:2")
    found = take(found, plan_of(state, found))
    assert c.project(TASK, state, found)["phase"] == "awaiting_answers"
    gap = {
        "id": "G1",
        "kind": "gap",
        "quotes": ["须盖章"],
        "title": "章盖在哪里",
        "question": "章盖在附页上算不算盖章？",
        "example": "封面没有章，附页盖了公司章。",
        "challenge": "原文只写须盖章，没写盖在哪一页；例子中按封面判不通过，按附页判通过，原文没有给出结论。",
    }
    # Checked as part of the clause's review: quotes from the frozen text, an ID new to the review, a reason.
    with pytest.raises(c.Invalid):
        act(state, found, "CLAUSE_FINDING", "add_finding", {"clause": "A:2", "finding": {**gap, "quotes": ["须签字"]}, "reason": "试跑发现"})
    with pytest.raises(c.Invalid):
        act(state, found, "CLAUSE_FINDING", "add_finding", {"clause": "A:1", "finding": {**gap, "id": "1", "quotes": ["已签名"]}, "reason": "试跑发现"})
    state = act(state, found, "CLAUSE_FINDING", "add_finding", {"clause": "A:2", "finding": gap, "reason": "试跑发现"})
    # The review as submitted stays; the added finding is one more card, sent alone in the next confirmation.
    assert state["reviews"]["A:2"]["findings"] == []
    (step,) = plan_of(state, found)
    assert step["id"] == "cards" and step["external"] and [card["key"] for card in step["body"]["cards"]] == ["A:2#G1"]
    assert c.project(TASK, state, take(found, [step]))["result"]["progress"]["cards"]["waiting"] == 2
