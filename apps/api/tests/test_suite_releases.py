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
    # Saving an unverified official candidate is not publication for other tenants.
    carol = TestClient(create_app(store.settings, auth_store=store))
    carol.cookies.set(SESSION_COOKIE, store.create_session(store.upsert_feishu_identity("untouched", "reader")))
    untouched = carol.get(path).json()
    assert untouched["selection_mode"] == "candidate_only"
    assert untouched["current_revision_id"] is None
    assert carol.get(f"/suite-revisions/{second_official.id}").status_code == 200
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


def test_candidate_save_is_idempotent_and_readable_after_lost_response(auth_store_factory):
    store, official, _, _, (alice, bob, carol) = prepare_fixture(auth_store_factory)
    path = f"/suite-revisions/{official.id}/candidates"
    operation = str(uuid.uuid4())
    body = {"operation_id": operation, "files": {"checker.py": "def check(context):\n    return [1]\n"}}
    receipt_path = f"{path}/{operation}"
    assert alice.get(receipt_path).status_code == 404
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = [future.result() for future in [pool.submit(alice.post, path, json=body) for _ in range(2)]]
    assert [reply.status_code for reply in replies] == [201, 201]
    candidate = replies[0].json()
    assert replies[1].json() == candidate
    assert alice.get(receipt_path).json() == candidate
    assert bob.get(receipt_path).json() == candidate
    assert carol.get(receipt_path).status_code == 404
    assert alice.post(path, json={**body, "files": {"checker.py": "changed"}}).status_code == 409
    assert bob.post(path, json=body).status_code == 409
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_candidate_operations").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE tenant_id IS NOT NULL").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == 0


def test_first_adoption_reads_accessible_candidate_and_preserves_receipt(auth_store_factory):
    store, official, creator, publisher, (alice, bob, carol) = prepare_fixture(auth_store_factory)
    path = "/suites/control-plan/active"
    available = bob.get(path)
    assert available.status_code == 200, available.text
    before = available.json()
    assert before["selection_mode"] == "candidate_only"
    assert before["current_revision_id"] is None and before["revision"] == 0
    assert before["candidate_revision_id"] == official.id
    assert before["history"] == []
    assert before == next(row for row in bob.get("/suites").json()["suites"] if row["suite_key"] == "control-plan")
    candidate = alice.post(f"/suite-revisions/{official.id}/candidates", json={"operation_id": str(uuid.uuid4()), "files": {"checker.py": "def check(context): return []\n"}}).json()
    assert bob.get(path).json()["candidate_revision_id"] == candidate["id"]
    request = command(candidate["id"], expected_revision=before["revision"], force=True)
    published = bob.put(path, json=request)
    assert published.status_code == 200, published.text
    receipt = published.json()
    assert receipt["previous_revision_id"] is None
    assert receipt["revision"] == 1 and receipt["force_unverified"] is True
    assert receipt["verification_status"] == "unverified"
    assert receipt["published_by"] == publisher.user_id
    assert bob.put(path, json=request).json() == receipt
    active = alice.get(path).json()
    assert active["current_revision_id"] == candidate["id"] and active["selection_mode"] == "pinned"
    assert active["history"] == [receipt]
    assert carol.get(path).json()["candidate_revision_id"] == official.id
    assert carol.get(path).json()["current_revision_id"] is None
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_publications").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.usage_events").fetchone()[0] == 0


def test_catalog_preserves_titles_selections_and_reuses_shared_content(auth_store_factory, monkeypatch):
    import product.store.suite_releases as release_store

    store, official, creator, _, (alice, bob, carol) = prepare_fixture(auth_store_factory)
    second = SuiteStore(store.settings, store.objects, role="migrator").create_revision(tenant_id=None, suite_key="second-check", files=FILES, created_by=None)
    tenant_candidate = SuiteStore(store.settings, store.objects, role="api").create_revision(
        tenant_id=creator.tenant_id, suite_key=second.suite_key, files={"checker.py": b"def check(context): return []\n"}, created_by=creator.user_id, base_revision_id=second.id
    )
    assert alice.put("/suites/control-plan/active", json=command(official.id)).status_code == 200
    expected = [bob.get(f"/suites/{key}/active").json() for key in ("control-plan", "second-check")]
    object_reads, connections = [], []
    get, connect = store.objects.get, release_store.connect

    def read_object(key):
        object_reads.append(key)
        return get(key)

    def open_connection(*args, **kwargs):
        connections.append(True)
        return connect(*args, **kwargs)

    monkeypatch.setattr(store.objects, "get", read_object)
    monkeypatch.setattr(release_store, "connect", open_connection)
    for _ in range(2):
        object_reads.clear()
        connections.clear()
        response = bob.get("/suites")
        assert response.status_code == 200, response.text
        assert response.json() == {"suites": expected}
        assert [row["title"] for row in response.json()["suites"]] == ["Control plan checks", "Control plan checks"]
        assert [row["selection_mode"] for row in response.json()["suites"]] == ["pinned", "candidate_only"]
        assert response.json()["suites"][1]["candidate_revision_id"] == tenant_candidate.id
        # The next HTTP read gets a fresh cache; within one read every shared
        # immutable object is fetched once and the DB connection is reused.
        assert len(object_reads) == len(set(object_reads)) == 3
        assert len(connections) == 1
    other = carol.get("/suites").json()["suites"]
    assert [row["selection_mode"] for row in other] == ["candidate_only", "candidate_only"]
    assert [row["candidate_revision_id"] for row in other] == [official.id, second.id]
