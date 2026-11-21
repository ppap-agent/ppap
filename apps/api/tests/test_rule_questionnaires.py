"""Rule clarification questionnaire (#342): operators import, close and export; members of that enterprise answer.

Each member's answer is their own and keeps its history; another enterprise sees nothing; no model, rule, checker
or audit result is involved.
"""

import uuid
from copy import deepcopy

import psycopg
import pytest
from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings

FILE = {
    "schema": "rule-questionnaire-v1",
    "customer_id": "uaes",
    "rule_version": "规范性条款 v1.0",
    "title": "UAES 审核规则库 规范性条款 v1.0",
    "questions": [
        {
            "key": "common:模板",
            "group": "common",
            "clauses": ["试装单 规则 1", "零件提交保证书（PSW）规则 1"],
            "question": "“模板”是这份文件自己的版式，还是客户发放的模板？",
            "why": "供应商删掉了客户模板里的某一栏时，两种读法结论不同。",
            "options": [{"key": "A", "label": "这份文件自己的版式"}, {"key": "B", "label": "客户发放的模板"}],
            "expect": "选一个，或写出你的说法。",
            "related": [{"clause": "试装单 规则 1", "question": "“模板”指什么？", "example": "试装单只设了两栏。"}],
            "items": ["TA:1#2", "PSW:1#1"],
        },
        {
            "key": "PKG:1#1",
            "group": "clause",
            "clauses": ["包装方案 规则 1"],
            "question": "签署栏没盖章时，通过还是不通过？",
            "items": ["PKG:1#1"],
            "relayed": {"answer": "只写了「须……」的条款，全部满足即通过。", "applied": "签署也是通过的前提，缺章不通过", "source": "经产品负责人转达", "date": "2026-11-20"},
        },
        {"key": "CP:8#8.1", "group": "clause", "clauses": ["控制计划（CP）规则 8"], "question": "控制计划里怎样才算做了“分析”？", "expect": "给出认定标准。", "items": ["CP:8#8.1"]},
    ],
}


def fixture(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="rule-questionnaires", usage_charge_enabled=False, platform_operators="uaes-t:ou_operator,other-t:ou_other_operator"))
    people = {
        "operator": store.upsert_feishu_identity("uaes-t", "ou_operator", "运营"),
        "zhang": store.upsert_feishu_identity("uaes-t", "ou_zhang", "张三"),
        "li": store.upsert_feishu_identity("uaes-t", "ou_li", "李四"),
        "other": store.upsert_feishu_identity("other-t", "ou_wang", "王五"),
        "other_operator": store.upsert_feishu_identity("other-t", "ou_other_operator", "别家运营"),
    }
    api_store = build_product_store(store.settings, objects=store.objects, role="api")

    def client(name):
        session = TestClient(create_app(store.settings, auth_store=api_store))
        session.cookies.set(SESSION_COOKIE, store.create_session(people[name]))
        return session

    return store, client


def imported(client, operation_id=None, questionnaire=None):
    return client.post("/rule-questionnaires", json={"operation_id": operation_id or str(uuid.uuid4()), "questionnaire": questionnaire or FILE})


def answer(client, qid, key, *, expected=0, choice=None, text=None, note=None, operation_id=None):
    return client.post(
        f"/rule-questionnaires/{qid}/answers",
        json={"operation_id": operation_id or str(uuid.uuid4()), "question_key": key, "expected_revision": expected, "choice": choice, "text": text, "note": note},
    )


def counts(store) -> tuple:
    with store._conn() as conn:
        return tuple(
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("engine.jobs", "product.usage_events", "product.rule_versions", "product.suite_revisions", "product.suite_publications", "product.findings", "product.audit_rounds")
        )


def test_operator_imports_members_answer_each_their_own_and_operator_exports(auth_store_factory):
    store, client = fixture(auth_store_factory)
    operator, zhang, li, other, other_operator = (client(name) for name in ("operator", "zhang", "li", "other", "other_operator"))
    before = counts(store)

    # Only a platform operator imports; a replay of the same import changes nothing, a different one is refused.
    refused = imported(zhang)
    assert (refused.status_code, refused.json()) == (403, {"error": "platform_operator_required"})
    operation = str(uuid.uuid4())
    first = imported(operator, operation)
    assert first.status_code == 201, first.text
    qid = first.json()["id"]
    assert (first.json()["total"], first.json()["answered"], first.json()["customer_name"]) == (len(FILE["questions"]), 0, "UAES")
    assert imported(operator, operation).json() == first.json()
    changed = deepcopy(FILE)
    changed["questions"][0]["question"] = "改过的问题？"
    assert (imported(operator, operation, changed).status_code, imported(operator, operation, changed).json()) == (409, {"error": "idempotency_conflict"})
    assert imported(operator).json() == {"error": "questionnaire_exists"}

    # Members read the cards as imported, in order, with the relayed answer and its source.
    listed = zhang.get("/rule-questionnaires").json()
    assert listed["can_manage"] is False and [row["id"] for row in listed["questionnaires"]] == [qid]
    assert operator.get("/rule-questionnaires").json()["can_manage"] is True
    view = zhang.get(f"/rule-questionnaires/{qid}").json()
    assert [card["key"] for card in view["questions"]] == [card["key"] for card in FILE["questions"]]
    assert view["questions"][1]["relayed"]["source"] == "经产品负责人转达" and view["questions"][1]["relayed"]["date"] == "2026-11-20"

    # Two members answer the same card: both answers are kept, each with its answerer.
    first_answer = answer(zhang, qid, "common:模板", choice="A", note="按我们的模板")
    assert first_answer.status_code == 200, first_answer.text
    assert (first_answer.json()["revision"], first_answer.json()["answerer"]) == (1, "张三")
    assert answer(li, qid, "common:模板", text="看文件用的是哪版模板").status_code == 200
    card = zhang.get(f"/rule-questionnaires/{qid}").json()["questions"][0]
    assert [(row["answerer"], row["mine"], row["choice"], row["text"]) for row in card["answers"]] == [("张三", True, "A", None), ("李四", False, None, "看文件用的是哪版模板")]

    # A member confirms the relayed answer, or answers differently; a choice the card does not offer is refused.
    assert answer(li, qid, "PKG:1#1", choice="relayed").status_code == 200
    assert answer(li, qid, "CP:8#8.1", choice="relayed").json() == {"error": "invalid_answer_request"}
    assert answer(li, qid, "common:模板", expected=1, choice="Z").json() == {"error": "invalid_answer_request"}
    assert answer(li, qid, "CP:8#8.1", text="   ").json() == {"error": "answer_required"}

    # Changing one's own answer keeps the earlier one; a stale change is refused, a replay returns the same receipt.
    change = str(uuid.uuid4())
    changed_answer = answer(zhang, qid, "common:模板", expected=1, choice="B", text="改为客户模板", operation_id=change)
    assert changed_answer.json()["revision"] == 2
    assert answer(zhang, qid, "common:模板", expected=1, choice="B", text="改为客户模板", operation_id=change).json() == changed_answer.json()
    assert answer(zhang, qid, "common:模板", expected=1, choice="A", operation_id=change).json() == {"error": "idempotency_conflict"}
    stale = answer(zhang, qid, "common:模板", expected=1, choice="A")
    assert (stale.status_code, stale.json()) == (409, {"error": "answer_revision_conflict"})
    assert zhang.get(f"/rule-questionnaires/{qid}/answers/{change}").json() == changed_answer.json()

    # After signing in again the answers and their history are still there; the summary counts answered cards.
    again = client("zhang")
    mine = next(row for row in again.get(f"/rule-questionnaires/{qid}").json()["questions"][0]["answers"] if row["mine"])
    assert (mine["revision"], mine["choice"], mine["text"], mine["note"]) == (2, "B", "改为客户模板", None)
    assert [(old["revision"], old["choice"], old["note"]) for old in mine["history"]] == [(1, "A", "按我们的模板")]
    summary = again.get("/rule-questionnaires").json()["questionnaires"][0]
    assert (summary["total"], summary["answered"], summary["mine"]) == (3, 2, 1)

    # Another enterprise, its operator included, sees nothing and cannot write.
    assert other.get("/rule-questionnaires").json()["questionnaires"] == []
    for response in (
        other.get(f"/rule-questionnaires/{qid}"),
        answer(other, qid, "common:模板", choice="A"),
        other.get(f"/rule-questionnaires/{qid}/answers/{change}"),
        other_operator.get(f"/rule-questionnaires/{qid}/export"),
        other_operator.post(f"/rule-questionnaires/{qid}/close"),
    ):
        assert (response.status_code, response.json()) == (403, {"error": "access_denied"})

    # Members neither export nor close.
    for response in (zhang.get(f"/rule-questionnaires/{qid}/export"), zhang.post(f"/rule-questionnaires/{qid}/close")):
        assert (response.status_code, response.json()) == (403, {"error": "platform_operator_required"})

    # The export has every answer with its current value and history, by answerer name, with the findings each card answers.
    exported = operator.get(f"/rule-questionnaires/{qid}/export")
    assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
    body = exported.json()
    assert (body["schema"], body["customer_id"], body["rule_version"]) == ("rule-questionnaire-answers-v1", "uaes", "规范性条款 v1.0")
    template = body["questions"][0]
    assert template["items"] == ["TA:1#2", "PSW:1#1"] and template["clauses"] == ["试装单 规则 1", "零件提交保证书（PSW）规则 1"]
    zhang_row = next(row for row in template["answers"] if row["answerer"] == "张三")
    assert (zhang_row["choice"], zhang_row["choice_label"], zhang_row["text"], zhang_row["revision"]) == ("B", "客户发放的模板", "改为客户模板", 2)
    assert [(old["choice_label"], old["note"]) for old in zhang_row["history"]] == [("这份文件自己的版式", "按我们的模板")]
    assert body["questions"][1]["answers"][0]["choice_label"] == "确认按共性答复处理"

    # Closing makes it read only for everyone; closing again keeps the first closing.
    closed = operator.post(f"/rule-questionnaires/{qid}/close")
    assert closed.status_code == 200 and closed.json()["closed"] is True
    assert operator.post(f"/rule-questionnaires/{qid}/close").json()["closed_at"] == closed.json()["closed_at"]
    late = answer(li, qid, "CP:8#8.1", text="出现并有控制方法")
    assert (late.status_code, late.json()) == (409, {"error": "questionnaire_closed"})
    assert zhang.get(f"/rule-questionnaires/{qid}").json()["closed"] is True
    assert operator.get(f"/rule-questionnaires/{qid}/export").json()["closed_at"] == closed.json()["closed_at"]

    # Nothing ran: no job, no usage, no rule, checker or audit result appeared or changed.
    assert counts(store) == before


def test_import_refuses_unknown_customers_and_malformed_files_and_cards_stay_frozen(auth_store_factory):
    store, client = fixture(auth_store_factory)
    operator = client("operator")
    unknown = dict(FILE, customer_id="nobody")
    assert imported(operator, questionnaire=unknown).json() == {"error": "unknown_customer"}
    duplicate = dict(FILE, questions=[FILE["questions"][0], FILE["questions"][0]])
    assert imported(operator, questionnaire=duplicate).json() == {"error": "invalid_questionnaire_file"}
    assert imported(operator, questionnaire=dict(FILE, questions=[dict(FILE["questions"][0], group="other")])).json() == {"error": "invalid_questionnaire_file"}
    assert operator.post("/rule-questionnaires", json={"questionnaire": FILE}).json() == {"error": "invalid_questionnaire_request"}
    assert imported(operator).status_code == 201
    # The runtime role cannot change or remove an imported card, or rewrite an answer.
    api_store = build_product_store(store.settings, objects=store.objects, role="api")
    for statement in (
        "UPDATE product.rule_questions SET card='{}'::jsonb",
        "DELETE FROM product.rule_questions",
        "UPDATE product.rule_question_answers SET note='x'",
        "UPDATE product.rule_questionnaires SET title='x'",
    ):
        with api_store._conn() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)
