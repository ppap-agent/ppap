"""New enterprises receive product rule versions, not a manual import project."""

import uuid

from api import create_app
from audit.presets import customer_presets
from fastapi.testclient import TestClient
from product import customers
from product.identity import SESSION_COOKIE
from product.settings import Settings


def test_new_enterprise_receives_all_preset_rules_without_upload(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="v4-presets"))
    tenant = "v4-preset-" + uuid.uuid4().hex
    principal = store.upsert_feishu_identity(tenant, "first")
    client = TestClient(create_app(store.settings, auth_store=store))
    client.cookies.set(SESSION_COOKIE, store.create_session(principal))
    rules = client.get("/rules").json()["rules"]
    presets = [rule for rule in rules if rule["rule_profile"] == "v4"]
    assert presets
    assert {row["key"] for row in client.get("/audit-customers").json()["customers"]} == {customer.key for customer in customers.CUSTOMERS}
    installed = [row for row in store.list_rules(principal.tenant_id) if row["rule_profile"] == "v4"]
    first_ids = {row["id"] for row in installed}
    # Every preset is installed and kept; new tasks are offered only 蔚来's types in its M, not its N06–N17.
    nio = customers.find("nio")
    outside = {row["id"] for row in installed if row["customers"] == [nio.key] and customers.type_code(row["type"], nio.key) not in nio.type_set.types}
    withheld = {row["id"] for row in installed} - {preset["id"] for preset in presets}
    assert outside and withheld == outside
    store.upsert_feishu_identity(tenant, "first")
    peer = store.upsert_feishu_identity(tenant, "second")
    assert {row["id"] for row in store.list_rules(peer.tenant_id) if row["rule_profile"] == "v4"} == first_ids
    entries = {preset.entry_id for preset in customer_presets()}
    for rule in presets:
        actual = client.get("/rules/" + rule["id"])
        assert actual.status_code == 200
        body = actual.json()["body"]
        assert body["preset_key"] in entries
        assert body["rules"]
        assert actual.json()["created_by"] is None
        assert actual.json()["source_file_version_id"] is None
    other = store.upsert_feishu_identity("other-" + uuid.uuid4().hex, "first")
    other_ids = {row["id"] for row in store.list_rules(other.tenant_id) if row["rule_profile"] == "v4"}
    assert len(other_ids) == len(first_ids) and not first_ids & other_ids
    assert store.get_rule(other.tenant_id, next(iter(first_ids))) is None
    assert store.list_rounds(principal) == []
    assert store.list_usage_events(principal) == []
    client.close()


def test_customer_upload_cannot_mint_or_replace_product_presets(auth_store_factory):
    tenant = "preset-authority-" + uuid.uuid4().hex
    # Even a platform operator cannot mint or replace a product preset.
    store = auth_store_factory(Settings(feishu_app_id="v4-preset-authority", platform_operators=tenant + ":operator"))
    principal = store.upsert_feishu_identity(tenant, "operator")
    client = TestClient(create_app(store.settings, auth_store=store))
    client.cookies.set(SESSION_COOKIE, store.create_session(principal))
    presets = [rule for rule in client.get("/rules").json()["rules"] if rule["rule_profile"] == "v4"]
    preset = next(rule for rule in presets if rule["type"] == "过程特殊特性清单")
    original = client.get("/rules/" + preset["id"]).json()["body"]
    content = b"customer rule source"
    version = client.post("/files", json={"original_filename": "rules.md", "byte_size": len(content), "content_type": "text/markdown"}).json()
    path = f"/files/{version['file_id']}/versions/{version['id']}"
    assert client.put(path + "/blob", content=content).status_code == 200
    assert client.post(path + "/finalize").status_code == 200
    request = {"title": "Customer rule", "body": original, "source_file_version_id": version["id"]}
    forged = client.post("/rules", json=request)
    assert forged.status_code == 400 and forged.json()["error"] == "product_preset_required"
    legacy = {
        "kind": "engine",
        "rule_profile": "v3",
        "type": "控制计划",
        "customers": ["UAES"],
        "rules": [{"id": "1", "category": "检查", "rule_to_check": "应填写零件号", "type_of_rule": "text", "priority": "P1"}],
    }
    replacement = client.post("/rules", json={**request, "body": legacy, "predecessor_id": preset["id"]})
    assert replacement.status_code == 400 and replacement.json()["error"] == "product_preset_required"
    independent = client.post("/rules", json={**request, "body": legacy})
    assert independent.status_code == 201
    assert client.get("/rules/" + preset["id"]).json()["body"] == original
    assert len([rule for rule in client.get("/rules").json()["rules"] if rule["rule_profile"] == "v4"]) == len(presets)
    client.close()
