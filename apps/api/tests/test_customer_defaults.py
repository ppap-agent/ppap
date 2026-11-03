"""V4 enterprise defaults are tenant preferences, separate from task inputs."""

import uuid
from concurrent.futures import ThreadPoolExecutor

from api import create_app
from fastapi.testclient import TestClient
from product.identity import SESSION_COOKIE
from product.settings import Settings


def members(store, tenant):
    clients = []
    for name in ("first", "second"):
        principal = store.upsert_feishu_identity(tenant, name)
        client = TestClient(create_app(store.settings, auth_store=store))
        client.cookies.set(SESSION_COOKIE, store.create_session(principal))
        clients.append(client)
    return clients


def test_members_share_defaults_but_not_other_tenants(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="v4-defaults"))
    first, peer = members(store, "defaults-" + uuid.uuid4().hex)
    other, _ = members(store, "other-" + uuid.uuid4().hex)
    path = "/audit-customers/default"
    assert TestClient(create_app(store.settings, auth_store=store)).get(path).status_code == 401
    initial = first.get(path)
    assert initial.status_code == 200
    assert initial.headers["cache-control"] == "private, no-store"
    assert initial.json() == {"customer_preference": None, "revision": 0}
    command = {"customer_preference": "UAES", "expected_revision": 0, "confirmed": True}
    assert first.put(path, json={**command, "confirmed": False}).status_code == 400
    assert peer.get(path).json() == initial.json()
    assert first.put(path, json=command).json() == {"customer_preference": "UAES", "revision": 1}
    assert peer.get(path).json() == {"customer_preference": "UAES", "revision": 1}
    assert other.get(path).json() == initial.json()
    assert peer.put(path, json=command).status_code == 409
    assert peer.put(path, json={**command, "expected_revision": 1, "customer_preference": "蔚来"}).status_code == 200
    # The body cannot select another tenant, even for an authenticated member.
    assert other.put(path, json={**command, "tenant_id": store.upsert_feishu_identity("foreign", "user").tenant_id}).status_code == 400
    assert first.get(path).json() == {"customer_preference": "蔚来", "revision": 2}
    for client in (first, peer, other):
        client.close()


def test_concurrent_updates_do_not_lose_a_members_change(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="v4-defaults"))
    first, peer = members(store, "defaults-" + uuid.uuid4().hex)
    path = "/audit-customers/default"
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(client.put, path, json={"customer_preference": value, "expected_revision": 0, "confirmed": True}) for client, value in ((first, "理想"), (peer, "蔚来"))]
        results = [request.result() for request in requests]
    assert sorted(result.status_code for result in results) == [200, 409]
    winner = next(result.json() for result in results if result.status_code == 200)
    assert first.get(path).json() == winner
    assert peer.get(path).json() == winner
    assert winner["revision"] == 1
    # Readback is sufficient after a lost response; it does not repeat the write.
    assert first.get(path).json() == winner
    first.close()
    peer.close()
