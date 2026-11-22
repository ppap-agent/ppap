"""Only listed platform operators change effective rules or checker versions; tenant members only read."""

import uuid

import pytest
from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import ISOLATED_DEV_PLATFORM_OPERATORS, Settings
from product.store.suites import SuiteStore

FILES = {"rules.json": b'{"title":"Control plan checks","rules":[{"id":"R1","rule_to_check":"Required"}]}', "checker.py": b"def check(context):\n    return []\n"}
PATH = "/suites/control-plan/active"
REFUSED = (403, {"error": "platform_operator_required"})


def adopt(revision_id):
    return {"revision_id": revision_id, "expected_revision": 0, "operation_id": str(uuid.uuid4()), "force_unverified": True}


def fixture(auth_store_factory, operators: str):
    store = auth_store_factory(Settings(feishu_app_id="platform-operators", usage_charge_enabled=False, platform_operators=operators))
    people = {
        "operator": store.upsert_feishu_identity("ops-a", "ou_operator", "Operator"),
        "member": store.upsert_feishu_identity("ops-a", "ou_member", "Member"),
        "other_operator": store.upsert_feishu_identity("ops-b", "ou_other", "Other"),
    }
    official = SuiteStore(store.settings, store.objects, role="migrator").create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None)
    api_store = build_product_store(store.settings, objects=store.objects, role="api")
    clients = {}
    for name, principal in people.items():
        client = TestClient(create_app(store.settings, auth_store=api_store))
        client.cookies.set(SESSION_COOKIE, store.create_session(principal))
        clients[name] = client
    return store, official, people, clients


def publications(store) -> int:
    with store._conn() as conn:
        return conn.execute("SELECT count(*) FROM product.suite_publications").fetchone()[0]


def test_operator_list_settings():
    assert Settings.model_fields["platform_operators"].default == ""
    assert Settings(profile="isolated-dev").platform_operators == ISOLATED_DEV_PLATFORM_OPERATORS
    assert Settings(profile="isolated-dev", platform_operators="").platform_operator_identities() == frozenset()
    listed = Settings(platform_operators=" t1:ou_a , t2:ou_b ,")
    assert listed.platform_operator("t1", "ou_a") and listed.platform_operator("t2", "ou_b")
    # The same open_id in another tenant, or another person in the tenant, is not an operator.
    assert not listed.platform_operator("t2", "ou_a") and not listed.platform_operator("t1", "ou_b")
    for malformed in ("ou_a", ":ou_a", "t1:", "t1:ou_a:x"):
        with pytest.raises(ValueError):
            Settings(platform_operators=malformed)


def test_member_is_refused_every_rule_and_version_write_but_still_reads(auth_store_factory):
    store, official, _, clients = fixture(auth_store_factory, "ops-a:ou_operator,ops-b:ou_other")
    member = clients["member"]
    parse = member.post("/rules/parse", json={"request_id": str(uuid.uuid4()), "source_file_id": str(uuid.uuid4()), "source_file_version_id": str(uuid.uuid4()), "document_type": "控制计划"})
    publish = member.post("/rules", json={"request_id": str(uuid.uuid4()), "title": "新规则", "body": {"kind": "engine", "rules": []}})
    adoption = member.put(PATH, json=adopt(official.id))
    for response in (parse, publish, adoption):
        assert (response.status_code, response.json()) == REFUSED
    assert publications(store) == 0
    for path in ("/rules", "/suites", PATH, f"/suite-revisions/{official.id}"):
        assert member.get(path).status_code == 200, path
    # Unauthenticated callers are still told to sign in, not that they lack the operator role.
    anonymous = TestClient(create_app(store.settings, auth_store=store))
    assert anonymous.put(PATH, json=adopt(official.id)).status_code == 401
    assert anonymous.post("/rules", json={}).status_code == 401


def test_empty_list_refuses_everyone(auth_store_factory):
    store, official, _, clients = fixture(auth_store_factory, "")
    for client in clients.values():
        assert (lambda r: (r.status_code, r.json()))(client.put(PATH, json=adopt(official.id))) == REFUSED
        assert (lambda r: (r.status_code, r.json()))(client.post("/rules", json={})) == REFUSED
    assert publications(store) == 0


def test_listed_operator_passes_the_write_gate_and_members_read_the_adoption(auth_store_factory):
    # Replay and another tenant's candidate are covered by test_suite_releases.py.
    store, official, people, clients = fixture(auth_store_factory, "ops-a:ou_operator,ops-b:ou_other")
    adopted = clients["operator"].put(PATH, json=adopt(official.id))
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["published_by"] == people["operator"].user_id
    assert publications(store) == 1
    assert clients["member"].get(PATH).json()["current_revision_id"] == official.id
    # Past the gate, the operator's request is still validated.
    assert clients["operator"].post("/rules", json=[]).json() == {"error": "invalid_rule_request"}
