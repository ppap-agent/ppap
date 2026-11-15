"""Rules are delivered by developers: unless the deployment enables it, users cannot upload or publish rules."""

import uuid

from api import create_app
from fastapi.testclient import TestClient
from product.identity import SESSION_COOKIE
from product.settings import Settings


def _client(auth_store_factory, enabled: bool) -> TestClient:
    store = auth_store_factory(Settings(feishu_app_id="rule-upload-switch", rule_upload_enabled=enabled))
    principal = store.upsert_feishu_identity("rule-upload-" + uuid.uuid4().hex, "first")
    client = TestClient(create_app(store.settings, auth_store=store))
    client.cookies.set(SESSION_COOKIE, store.create_session(principal))
    return client


def test_rule_upload_is_off_by_default():
    assert Settings.model_fields["rule_upload_enabled"].default is False


def test_disabled_upload_refuses_parse_and_publish_and_says_so(auth_store_factory):
    client = _client(auth_store_factory, enabled=False)
    assert client.get("/public-config").json()["rule_upload_enabled"] is False
    request = {"request_id": str(uuid.uuid4()), "source_file_id": str(uuid.uuid4()), "source_file_version_id": str(uuid.uuid4()), "document_type": "控制计划"}
    parse = client.post("/rules/parse", json=request)
    assert (parse.status_code, parse.json()) == (403, {"error": "rule_upload_disabled"})
    publish = client.post("/rules", json={"request_id": str(uuid.uuid4()), "title": "新规则", "body": {"kind": "engine", "rules": []}})
    assert (publish.status_code, publish.json()) == (403, {"error": "rule_upload_disabled"})
    # Reading rules is unaffected.
    assert client.get("/rules").status_code == 200


def test_enabled_upload_reaches_the_rule_routes(auth_store_factory):
    client = _client(auth_store_factory, enabled=True)
    assert client.get("/public-config").json()["rule_upload_enabled"] is True
    parse = client.post("/rules/parse", json={"request_id": str(uuid.uuid4()), "source_file_id": str(uuid.uuid4()), "source_file_version_id": str(uuid.uuid4()), "document_type": "控制计划"})
    assert parse.json().get("error") != "rule_upload_disabled"
