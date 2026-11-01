"""Directory HTTP budgets through actual routes, without a database or Feishu."""

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from api import web_contract as wc
from api.sharing_routes import register_sharing_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter


class NoDatabaseStore:
    def __init__(self):
        self.database_probes = 0
        self.ticket_reads = 0

    def app_ticket(self, app_id):
        assert app_id == "cli_test"
        self.ticket_reads += 1
        return "test-ticket"

    def _conn(self):
        self.database_probes += 1
        raise KeyError("missing")


@contextmanager
def directory_client(provider):
    store = NoDatabaseStore()
    principal = SimpleNamespace(provider="feishu", app_id="cli_test", tenant_key="tenant-one", subject="ou_self", display_name="自己")
    session = SimpleNamespace(principal=principal, access_token="user-token")
    settings = SimpleNamespace(feishu_app_id="cli_test", feishu_app_secret="test-secret")
    app = FastAPI()
    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        register_sharing_routes(app, store, lambda _request: session, http, settings)
        with TestClient(app) as client:
            yield client, store


def provider_body(path, *, kind="user", count=20):
    if path.endswith("/authen/v1/user_info"):
        return {"code": 0, "data": {"open_id": "ou_self", "tenant_key": "tenant-one", "name": "已核验本人"}}
    if path.endswith("/auth/v3/app_access_token"):
        return {"code": 0, "app_access_token": "test-app-token"}
    if path.endswith("/auth/v3/tenant_access_token"):
        return {"code": 0, "tenant_access_token": "test-tenant-token"}
    if path.endswith("/search/v1/user") or path.endswith("/im/v1/chats/search"):
        key, rows_key, prefix = ("open_id", "users", "ou_") if kind == "user" else ("chat_id", "items", "oc_")
        return {"code": 0, "data": {rows_key: [{key: prefix + str(index)} for index in range(count)], "has_more": True, "page_token": "next-page"}}
    if "/contact/v3/users/" in path:
        return {
            "code": 0,
            "data": {"user": {"open_id": path.rsplit("/", 1)[1], "name": "成员", "status": {"is_activated": True, "is_resigned": False, "is_frozen": False, "is_exited": False, "is_unjoin": False}}},
        }
    if "/im/v1/chats/" in path:
        return {"code": 0, "data": {"name": "内部群", "external": False, "tenant_key": "tenant-one", "chat_mode": "group"}}
    raise AssertionError("unexpected provider request")


@pytest.mark.parametrize("late_stage", ["/authen/v1/user_info", "/auth/v3/app_access_token", "/auth/v3/tenant_access_token", "/search/v1/user"])
@pytest.mark.parametrize("transport_timeout", [False, True], ids=["late-response", "transport-timeout"])
def test_directory_profile_token_and_search_share_one_budget(monkeypatch, late_stage, transport_timeout):
    clock, calls = [0.0], []
    monkeypatch.setattr("product.report_sharing.monotonic", lambda: clock[0])

    def provider(request):
        calls.append(request)
        if request.url.path.endswith(late_stage):
            clock[0] += 31
            if transport_timeout:
                raise httpx.ReadTimeout("private token or directory diagnostic", request=request)
        return httpx.Response(200, json=provider_body(request.url.path))

    with directory_client(provider) as (client, store):
        response = client.get("/audit-share-targets", params={"kind": "user", "query": "查询"})
    assert response.status_code == 503
    assert response.json() == {"error": "share_directory_timeout"}
    assert response.headers["cache-control"] == "private, no-store"
    assert calls[-1].url.path.endswith(late_stage)
    assert all("/contact/v3/users/" not in request.url.path for request in calls)
    assert store.database_probes == 0


@pytest.mark.parametrize("kind", ["user", "group"])
def test_directory_partial_read_preserves_order_and_unverified_targets(monkeypatch, kind):
    clock, calls = [0.0], []
    monkeypatch.setattr("product.report_sharing.monotonic", lambda: clock[0])

    def provider(request):
        calls.append(request)
        clock[0] += 1 if len(calls) <= 4 else 15 if len(calls) == 5 else 12
        return httpx.Response(200, json=provider_body(request.url.path, kind=kind))

    with directory_client(provider) as (client, store):
        response = client.get("/audit-share-targets", params={"kind": kind, "query": "查询"})
    assert response.status_code == 200, response.text
    assert len(calls) == 6
    assert [request.extensions["timeout"]["read"] for request in calls] == [20, 20, 20, 20, 20, 11]
    page = response.json()
    TypeAdapter(wc.AuditShareTargetsPage).validate_python(page)
    prefix = "ou_" if kind == "user" else "oc_"
    assert [target["id"] for target in page["targets"]] == [prefix + str(index) for index in range(20)]
    assert page["targets"][0]["eligibility"] == "eligible"
    assert all(target["eligibility"] == "unknown" and "超时" in target["reason"] for target in page["targets"][1:])
    assert page["next_page_token"] == "next-page"
    assert store.database_probes == 0


def test_a_new_explicit_directory_read_gets_a_new_budget_and_verified_self(monkeypatch):
    clock, calls = [0.0], []
    monkeypatch.setattr("product.report_sharing.monotonic", lambda: clock[0])

    def provider(request):
        calls.append(request)
        if len(calls) == 1:
            clock[0] += 31
        return httpx.Response(200, json=provider_body(request.url.path))

    with directory_client(provider) as (client, store):
        failed = client.get("/audit-share-targets")
        assert failed.status_code == 503 and failed.json() == {"error": "share_directory_timeout"}
        assert len(calls) == 1
        recovered = client.get("/audit-share-targets")
    assert recovered.status_code == 200
    assert len(calls) == 2
    assert recovered.json()["targets"] == [{"id": "ou_self", "kind": "user", "display_name": "已核验本人", "organization_id": "tenant-one", "eligibility": "eligible", "reason": None}]
    assert store.ticket_reads == store.database_probes == 0


@pytest.mark.parametrize(
    "status,code,expected",
    [(400, 99991672, "share_directory_permission_missing"), (400, 99991679, "share_directory_user_permission_missing"), (503, 99991679, "share_unavailable"), (503, 99991672, "share_unavailable")],
)
def test_directory_budget_preserves_permission_and_provider_failure_errors(monkeypatch, status, code, expected):
    monkeypatch.setattr("product.report_sharing.monotonic", lambda: 0.0)

    def provider(request):
        if request.url.path.endswith("/search/v1/user"):
            return httpx.Response(status, json={"code": code, "msg": "private provider details"})
        return httpx.Response(200, json=provider_body(request.url.path))

    with directory_client(provider) as (client, _store):
        response = client.get("/audit-share-targets", params={"query": "查询"})
    assert response.status_code == 503 and response.json() == {"error": expected}


@pytest.mark.parametrize("changed", [{"tenant_key": "other-tenant"}, {"open_id": "ou_other"}])
def test_directory_budget_does_not_replace_identity_validation(monkeypatch, changed):
    monkeypatch.setattr("product.report_sharing.monotonic", lambda: 0.0)
    calls = []

    def provider(request):
        calls.append(request)
        body = provider_body(request.url.path)
        body["data"].update(changed)
        return httpx.Response(200, json=body)

    with directory_client(provider) as (client, store):
        response = client.get("/audit-share-targets", params={"query": "查询"})
    assert response.status_code == 403 and response.json() == {"error": "access_denied"}
    assert len(calls) == 1
    assert store.ticket_reads == store.database_probes == 0


@pytest.mark.parametrize("operation", ["send", "read", "retry"])
def test_directory_budget_is_not_applied_to_delivery_or_receipt_routes(monkeypatch, operation):
    clock, calls = [0.0], []
    monkeypatch.setattr("product.report_sharing.monotonic", lambda: clock[0])

    def provider(request):
        calls.append(request)
        clock[0] += 35
        return httpx.Response(200, json=provider_body(request.url.path))

    request_id = str(uuid.uuid4())
    with directory_client(provider) as (client, store):
        if operation == "send":
            reference = {key: str(uuid.uuid4()) for key in ("task_id", "round_id", "file_version_id", "report_id")}
            response = client.post(
                "/audit-report-shares",
                json={
                    "request_id": request_id,
                    "organization_id": "tenant-one",
                    "reports": [{**reference, "result_attempt": 1}],
                    "targets": [{"id": "ou_reader", "kind": "user"}],
                    "content": {"kind": "link", "artifact": None, "package_id": None},
                },
            )
        elif operation == "read":
            response = client.get("/audit-report-shares/" + request_id)
        else:
            response = client.post(f"/audit-report-shares/{request_id}/retries", json={"target": {"id": "ou_reader", "kind": "user"}, "attempt_id": str(uuid.uuid4())})
    # These deny-only DB boundaries do not send messages. Reaching them after
    # the slow profile proves the directory-only budget did not alter a write.
    assert response.status_code == 403 and response.json() == {"error": "access_denied"}
    assert len(calls) == 1 and calls[0].extensions["timeout"]["read"] == 20
    assert store.database_probes == 1 and store.ticket_reads == 0
