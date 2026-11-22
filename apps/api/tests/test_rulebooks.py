"""Members read the rulebook revisions the product ships; without a session nothing is read."""

import json
import uuid

from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings


def test_members_read_rulebook_revisions(auth_store_factory, tmp_path):
    rules = tmp_path / "acme" / "rulebook" / "v1.0" / "m0" / "rules"
    rules.mkdir(parents=True)
    (rules / "A.json").write_text(json.dumps({"title": "A 文件", "rules": [{"id": "A:1", "rule_to_check": "签字"}, {"id": "A:2", "rule_to_check": "盖章"}]}), encoding="utf-8")
    store = auth_store_factory(Settings(feishu_app_id="rulebooks", usage_charge_enabled=False, template_root=str(tmp_path)))
    member = store.upsert_feishu_identity("acme-t", "ou_reader", "读者")
    client = TestClient(create_app(store.settings, auth_store=build_product_store(store.settings, objects=store.objects, role="api")))
    assert client.get("/rulebooks/acme").status_code == 401
    client.cookies.set(SESSION_COOKIE, store.create_session(member))
    assert [(row["version"], row["revision"], row["clause_count"]) for row in client.get("/rulebooks/acme").json()["revisions"]] == [("v1.0", 0, 2)]
    view = client.get("/rulebooks/acme/v1.0/0").json()
    assert [clause["id"] for document in view["documents"] for clause in document["clauses"]] == ["A:1", "A:2"]
    assert client.get("/rulebooks/other").json() == {"revisions": []}
    assert client.get("/rulebooks/acme/v1.0/9").status_code == 404


def test_operator_writes_the_next_revision_once_with_its_grounds(auth_store_factory, tmp_path):
    rules = tmp_path / "uaes" / "rulebook" / "v9.0" / "m0" / "rules"
    rules.mkdir(parents=True)
    (rules / "A.json").write_text(json.dumps({"title": "A 文件", "rules": [{"id": "A:1", "rule_to_check": "须“已签名”"}, {"id": "A:2", "rule_to_check": "盖章"}]}), encoding="utf-8")
    store = auth_store_factory(Settings(feishu_app_id="rulebooks-w", usage_charge_enabled=False, template_root=str(tmp_path), platform_operators="acme-t:ou_owner"))
    api = build_product_store(store.settings, objects=store.objects, role="api")
    owner, member = (TestClient(create_app(store.settings, auth_store=api)) for _ in range(2))
    owner.cookies.set(SESSION_COOKIE, store.create_session(store.upsert_feishu_identity("acme-t", "ou_owner", "负责人")))
    member.cookies.set(SESSION_COOKIE, store.create_session(store.upsert_feishu_identity("acme-t", "ou_reader", "读者")))

    def body(text, changes):
        documents = [{"key": "A", "title": "A 文件", "clauses": [{"id": "A:1", "category": "", "text": text}, {"id": "A:2", "category": "", "text": "盖章"}]}]
        return {"version": "v9.0", "revision": 1, "base": {"version": "v9.0", "revision": 0}, "documents": documents, "changes": changes, "origin": "clarification"}

    grounds = {"A:1": {"kind": "changed", "note": "电子签名也算已签名", "sources": [{"question_key": "A:1#1", "answerers": ["张三"]}]}}
    assert member.post("/rulebooks/uaes/revisions", json=body("须“已签名”，手写或电子均可", grounds)).status_code == 403
    # A changed clause without grounds is not a revision; neither is a renamed clause.
    assert owner.post("/rulebooks/uaes/revisions", json=body("须“已签名”，手写或电子均可", {})).json()["error"] == "invalid_rulebook_revision"
    created = owner.post("/rulebooks/uaes/revisions", json=body("须“已签名”，手写或电子均可", grounds))
    assert created.status_code == 200 and created.json()["changes"] == {"same": 0, "changed": 1, "new": 0}
    assert owner.post("/rulebooks/uaes/revisions", json=body("须“已签名”，手写或电子均可", grounds)).json()["id"] == created.json()["id"]
    assert owner.post("/rulebooks/uaes/revisions", json=body("须“已签名”，仅手写", grounds)).status_code == 409
    # The earlier revision is untouched, and no revision is a rule an audit could be bound to.
    assert member.get("/rulebooks/uaes/v9.0/0").json()["documents"][0]["clauses"][0]["text"] == "须“已签名”"
    assert [row["revision"] for row in member.get("/rulebooks/uaes").json()["revisions"]] == [0, 1]
    assert not [rule for rule in member.get("/rules").json()["rules"] if rule["title"] == "A 文件"]

    reading = {"checks": ["签名栏"], "passes": ["有“已签名”，手写或电子均可"], "fails": ["没有签名"], "manual": []}
    assert owner.put("/rulebooks/uaes/v9.0/1/readings", json={"readings": {"A:1": reading}}).json()["error"] == "rulebook_revision_not_clarified"
    assert owner.post("/rulebooks/uaes/v9.0/1/clarified").json()["clarified"] is True
    lost = owner.put("/rulebooks/uaes/v9.0/1/readings", json={"readings": {"A:1": {**reading, "passes": ["有签名"]}}})
    assert lost.json()["error"] == "invalid_clause_reading"
    saved = owner.put("/rulebooks/uaes/v9.0/1/readings", json={"readings": {"A:1": reading}}).json()
    assert saved["documents"][0]["clauses"][0]["reading"] == reading


def test_operator_sends_cards_and_the_next_revision_closes_the_questionnaire(auth_store_factory, tmp_path):
    rules = tmp_path / "uaes" / "rulebook" / "v9.0" / "m0" / "rules"
    rules.mkdir(parents=True)
    (rules / "A.json").write_text(json.dumps({"title": "A 文件", "rules": [{"id": "A:1", "rule_to_check": "须已签名"}]}), encoding="utf-8")
    store = auth_store_factory(Settings(feishu_app_id="rulebooks-q", usage_charge_enabled=False, template_root=str(tmp_path), platform_operators="acme-t:ou_owner"))
    api = build_product_store(store.settings, objects=store.objects, role="api")
    owner, member = (TestClient(create_app(store.settings, auth_store=api)) for _ in range(2))
    owner.cookies.set(SESSION_COOKIE, store.create_session(store.upsert_feishu_identity("acme-t", "ou_owner", "负责人")))
    member.cookies.set(SESSION_COOKIE, store.create_session(store.upsert_feishu_identity("acme-t", "ou_reader", "读者")))
    owner.get("/rulebooks/uaes")

    def card(key, question="电子签名算已签名吗？", **more):
        return {"key": key, "group": "clause", "clauses": ["A 文件 规则 1"], "question": question, "options": [{"key": "yes", "label": "算"}, {"key": "no", "label": "不算"}], "items": [key], **more}

    def send(cards):
        return {"customer_id": "uaes", "rulebook": {"version": "v9.0", "revision": 0}, "cards": cards}

    assert member.post("/rule-questionnaires/cards", json=send([card("A:1#1")])).status_code == 403
    first = owner.post("/rule-questionnaires/cards", json=send([card("A:1#1")])).json()
    assert (first["total"], first["added"]) == (1, 1)
    # More cards join the same questionnaire; a card already there stays as written.
    more = owner.post("/rule-questionnaires/cards", json=send([card("A:1#1"), card("A:1#2", "只答了一半：盖章算吗？", follows="A:1#1")])).json()
    assert (more["id"], more["total"], more["added"]) == (first["id"], 2, 1)
    assert owner.post("/rule-questionnaires/cards", json=send([card("A:1#1", "换一个问题")])).status_code == 409
    assert [q.get("follows") for q in member.get(f"/rule-questionnaires/{first['id']}").json()["questions"]] == [None, "A:1#1"]
    # Nobody had answered, so nobody was told; once someone answers, they hear about the next cards, once.
    assert (first["notified"], more["notified"]) == (0, 0)
    answer = {"operation_id": str(uuid.uuid4()), "question_key": "A:1#1", "expected_revision": 0, "choice": "yes", "text": None, "note": None}
    assert member.post(f"/rule-questionnaires/{first['id']}/answers", json=answer).status_code == 200
    third = owner.post("/rule-questionnaires/cards", json=send([card("A:1#3", "盖章算吗？")])).json()
    assert (third["added"], third["notified"]) == (1, 1)
    assert owner.post("/rule-questionnaires/cards", json=send([card("A:1#3", "盖章算吗？")])).json()["notified"] == 0
    with api._conn() as conn:
        jobs = conn.execute("SELECT payload FROM engine.jobs WHERE kind='rule_cards_notification'").fetchall()
    reader = store.upsert_feishu_identity("acme-t", "ou_reader", "读者")
    assert [(job[0]["user_id"], job[0]["added"]) for job in jobs] == [(reader.user_id, 1)]

    documents = [{"key": "A", "title": "A 文件", "clauses": [{"id": "A:1", "category": "", "text": "须已签名，手写或电子均可"}]}]
    changes = {"A:1": {"kind": "changed", "note": "电子签名也算", "sources": [{"question_key": "A:1#1", "answerers": ["读者"]}]}}
    revision = {"version": "v9.0", "revision": 1, "base": {"version": "v9.0", "revision": 0}, "documents": documents, "changes": changes, "origin": "clarification"}
    assert owner.post("/rulebooks/uaes/revisions", json=revision).status_code == 200
    assert member.get(f"/rule-questionnaires/{first['id']}").json()["closed"] is True
    assert owner.post("/rule-questionnaires/cards", json=send([card("A:1#4")])).json()["error"] == "questionnaire_closed"


def test_the_new_questions_card_opens_the_questionnaire():
    from urllib.parse import parse_qs, urlsplit

    from product.feishu_notifications import rule_cards_card

    card = rule_cards_card("联电科技/UAES", "uaes", {"version": "v1.0", "revision": 1}, 3, "https://ppap.example", "q-1")
    assert card["header"]["subtitle"]["content"] == "联电科技/UAES 规则库 v1.0 修订 1" and "新增 **3 个**问题" in card["body"]["elements"][0]["content"]
    link = card["body"]["elements"][1]["behaviors"][0]["default_url"]
    assert parse_qs(urlsplit(link).query)["return_to"] == ["https://ppap.example/requirements?template=uaes&ruleQuestions=q-1"]
