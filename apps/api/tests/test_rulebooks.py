"""Members read the rulebook revisions the product ships; without a session nothing is read."""

import json

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
