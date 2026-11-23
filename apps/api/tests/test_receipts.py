"""Receipts: a person's own signing actions, numbered and downloadable, read from the action record (#361)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings


def test_a_signing_action_is_the_persons_receipt(auth_store_factory, tmp_path):
    store = auth_store_factory(Settings(feishu_app_id="receipts", usage_charge_enabled=False, template_root=str(tmp_path), platform_operators="acme-t:ou_owner"))
    api = build_product_store(store.settings, objects=store.objects, role="api")
    owner, other = (TestClient(create_app(store.settings, auth_store=api)) for _ in range(2))
    owner.cookies.set(SESSION_COOKIE, store.create_session(store.upsert_feishu_identity("acme-t", "ou_owner", "负责人")))
    other.cookies.set(SESSION_COOKIE, store.create_session(store.upsert_feishu_identity("acme-t", "ou_other", "同事")))
    documents = [{"key": "A", "title": "A 文件", "clauses": [{"id": "A:1", "category": "", "text": "须签字"}]}]
    plan = {
        "steps": [{"step": "revision", "body": {"version": "v5.0", "revision": 0, "base": None, "documents": documents, "changes": {}, "origin": "clarification"}}],
        "unselected": ["clarified"],
        "reason": "再核对一遍",
    }
    done = owner.post("/rulebooks/uaes/plan", json=plan)
    assert done.status_code == 200
    ident = done.headers["x-ppap-action"]
    # Reading is not an action and leaves no receipt; another person's actions are not mine.
    assert owner.get("/rulebooks/uaes").headers.get("x-ppap-action") is None
    (receipt,) = owner.get("/receipts").json()["receipts"]
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    assert receipt["id"] == ident and receipt["number"] == f"R-{today}-0001"
    assert (receipt["action"], receipt["subject"], receipt["via"], receipt["outcome"]["text"]) == ("确认规则澄清的这一轮", "uaes 规则库", "网页", "已完成")
    assert receipt["details"] == ["执行：冻结修订", "没有勾选：标记已澄清（原因：再核对一遍）"]
    assert owner.get(f"/receipts/{ident}").json() == receipt
    pdf = owner.get(f"/receipts/{ident}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert f"R-{today}-0001.pdf" in pdf.headers["content-disposition"]
    assert other.get("/receipts").json()["receipts"] == [] and other.get(f"/receipts/{ident}").status_code == 404
    # A refused signing call is a receipt too: it says it did not complete, and why.
    owner.post("/rulebooks/uaes/plan", json={"steps": [{"step": "clarified", "body": {"version": "v9.9", "revision": 0}}]})
    latest = owner.get("/receipts").json()["receipts"][0]
    assert latest["number"] == f"R-{today}-0002" and latest["outcome"] == {"state": "failed", "text": "没有完成", "error": "plan_step_requires_revision"}


def _accepted_task(store, principal, charges: list[tuple[int, bool, str]]) -> str:
    """A task whose round was accepted, with the usage its acceptance recorded (no file split, as charging off leaves none)."""
    import uuid

    task, round_id = str(uuid.uuid4()), str(uuid.uuid4())
    with store._conn() as conn, conn.transaction():
        conn.execute("INSERT INTO product.resources (id, tenant_id, kind) VALUES (%s, %s, 'audit_draft'), (%s, %s, 'audit_round')", (task, principal.tenant_id, round_id, principal.tenant_id))
        conn.execute("INSERT INTO product.audit_drafts (id, tenant_id, created_by, status) VALUES (%s, %s, %s, 'accepted')", (task, principal.tenant_id, principal.user_id))
        conn.execute(
            """INSERT INTO product.audit_rounds (id, tenant_id, created_by, source_draft_id, idempotency_key, request_fingerprint, execution)
               VALUES (%s, %s, %s, %s, %s, 'fingerprint', 'completed')""",
            (round_id, principal.tenant_id, principal.user_id, task, str(uuid.uuid4())),
        )
        for points, free, status in charges:
            conn.execute(
                """INSERT INTO product.usage_events (id, tenant_id, user_id, ai_item_id, consumed_points, is_free_trial, external_status, round_id)
                   VALUES (%s, %s, %s, 'item', %s, %s, %s, %s)""",
                (str(uuid.uuid4()), principal.tenant_id, principal.user_id, points, free, status, round_id),
            )
    return task


def test_the_task_page_reads_what_its_round_charged(auth_store_factory, tmp_path):
    from product.store import actions

    store = auth_store_factory(Settings(feishu_app_id="charges", usage_charge_enabled=False, template_root=str(tmp_path)))
    api = build_product_store(store.settings, objects=store.objects, role="api")
    owner, colleague, outsider = (TestClient(create_app(store.settings, auth_store=api)) for _ in range(3))
    person = store.upsert_feishu_identity("acme-t", "ou_owner", "发起人")
    owner.cookies.set(SESSION_COOKIE, store.create_session(person))
    colleague.cookies.set(SESSION_COOKIE, store.create_session(store.upsert_feishu_identity("acme-t", "ou_colleague", "同事")))
    outsider.cookies.set(SESSION_COOKIE, store.create_session(store.upsert_feishu_identity("other-t", "ou_outsider", "外人")))
    charged = _accepted_task(store, person, [(200, True, "none"), (300, False, "pending")])
    free = _accepted_task(store, person, [(500, True, "none")])
    reported = _accepted_task(store, person, [(500, False, "confirmed")])
    uncharged = _accepted_task(store, person, [])
    # The paid part is not reported yet: the round charged 500 points and they are pending, for everyone who reads the task.
    assert owner.get(f"/audit-tasks/{charged}/charge").json() == {"points": 500, "state": "pending"}
    assert colleague.get(f"/audit-tasks/{charged}/charge").json() == {"points": 500, "state": "pending"}
    assert owner.get(f"/audit-tasks/{free}/charge").json() == {"points": 500, "state": "trial"}
    # Feishu acknowledging the report is not a debit: without its order the charge stays to be confirmed.
    assert owner.get(f"/audit-tasks/{reported}/charge").json() == {"points": 500, "state": "unknown"}
    # Charging off: nothing was charged, and it says so.
    assert owner.get(f"/audit-tasks/{uncharged}/charge").json() == {"points": 0, "state": "none"}
    assert outsider.get(f"/audit-tasks/{charged}/charge").status_code == 403
    assert owner.get("/audit-tasks/not-a-task/charge").status_code == 403
    assert TestClient(create_app(store.settings, auth_store=api)).get(f"/audit-tasks/{charged}/charge").status_code == 401
    # Starting the audit is a receipt that names the same charge.
    started = actions.start(api, person, channel="web", grant_id=None, intent_id=None, method="POST", route="/audit-tasks/{task_id}/confirm", path=f"/audit-tasks/{charged}/confirm", params={})
    actions.finish(api, started, 202)
    (receipt,) = owner.get("/receipts").json()["receipts"]
    assert (receipt["action"], receipt["task_id"], receipt["points"]) == ("开始审核", charged, {"points": 500, "state": "pending"})
