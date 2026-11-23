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
    assert latest["number"] == f"R-{today}-0002" and latest["outcome"]["state"] == "failed" and "plan_step_requires_revision" in latest["outcome"]["text"]
