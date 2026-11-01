"""Callback response tests at the HTTP and database adapter boundaries.

The in-memory adapter does not prove durable acceptance; schema tests own that.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from api import create_app, notifications
from fastapi.testclient import TestClient
from product.identity import Principal
from product.settings import Settings


def card_event(*, token="verification", app_id="cli_callback"):
    return {
        "schema": "2.0",
        "header": {"event_type": "card.action.trigger", "app_id": app_id, "token": token},
        "event": {
            "operator": {"tenant_key": "test-tenant", "open_id": "test-creator"},
            "token": "card-update-token",
            "action": {"tag": "button", "value": {"action": "confirm_audit_task", "notification_id": "test-notification"}},
            "context": {"open_message_id": "test-message"},
        },
    }


def callback_settings():
    return Settings(
        feishu_app_id="cli_callback",
        feishu_app_secret="test-secret",
        feishu_verification_token="verification",
        feishu_ai_item_id="test-item",
        feishu_notifications_enabled=False,
        usage_charge_enabled=False,
    )


class NoDatabaseStore:
    def __init__(self, receipt=None):
        self.receipt = receipt if receipt is not None else {"request_id": "test-request", "state": "pending", "reason": None}
        self.actions = []

    def enqueue_task_confirmation(self, action):
        self.actions.append(action)
        return self.receipt

    def app_ticket(self, _app_id):
        return "test-ticket"

    def confirm_task_notification(self, action, *, usage_policy):
        # This bridge makes the former synchronous callback exercise the real
        # external quota boundary. It never creates a Round or touches SQL.
        principal = Principal("test-user", "test-tenant-id", "feishu", "cli_callback", action.tenant_key, action.subject, "verified")
        usage_policy(principal)
        raise AssertionError("callback must not synchronously accept the task")


def test_callback_returns_pending_without_waiting_for_external_quota():
    release = threading.Event()
    quota_entered = threading.Event()
    store = NoDatabaseStore()

    def provider(request):
        if request.url.path.endswith("/auth/v3/app_access_token"):
            return httpx.Response(200, json={"code": 0, "app_access_token": "test-app-access"})
        if request.url.path.endswith("/auth/v3/tenant_access_token"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "test-tenant-access"})
        quota_entered.set()
        assert release.wait(5), "test quota response was not released"
        return httpx.Response(200, json={"code": 0, "data": {"tenant_ai_item": {"enabled": True, "user_quota": 1, "ai_package_purchased": True}}})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        client = TestClient(create_app(callback_settings(), auth_store=store, http=http))
        with ThreadPoolExecutor(max_workers=1) as pool:
            started = time.monotonic()
            pending = pool.submit(client.post, "/integrations/feishu/events", json=card_event())
            try:
                response = pending.result(timeout=2.5)
                assert time.monotonic() - started < 3
                assert not quota_entered.is_set()
                assert response.json() == {"toast": {"type": "info", "content": "已收到确认，正在核对。请打开审核查看结果。"}}
                assert len(store.actions) == 1
                assert store.actions[0].message_id == "test-message"
                assert "card" not in response.json()
            finally:
                release.set()


def test_dispatcher_checks_the_clicked_actor_quota_when_outbound_notifications_are_disabled():
    settings = callback_settings()
    principal = Principal("test-user", "test-tenant-id", "feishu", "cli_callback", "test-tenant", "test-creator", "verified")

    class QueueStore(NoDatabaseStore):
        def claim_task_confirmation(self, worker_ident):
            assert worker_ident == "test-api-worker"
            return "claimed-confirmation"

        def process_task_confirmation(self, job, worker_ident, *, usage_policy):
            assert job == "claimed-confirmation"
            assert worker_ident == "test-api-worker"
            partner = usage_policy(principal)
            assert partner.enabled is True
            assert partner.user_quota == 7
            return {"request_id": "test-request", "state": "accepted", "reason": None}

    paths = []

    def provider(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/auth/v3/app_access_token"):
            assert json.loads(request.content) == {"app_id": "cli_callback", "app_secret": "test-secret", "app_ticket": "test-ticket"}
            return httpx.Response(200, json={"code": 0, "app_access_token": "test-app-access"})
        if request.url.path.endswith("/auth/v3/tenant_access_token"):
            assert json.loads(request.content) == {"app_access_token": "test-app-access", "tenant_key": "test-tenant"}
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "test-tenant-access"})
        assert request.url.path == "/open-apis/partner_ai/v1/tenant_ai_items/test-item"
        assert dict(request.url.params) == {"user_id": "test-creator", "user_id_type": "open_id"}
        assert request.headers["authorization"] == "Bearer test-tenant-access"
        return httpx.Response(200, json={"code": 0, "data": {"tenant_ai_item": {"enabled": True, "user_quota": 7, "ai_package_purchased": True}}})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        assert notifications.dispatch_task_confirmation(QueueStore(), http, settings, "test-api-worker") is True
    assert paths == ["/open-apis/auth/v3/app_access_token", "/open-apis/auth/v3/tenant_access_token", "/open-apis/partner_ai/v1/tenant_ai_items/test-item"]


def test_api_lifespan_processes_clicked_confirmations_when_sending_is_disabled():
    polled = threading.Event()

    class EmptyQueueStore:
        def claim_task_confirmation(self, _worker_ident):
            polled.set()
            return None

    with TestClient(create_app(callback_settings(), auth_store=EmptyQueueStore())):
        assert polled.wait(1), "disabling outbound messages must not stop queued confirmation processing"


@pytest.mark.parametrize(
    ("state", "reason", "expected"),
    [
        ("accepted", None, {"type": "success", "content": "本任务已受理，请打开审核查看进度。"}),
        ("rejected", "规则已更新，请打开页面重新识别并核对。", {"type": "warning", "content": "规则已更新，请打开页面重新识别并核对。"}),
        ("rejected", "private-database-detail", {"type": "warning", "content": "本次未受理，请打开页面核对当前任务。"}),
    ],
)
def test_callback_uses_the_persisted_receipt_without_reading_a_task(state, reason, expected):
    store = NoDatabaseStore({"request_id": "test-request", "state": state, "reason": reason})
    response = TestClient(create_app(callback_settings(), auth_store=store)).post("/integrations/feishu/events", json=card_event())
    assert response.status_code == 200
    assert response.json() == {"toast": expected}
    assert len(store.actions) == 1


@pytest.mark.parametrize("receipt", [None, {}, {"state": "pending"}, {"request_id": "test-request", "state": "unknown"}, {"request_id": 42, "state": "accepted"}])
def test_missing_or_invalid_committed_receipt_is_unknown_not_queued(receipt):
    class InvalidReceiptStore:
        def enqueue_task_confirmation(self, _action):
            return receipt

    response = TestClient(create_app(callback_settings(), auth_store=InvalidReceiptStore())).post("/integrations/feishu/events", json=card_event())
    assert response.status_code == 200
    assert response.json() == {"toast": {"type": "warning", "content": "受理结果尚未确认。请打开页面读取任务，不要重复新建审核。"}}


@pytest.mark.parametrize(
    ("error", "kind", "message"),
    [
        (KeyError("private-database-detail"), "error", "无法确认此卡片。请由任务创建人打开页面核对。"),
        (ValueError("rule_candidate_expired"), "warning", "规则已更新，本次未受理。请打开页面重新识别并核对。"),
        (ValueError("private-database-detail"), "warning", "本次未受理，请打开页面核对当前任务。"),
        (RuntimeError("private-database-detail"), "warning", "受理结果尚未确认。请打开页面读取任务，不要重复新建审核。"),
    ],
)
def test_callback_does_not_claim_a_commit_after_permission_rejection_or_storage_failure(error, kind, message, caplog):
    class FailingStore:
        def enqueue_task_confirmation(self, _action):
            raise error

    response = TestClient(create_app(callback_settings(), auth_store=FailingStore())).post("/integrations/feishu/events", json=card_event())
    assert response.status_code == 200
    assert response.json() == {"toast": {"type": kind, "content": message}}
    assert "private-database-detail" not in response.text
    assert "private-database-detail" not in caplog.text


@pytest.mark.parametrize(("event", "status", "error"), [(card_event(token="card-update-token"), 401, "unauthenticated"), (card_event(app_id="other-app"), 403, "app_mismatch")])
def test_callback_authentication_guards_run_before_database_enqueue(event, status, error):
    store = NoDatabaseStore()
    response = TestClient(create_app(callback_settings(), auth_store=store)).post("/integrations/feishu/events", json=event)
    assert response.status_code == status
    assert response.json() == {"error": error}
    assert store.actions == []


@pytest.mark.parametrize("failure", ["confirmation", "notification"])
def test_dispatcher_failure_in_one_queue_does_not_skip_the_other_queue(failure, caplog):
    stop = threading.Event()
    received = []

    class QueueStore:
        def claim_task_confirmation(self, _worker_ident):
            received.append("confirmation")
            if failure == "confirmation":
                raise RuntimeError("private-dispatch-detail")
            return "expired-claim"

        def process_task_confirmation(self, _claim, _worker_ident, *, usage_policy):
            received.append("expired-claim-ignored")
            return None

        def claim_task_notification(self, _worker_ident, *, task_id):
            received.append("notification")
            stop.set()
            if failure == "notification":
                raise RuntimeError("private-dispatch-detail")
            return None

    settings = callback_settings()
    settings.feishu_notifications_enabled = True
    notifications.run_task_notifications(QueueStore(), settings, stop)
    assert received == (["confirmation", "notification"] if failure == "confirmation" else ["confirmation", "expired-claim-ignored", "notification"])
    assert "private-dispatch-detail" not in caplog.text


@pytest.mark.parametrize("missing", ["feishu_app_id", "feishu_app_secret"])
def test_dispatcher_without_app_configuration_does_not_claim_a_command(missing):
    settings = callback_settings()
    setattr(settings, missing, "")
    assert notifications.dispatch_task_confirmation(object(), object(), settings, "test-worker") is False
