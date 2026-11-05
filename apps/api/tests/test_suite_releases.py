"""Suite routes exercise actual tenant transactions, never fake formal audit results."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor

from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.store.suites import SuiteStore

FILES = {"rules.json": b'{"title":"Control plan checks","rules":[{"id":"R1","rule_to_check":"Required"}]}', "checker.py": b"def check(context):\n    return []\n"}


def prepare_fixture(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="suite-releases", usage_charge_enabled=False))
    principal = store.upsert_feishu_identity("suite-a", "operator-a", "Alice")
    peer = store.upsert_feishu_identity("suite-a", "operator-b", "Bob")
    other = store.upsert_feishu_identity("suite-b", "operator-c", "Carol")
    official = SuiteStore(store.settings, store.objects, role="migrator").create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None)
    api_store = build_product_store(store.settings, objects=store.objects, role="api")
    clients = []
    for person in (principal, peer, other):
        client = TestClient(create_app(store.settings, auth_store=api_store))
        client.cookies.set(SESSION_COOKIE, store.create_session(person))
        clients.append(client)
    return store, official, principal, peer, clients


def command(revision_id, expected_revision=0, force=True):
    return {"revision_id": revision_id, "expected_revision": expected_revision, "operation_id": str(uuid.uuid4()), "force_unverified": force}


def test_member_can_publish_peers_candidate_with_explicit_risk_and_read_same_receipt(auth_store_factory):
    store, official, creator, publisher, (alice, bob, carol) = prepare_fixture(auth_store_factory)
    path = "/suites/control-plan/active"
    assert TestClient(create_app(store.settings, auth_store=store)).get(path).status_code == 401
    candidate = alice.post("/suites/control-plan/variants", json={"base_revision_id": official.id, "files": {"checker.py": "def check(context):\n    return [1]\n"}})
    assert candidate.status_code == 201, candidate.text
    revision = candidate.json()
    assert revision["created_by"] == creator.user_id
    assert revision["validation"]["verified"] is False
    assert revision["changed_paths"] == ["checker.py"]
    assert revision["diff"][0]["before"] == FILES["checker.py"].decode()
    assert bob.get(f"/suite-revisions/{revision['id']}").status_code == 200
    assert carol.get(f"/suite-revisions/{revision['id']}").status_code == 403
    assert carol.put(path, json=command(revision["id"])).status_code == 403
    assert bob.put(path, json=command(revision["id"], force=False)).json()["error"] == "suite_confirmation_required"
    request = command(revision["id"])
    first = bob.put(path, json=request)
    assert first.status_code == 200, first.text
    assert first.json()["published_by"] == publisher.user_id
    assert first.json()["published_by_name"] == "Bob"
    assert first.json()["verification_status"] == "unverified"
    assert bob.put(path, json=request).json() == first.json()
    assert bob.get(path).json()["revision"] == 1
    assert bob.put(path, json={**request, "revision_id": official.id}).status_code == 409
    # No user-supplied PASS/verified result can become a regression proof.
    assert bob.put(path, json={**command(revision["id"], 1), "verified": True}).status_code == 400
    # Candidate/save/publish did not create any paid audit, job, or notification.
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.usage_events").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.suite_publications").fetchone()[0] == 1


def test_publish_compare_and_swap_and_fixed_rollback(auth_store_factory):
    store, official, _, _, (alice, bob, _) = prepare_fixture(auth_store_factory)
    path = "/suites/control-plan/active"
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(client.put, path, json=command(official.id)) for client in (alice, bob)]
        results = [request.result() for request in requests]
    assert sorted(response.status_code for response in results) == [200, 409]
    second_official = SuiteStore(store.settings, store.objects, role="migrator").create_revision(
        tenant_id=None,
        suite_key="control-plan",
        files={**FILES, "checker.py": b"def check(context):\n    return [2]\n"},
        created_by=None,
    )
    # A deliberate pin/rollback to a prior official version is not silently upgraded.
    assert alice.get(path).json()["current_revision_id"] == official.id
    # An untouched tenant still follows newly published official content.
    carol = TestClient(create_app(store.settings, auth_store=store))
    carol.cookies.set(SESSION_COOKIE, store.create_session(store.upsert_feishu_identity("untouched", "reader")))
    assert carol.get(path).json()["current_revision_id"] == second_official.id
    published = bob.put(path, json=command(second_official.id, 1)).json()
    rollback = alice.put(path, json=command(official.id, 2)).json()
    assert published["revision"] == 2
    assert rollback["revision"] == 3
    assert rollback["previous_revision_id"] == second_official.id
    assert alice.get(path).json()["current_revision_id"] == official.id


def test_template_distribution_and_tenant_input_cannot_be_forged(auth_store_factory):
    _, official, _, _, (alice, _, _) = prepare_fixture(auth_store_factory)
    path = "/suites/control-plan/variants"
    rejected = alice.post(path, json={"base_revision_id": official.id, "files": {"templates.json": json.dumps([{"kind": "licensed_blank", "license": "private", "uri": "private-original"}])}})
    assert rejected.status_code == 400
    assert rejected.json()["error"] == "template_distribution_not_authorized"
    assert alice.post(path, json={"base_revision_id": official.id, "files": {"checker.py": "new"}, "tenant_id": "other"}).status_code == 400
