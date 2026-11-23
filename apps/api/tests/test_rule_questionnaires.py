"""Rule clarification questionnaire (#342): loaded and closed offline in the database; members of that enterprise answer.

Each member's answer is their own and keeps its history; another enterprise sees nothing; the product cannot change a
questionnaire or its cards, and no model, rule, checker or audit result is involved.
"""

import json
import uuid
from html import unescape
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import pytest
from api import create_app
from api.notifications import dispatch_answer_notification
from fastapi.testclient import TestClient
from product import build_product_store, clarification
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.store.workflows import WorkflowStore

CARDS = [
    {
        "key": "common:模板",
        "group": "common",
        "clauses": ["试装单 规则 1", "零件提交保证书（PSW）规则 1"],
        "question": "“模板”是这份文件自己的版式，还是客户发放的模板？",
        "why": "供应商删掉了客户模板里的某一栏时，两种读法结论不同。",
        "options": [{"key": "A", "label": "这份文件自己的版式", "detail": ""}, {"key": "B", "label": "客户发放的模板", "detail": ""}],
        "expect": "选一个，或写出你的说法。",
        "related": [{"clause": "试装单 规则 1", "question": "“模板”指什么？", "example": "试装单只设了两栏。"}],
        "items": ["TA:1#2", "PSW:1#1"],
    },
    {
        "key": "PKG:1#1",
        "group": "clause",
        "clauses": ["包装方案 规则 1"],
        "question": "签署栏没盖章时，通过还是不通过？",
        "sources": [{"clause": "包装方案 规则 1", "text": "以上七项全部满足才通过，否则不通过。签字与印章任一缺失，不通过。", "quotes": ["签字与印章任一缺失，不通过"]}],
        "items": ["PKG:1#1"],
        "relayed": {"answer": "只写了「须……」的条款，全部满足即通过。", "applied": "签署也是通过的前提，缺章不通过", "source": "经产品负责人转达", "date": "2026-11-20"},
    },
    {"key": "CP:8#8.1", "group": "clause", "clauses": ["控制计划（CP）规则 8"], "question": "控制计划里怎样才算做了“分析”？", "expect": "给出认定标准。", "items": ["CP:8#8.1"]},
]


def fixture(auth_store_factory, **settings):
    store = auth_store_factory(Settings(feishu_app_id="rule-questionnaires", usage_charge_enabled=False, **settings))
    people = {
        "zhang": store.upsert_feishu_identity("uaes-t", "ou_zhang", "张三"),
        "li": store.upsert_feishu_identity("uaes-t", "ou_li", "李四"),
        "other": store.upsert_feishu_identity("other-t", "ou_wang", "王五"),
        "owner": store.upsert_feishu_identity("uaes-t", "ou_owner", "负责人"),
        "reviewer": store.upsert_feishu_identity("uaes-t", "ou_reviewer", "审查人"),
    }
    api_store = build_product_store(store.settings, objects=store.objects, role="api")

    def client(name):
        session = TestClient(create_app(store.settings, auth_store=api_store))
        session.cookies.set(SESSION_COOKIE, store.create_session(people[name]))
        return session

    return store, api_store, people, client


def load(store, tenant_id) -> str:
    """What the offline load does: one questionnaire and its cards, written by the migration role."""
    questionnaire_id = str(uuid.uuid4())
    with store._conn() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO product.rule_questionnaires(id,tenant_id,customer_id,rule_version,title,rulebook_version,rulebook_revision)
               VALUES(%s,%s,'uaes','规范性条款 v1.0','UAES 审核规则库 规范性条款 v1.0','v1.0',0)""",
            (questionnaire_id, tenant_id),
        )
        for position, card in enumerate(CARDS):
            conn.execute("INSERT INTO product.rule_questions(questionnaire_id,question_key,position,card) VALUES(%s,%s,%s,%s::jsonb)", (questionnaire_id, card["key"], position, json.dumps(card)))
    return questionnaire_id


def close(store, questionnaire_id):
    with store._conn() as conn:
        conn.execute("UPDATE product.rule_questionnaires SET closed_at=now() WHERE id=%s AND closed_at IS NULL", (questionnaire_id,))


def answer(client, qid, key, *, expected=0, choice=None, text=None, note=None, operation_id=None):
    body = {"operation_id": operation_id or str(uuid.uuid4()), "question_key": key, "expected_revision": expected, "choice": choice, "text": text, "note": note}
    return client.post(f"/rule-questionnaires/{qid}/answers", json=body)


def counts(store) -> tuple:
    tables = ("engine.jobs", "product.usage_events", "product.rule_versions", "product.suite_revisions", "product.suite_publications", "product.findings", "product.audit_rounds")
    with store._conn() as conn:
        return tuple(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables)


def test_members_answer_each_their_own_answers_keep_history_and_other_enterprises_see_nothing(auth_store_factory):
    store, _, people, client = fixture(auth_store_factory)
    zhang, li, other = (client(name) for name in ("zhang", "li", "other"))
    before = counts(store)
    qid = load(store, people["zhang"].tenant_id)

    # Members read the cards as loaded, in order, with the relayed answer and its source.
    listed = zhang.get("/rule-questionnaires").json()["questionnaires"]
    assert [(row["id"], row["customer_name"], row["rulebook"], row["total"], row["answered"]) for row in listed] == [(qid, "联电科技/UAES", {"version": "v1.0", "revision": 0}, len(CARDS), 0)]
    view = zhang.get(f"/rule-questionnaires/{qid}").json()
    assert [card["key"] for card in view["questions"]] == [card["key"] for card in CARDS]
    assert view["questions"][1]["relayed"]["source"] == "经产品负责人转达" and view["questions"][1]["relayed"]["date"] == "2026-11-20"
    assert view["questions"][1]["sources"] == CARDS[1]["sources"]
    # A card loaded without the fields it does not have still reads with every field.
    bare = view["questions"][2]
    assert (bare["related"], bare["sources"], bare["options"], bare["relayed"], bare["why"]) == ([], [], [], None, "")

    # Two members answer the same card: both answers are kept, each with its answerer.
    first = answer(zhang, qid, "common:模板", choice="A", note="按我们的模板")
    assert first.status_code == 200, first.text
    assert (first.json()["revision"], first.json()["answerer"]) == (1, "张三")
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
    changed = answer(zhang, qid, "common:模板", expected=1, choice="B", text="改为客户模板", operation_id=change)
    assert changed.json()["revision"] == 2
    assert answer(zhang, qid, "common:模板", expected=1, choice="B", text="改为客户模板", operation_id=change).json() == changed.json()
    assert answer(zhang, qid, "common:模板", expected=1, choice="A", operation_id=change).json() == {"error": "idempotency_conflict"}
    stale = answer(zhang, qid, "common:模板", expected=1, choice="A")
    assert (stale.status_code, stale.json()) == (409, {"error": "answer_revision_conflict"})
    assert zhang.get(f"/rule-questionnaires/{qid}/answers/{change}").json() == changed.json()

    # After signing in again the answers and their history are still there; the summary counts answered cards.
    again = client("zhang")
    mine = next(row for row in again.get(f"/rule-questionnaires/{qid}").json()["questions"][0]["answers"] if row["mine"])
    assert (mine["revision"], mine["choice"], mine["text"], mine["note"]) == (2, "B", "改为客户模板", None)
    assert [(old["revision"], old["choice"], old["note"]) for old in mine["history"]] == [(1, "A", "按我们的模板")]
    summary = again.get("/rule-questionnaires").json()["questionnaires"][0]
    assert (summary["total"], summary["answered"], summary["mine"]) == (3, 2, 1)

    # Another enterprise sees nothing and cannot write.
    assert other.get("/rule-questionnaires").json()["questionnaires"] == []
    for response in (other.get(f"/rule-questionnaires/{qid}"), answer(other, qid, "common:模板", choice="A"), other.get(f"/rule-questionnaires/{qid}/answers/{change}")):
        assert (response.status_code, response.json()) == (403, {"error": "access_denied"})

    # Closed offline, it is read only for everyone; the answers stay readable.
    close(store, qid)
    late = answer(li, qid, "CP:8#8.1", text="出现并有控制方法")
    assert (late.status_code, late.json()) == (409, {"error": "questionnaire_closed"})
    closed = zhang.get(f"/rule-questionnaires/{qid}").json()
    assert closed["closed"] is True and len(closed["questions"][0]["answers"]) == 2

    # Nothing ran: no job, no usage, no rule, checker or audit result appeared or changed.
    assert counts(store) == before


def test_the_product_cannot_change_questionnaires_and_the_database_refuses_answers_once_closed(auth_store_factory):
    store, api_store, people, _ = fixture(auth_store_factory)
    qid = load(store, people["zhang"].tenant_id)
    # The runtime role adds cards and closes a questionnaire, but never changes a card, a questionnaire's identity or an answer.
    for statement in (
        "UPDATE product.rule_questionnaires SET title='x'",
        "DELETE FROM product.rule_questionnaires",
        "UPDATE product.rule_questions SET card='{}'::jsonb",
        "DELETE FROM product.rule_questions",
        "UPDATE product.rule_question_answers SET note='x'",
    ):
        with api_store._conn() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)

    def insert(tenant_id):
        with api_store._conn() as conn:
            conn.execute(
                """INSERT INTO product.rule_question_answers(operation_id,tenant_id,questionnaire_id,question_key,answered_by,revision,choice,request)
                   VALUES(%s,%s,%s,'common:模板',%s,1,'A','{}'::jsonb)""",
                (str(uuid.uuid4()), tenant_id, qid, people["zhang"].user_id),
            )

    # An answer recorded under another enterprise, or after closing, never lands.
    with pytest.raises(psycopg.errors.CheckViolation, match="questionnaire_closed"):
        insert(people["other"].tenant_id)
    close(store, qid)
    with pytest.raises(psycopg.errors.CheckViolation, match="questionnaire_closed"):
        insert(people["zhang"].tenant_id)


def test_each_rulebook_revision_carries_its_own_questionnaire(auth_store_factory):
    store, _, people, client = fixture(auth_store_factory)
    tenant = people["zhang"].tenant_id
    first = load(store, tenant)
    insert = "INSERT INTO product.rule_questionnaires(id,tenant_id,customer_id,rule_version,title,rulebook_version,rulebook_revision) VALUES(gen_random_uuid(),%s,'uaes',%s,'x',%s,%s)"
    with store._conn() as conn:
        conn.execute(insert, (tenant, "规范性条款 v1.0", "v1.0", 1))
    # The same revision twice, no revision, or a version the label does not name is refused.
    for label, version, revision in (("规范性条款 v1.0", "v1.0", 1), ("规范性条款 v1.0", None, None), ("规范性条款 v1.0", "v2.0", 0), ("规范性条款 v1.0", "1.0", 3)):
        with store._conn() as conn, pytest.raises((psycopg.errors.UniqueViolation, psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation)):
            conn.execute(insert, (tenant, label, version, revision))
    listed = client("zhang").get("/rule-questionnaires").json()["questionnaires"]
    assert sorted((row["rulebook"]["version"], row["rulebook"]["revision"]) for row in listed) == [("v1.0", 0), ("v1.0", 1)]
    assert first in {row["id"] for row in listed}


def notices(store) -> list[tuple[str, str, dict]]:
    with store._conn() as conn:
        rows = conn.execute("SELECT status, result->>'state', payload FROM engine.jobs WHERE kind='rule_answers_notification' ORDER BY created_at, id").fetchall()
    return [(row[0], row[1], row[2]) for row in rows]


class Feishu:
    """The Feishu message API: records each send; `lose` drops the reply, so the send's outcome stays unknown."""

    def __init__(self):
        self.sent: list[dict] = []
        self.lose = False

    def __call__(self, request):
        self.sent.append(json.loads(request.content))
        if self.lose:
            raise httpx.ReadTimeout("reply lost", request=request)
        return httpx.Response(200, json={"code": 0, "data": {"message_id": f"om_{len(self.sent)}"}})

    def card(self, index: int) -> tuple[str, str, str]:
        """Who it went to, what the card says, and the page its button opens."""
        card = json.loads(self.sent[index]["content"])
        link = card["body"]["elements"][-1]["behaviors"][0]["default_url"]
        return self.sent[index]["receive_id"], unescape(card["body"]["elements"][0]["content"]), parse_qs(urlsplit(link).query)["return_to"][0]


def test_answers_on_a_version_being_clarified_reach_who_prepares_it_on_feishu_at_most_every_ten_minutes(auth_store_factory, monkeypatch):
    """#354: an answer queues a notice in its own transaction for whoever opened the version's clarification and the platform
    operators; answers within ten minutes are told together; a person can turn it off for the version."""
    store, api_store, people, client = fixture(auth_store_factory, platform_operators="uaes-t:ou_owner")
    zhang, li, owner, reviewer, other = (client(name) for name in ("zhang", "li", "owner", "reviewer", "other"))
    tenant, owner_id, reviewer_id = people["zhang"].tenant_id, people["owner"].user_id, people["reviewer"].user_id
    qid = load(store, tenant)
    view = f"/rule-questionnaires/{qid}"

    # Nobody is clarifying this version: an answer tells nobody, and nobody is offered the setting.
    assert answer(zhang, qid, "CP:8#8.1", text="出现并有控制方法").status_code == 200
    assert notices(store) == [] and owner.get(view).json()["answer_alerts"] is None

    # The reviewer opens the clarification; from now on they and the platform operator hear about answers, the answerer does not.
    WorkflowStore(api_store).open_shared(people["reviewer"], clarification.task_id_for(tenant, "uaes", "v1.0"), clarification.new_state("uaes", "v1.0"))
    assert answer(zhang, qid, "common:模板", choice="A").status_code == 200
    queued = notices(store)
    assert sorted(payload["user_id"] for _, _, payload in queued) == sorted([owner_id, reviewer_id])
    assert {(status, payload["customer_id"], payload["questionnaire_id"], json.dumps(payload["rulebook"])) for status, _, payload in queued} == {
        ("queued", "uaes", qid, json.dumps({"version": "v1.0", "revision": 0}))
    }
    # A second answer within ten minutes adds no notice: the one not sent yet tells it too.
    assert answer(li, qid, "PKG:1#1", choice="relayed").status_code == 200
    assert len(notices(store)) == 2
    assert [client.get(view).json()["answer_alerts"] for client in (owner, reviewer, zhang)] == [{"muted": False}, {"muted": False}, None]

    # The operator turns it off for this version; only they are affected, and the answers stay readable.
    path = f"/rule-questionnaires/{qid}/answer-alerts"
    assert owner.put(path, json={"muted": True}).json() == {"muted": True}
    assert owner.get(view).json()["answer_alerts"] == {"muted": True} and reviewer.get(view).json()["answer_alerts"] == {"muted": False}
    assert len(owner.get(view).json()["questions"][0]["answers"]) == 1
    assert owner.put(path, json={"muted": "yes"}).json() == {"error": "invalid_answer_alerts_request"}
    assert (other.put(path, json={"muted": True}).status_code, other.get(view).status_code) == (403, 403)

    # Sending: the operator's notice queued before they turned it off is dropped unsent; the reviewer's goes out once,
    # naming the cards answered and who answered them, and opening the first of those cards on the rules page.
    feishu = Feishu()
    monkeypatch.setattr("api.notifications.tenant_access_token", lambda *args: "tenant-token")
    settings = api_store.settings.model_copy(update={"feishu_app_secret": "secret", "feishu_notifications_enabled": True})
    web = settings.canonical_web_url()
    with httpx.Client(transport=httpx.MockTransport(feishu)) as http:
        while dispatch_answer_notification(api_store, http, settings, "notifier"):
            pass
        assert len(feishu.sent) == 1
        receiver, text, page = feishu.card(0)
        assert receiver == "ou_reviewer" and "v1.0 修订 0 有 2 张卡片收到答复（张三、李四）" in text
        assert page == f"{web}/requirements?template=uaes&ruleQuestions={qid}#common%3A%E6%A8%A1%E6%9D%BF"
        assert sorted((status, state, payload["user_id"]) for status, state, payload in notices(store)) == sorted([("cancelled", "suppressed", owner_id), ("succeeded", "sent", reviewer_id)])

        # Within ten minutes of that notice, the next answers wait for the ten minutes to be up and go out as one more notice;
        # the operator who turned it off gets nothing.
        assert answer(zhang, qid, "CP:8#8.1", expected=1, text="有控制方法即可").status_code == 200
        assert answer(li, qid, "CP:8#8.1", choice=None, text="同意").status_code == 200
        later = notices(store)[2:]
        assert [(status, payload["user_id"]) for status, _, payload in later] == [("queued", reviewer_id)]
        assert not dispatch_answer_notification(api_store, http, settings, "notifier") and len(feishu.sent) == 1
        with store._conn() as conn:
            conn.execute("UPDATE engine.jobs SET lease_until=now() - interval '1 second' WHERE kind='rule_answers_notification' AND status='queued'")
        # Its reply is lost: the outcome stays unknown and the notice is never sent again.
        feishu.lose = True
        while dispatch_answer_notification(api_store, http, settings, "notifier"):
            pass
        assert len(feishu.sent) == 2
        receiver, text, page = feishu.card(1)
        assert receiver == "ou_reviewer" and "v1.0 修订 0 有 1 张卡片收到答复（张三、李四）" in text and page.endswith("#CP%3A8%238.1")
        assert [(status, state) for status, state, _ in notices(store)[2:]] == [("leased", "unknown")]
        assert not dispatch_answer_notification(api_store, http, settings, "notifier") and len(feishu.sent) == 2
