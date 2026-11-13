"""Independent webhook delivery through the API's PostgreSQL ticket store."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.feishu_usage import tenant_access_token
from product.settings import Settings


@pytest.fixture
def ticket_system(auth_store_factory):
    settings = Settings(feishu_app_id="cli_tickets", feishu_app_secret="test-secret", feishu_verification_token="test-verification")
    store = auth_store_factory(settings)
    api = build_product_store(settings, objects=store.objects, role="api")
    with TestClient(create_app(settings, auth_store=api)) as client:
        yield client, api, settings


def ticket_event(value, issued_at, *, app_id="cli_tickets", token="test-verification"):
    return {
        "type": "event_callback",
        "token": token,
        "ts": f"{issued_at.timestamp():.6f}",
        "uuid": value + "-event",
        "event": {"type": "app_ticket", "app_id": app_id, "app_ticket": value},
    }


def test_duplicate_and_delayed_delivery_do_not_replace_the_newest_ticket_after_restart(ticket_system):
    client, store, settings = ticket_system
    older = datetime.now(timezone.utc) - timedelta(hours=1)
    latest = older + timedelta(minutes=30)
    for value, issued_at in (("older-ticket", older), ("latest-ticket", latest), ("latest-ticket", latest), ("older-ticket", older)):
        response = client.post("/integrations/feishu/events", json=ticket_event(value, issued_at))
        assert response.status_code == 200
        assert value not in response.text
    restored = build_product_store(settings, objects=store.objects, role="api")
    assert restored.app_ticket(settings.feishu_app_id) == "latest-ticket"
    with TestClient(create_app(settings, auth_store=restored)) as restarted:
        assert restarted.get("/capabilities").json()["feishu"]["app_ticket"] == "received"
    assert store.app_ticket("other-app") is None


def test_concurrent_deliveries_keep_the_newest_provider_event(ticket_system):
    client, store, settings = ticket_system
    older = datetime.now(timezone.utc) - timedelta(hours=1)
    events = [ticket_event("ticket-" + str(index), older + timedelta(seconds=index)) for index in (2, 0, 1, 2, 0)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        replies = list(pool.map(lambda event: client.post("/integrations/feishu/events", json=event), events))
    assert all(reply.status_code == 200 for reply in replies)
    assert store.app_ticket(settings.feishu_app_id) == "ticket-2"


def test_challenge_requires_the_configured_token_without_creating_a_ticket(ticket_system):
    client, store, settings = ticket_system
    for token, status in (("", 401), ("wrong", 401), ("test-verification", 200)):
        response = client.post("/integrations/feishu/events", json={"type": "url_verification", "challenge": "challenge-value", "token": token})
        assert response.status_code == status
        if status == 200:
            assert response.json() == {"challenge": "challenge-value"}
        assert store.app_ticket(settings.feishu_app_id) is None


@pytest.mark.parametrize("timestamp", [None, "", "NaN", "Infinity", "-1", True, 1700000000, "99999999999999999", "1700000000.1234567890123456789"])
def test_unordered_or_malformed_tickets_do_not_create_state(ticket_system, timestamp):
    client, store, settings = ticket_system
    event = ticket_event("invalid-timestamp-ticket", datetime.now(timezone.utc))
    event["ts"] = timestamp
    response = client.post("/integrations/feishu/events", json=event)
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_ticket_timestamp"}
    assert store.app_ticket(settings.feishu_app_id) is None


def test_wrong_app_token_future_or_encrypted_event_cannot_poison_a_valid_ticket(ticket_system):
    client, store, settings = ticket_system
    now = datetime.now(timezone.utc)
    assert client.post("/integrations/feishu/events", json=ticket_event("valid-ticket", now)).status_code == 200
    for event, status in (
        (ticket_event("wrong-token-ticket", now, token="wrong"), 401),
        (ticket_event("foreign-ticket", now, app_id="other-app"), 403),
        ({**ticket_event("conflicting-app-ticket", now, app_id="other-app"), "header": {"app_id": settings.feishu_app_id}}, 403),
        (ticket_event("future-ticket", now + timedelta(days=1)), 400),
        ({"encrypt": "blob"}, 400),
    ):
        assert client.post("/integrations/feishu/events", json=event).status_code == status
        assert store.app_ticket(settings.feishu_app_id) == "valid-ticket"


def test_duplicate_and_equal_time_conflict_do_not_refresh_receipt_metadata(ticket_system):
    client, store, settings = ticket_system
    now = datetime.now(timezone.utc)
    assert client.post("/integrations/feishu/events", json=ticket_event("first-ticket", now)).status_code == 200
    before = client.get("/capabilities").json()["feishu"]
    assert before["app_ticket_event_timestamp"]
    assert before["app_ticket_received_at"]
    for value in ("first-ticket", "different-ticket"):
        assert client.post("/integrations/feishu/events", json=ticket_event(value, now)).status_code == 200
        assert client.get("/capabilities").json()["feishu"] == before
    assert store.app_ticket(settings.feishu_app_id) == "first-ticket"


def test_version_two_timestamp_and_unversioned_bootstrap_cannot_roll_back_a_callback(ticket_system):
    client, store, settings = ticket_system
    assert store.save_app_ticket(settings.feishu_app_id, "bootstrap-ticket")
    assert client.get("/capabilities").json()["feishu"]["app_ticket_event_timestamp"] == ""
    body = {
        "schema": "2.0",
        "header": {"event_type": "app_ticket", "app_id": settings.feishu_app_id, "token": "test-verification", "create_time": "1700000000123"},
        "event": {"app_ticket": "callback-ticket", "app_id": settings.feishu_app_id},
    }
    assert client.post("/integrations/feishu/events", json=body).status_code == 200
    assert client.get("/capabilities").json()["feishu"]["app_ticket_event_timestamp"] == "1700000000.123"
    assert not store.save_app_ticket(settings.feishu_app_id, "unversioned-replay")
    assert store.app_ticket(settings.feishu_app_id) == "callback-ticket"


def test_submicrosecond_event_order_is_preserved_in_postgres(ticket_system):
    client, store, settings = ticket_system
    for timestamp, value in (("1700000000.1234567", "older"), ("1700000000.1234568", "newer"), ("1700000000.1234567", "older")):
        event = ticket_event(value, datetime.now(timezone.utc))
        event["ts"] = timestamp
        assert client.post("/integrations/feishu/events", json=event).status_code == 200
    assert store.app_ticket(settings.feishu_app_id) == "newer"
    assert client.get("/capabilities").json()["feishu"]["app_ticket_event_timestamp"] == "1700000000.1234568"


def test_restored_ticket_exchanges_directly_with_feishu_without_uac(ticket_system):
    client, store, settings = ticket_system
    for index in (1, 2):
        event = ticket_event("ticket-" + str(index), datetime.now(timezone.utc) - timedelta(minutes=3 - index))
        assert client.post("/integrations/feishu/events", json=event).status_code == 200
    restored = build_product_store(settings, objects=store.objects, role="api")

    def provider(request):
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/app_access_token":
            assert json.loads(request.content) == {"app_id": "cli_tickets", "app_secret": "test-secret", "app_ticket": "ticket-2"}
            return httpx.Response(200, json={"code": 0, "app_access_token": "provider-app-access"})
        assert str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token"
        assert json.loads(request.content) == {"app_access_token": "provider-app-access", "tenant_key": "test-tenant"}
        return httpx.Response(200, json={"code": 0, "tenant_access_token": "provider-tenant-access"})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        assert tenant_access_token(http, settings.feishu_app_id, settings.feishu_app_secret, restored.app_ticket(settings.feishu_app_id), "test-tenant") == "provider-tenant-access"
