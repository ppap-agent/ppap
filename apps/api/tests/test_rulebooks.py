"""Members read the rulebook revisions the product ships; without a session nothing is read."""

from pathlib import Path

from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings

# The repository's assets, wherever the tests run from (the distribution check runs them outside the checkout).
ASSETS = Path(__file__).resolve().parents[3] / "assets"


def test_members_read_rulebook_revisions(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="rulebooks", usage_charge_enabled=False, template_root=str(ASSETS)))
    member = store.upsert_feishu_identity("acme-t", "ou_reader", "读者")
    client = TestClient(create_app(store.settings, auth_store=build_product_store(store.settings, objects=store.objects, role="api")))
    assert client.get("/rulebooks/uaes").status_code == 401
    client.cookies.set(SESSION_COOKIE, store.create_session(member))
    listed = client.get("/rulebooks/uaes").json()["revisions"]
    assert ("v1.0", 0, False, 63) in [(row["version"], row["revision"], row["in_audit"], row["clause_count"]) for row in listed]
    view = client.get("/rulebooks/uaes/v1.0/0").json()
    assert sum(len(document["clauses"]) for document in view["documents"]) == 63
    assert client.get("/rulebooks/li").json() == {"revisions": []}
    assert client.get("/rulebooks/uaes/v1.0/9").status_code == 404
