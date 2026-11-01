"""OAuth return paths use real state persistence and controlled provider HTTP."""

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from api import create_app
from fastapi.testclient import TestClient
from product.feishu import UserTokens
from product.identity import SESSION_COOKIE
from product.settings import Settings


@pytest.mark.parametrize(
    ("callback", "reason"),
    [
        ({"code": "must-not-exchange", "error": "access_denied"}, "cancelled"),
        ({"error": "private-provider-detail"}, "failed"),
        ({}, "failed"),
    ],
)
def test_incomplete_authorization_returns_to_the_original_task_without_granting_a_session(auth_store_factory, callback, reason):
    settings = Settings(feishu_app_id="login-recovery-" + uuid.uuid4().hex, feishu_app_secret="test-secret", web_url="http://127.0.0.1:4173")
    store = auth_store_factory(settings, reset=False, role="api")

    def provider(_request):
        raise AssertionError("Cancelled authorization must not exchange a code")

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(settings, auth_store=store, http=http)) as client:
        dest = "http://127.0.0.1:4173/__audit-native?task=original-task&view=usage&report=1&auth_error=old#original-clause"
        start = client.get("/login/feishu", params={"return_to": dest}, follow_redirects=False)
        assert start.status_code == 302
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        response = client.get(
            "/login/feishu/callback",
            params={"state": state, **callback, "return_to": "https://foreign.example/task"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"http://127.0.0.1:4173/__audit-native?task=original-task&view=usage&report=1&auth_error={reason}#original-clause"
        assert not response.cookies.get(SESSION_COOKIE)
        assert client.get("/me").status_code == 401
        replay = client.get("/login/feishu/callback", params={"state": state, "code": "must-not-exchange"}, follow_redirects=False)
        assert replay.headers["location"] == "http://127.0.0.1:4173/"


@pytest.mark.parametrize("failure", ["exchange", "timeout", "invalid_json", "non_object", "userinfo", "userinfo_timeout", "userinfo_json", "userinfo_non_object", "denied"])
def test_provider_or_tenant_failure_returns_to_the_frozen_destination_without_a_session(auth_store_factory, failure):
    settings = Settings(
        feishu_app_id="login-recovery-" + uuid.uuid4().hex,
        feishu_app_secret="test-secret",
        web_url="http://127.0.0.1:4173",
        current_tenant_allowlist="permitted-test-tenant" if failure == "denied" else "",
    )
    store = auth_store_factory(settings, reset=False, role="api")

    def provider(request):
        if request.method == "POST":
            if failure == "timeout":
                raise httpx.ReadTimeout("controlled provider timeout", request=request)
            if failure == "exchange":
                return httpx.Response(400, json={"code": 1, "msg": "private-provider-detail"})
            if failure == "invalid_json":
                return httpx.Response(502, content=b"not JSON")
            if failure == "non_object":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={"code": 0, "access_token": "test-access", "expires_in": 3600})
        if failure == "userinfo":
            return httpx.Response(401, json={"code": 1, "msg": "private-provider-detail"})
        if failure == "userinfo_timeout":
            raise httpx.ReadTimeout("controlled provider timeout", request=request)
        if failure == "userinfo_json":
            return httpx.Response(502, content=b"not JSON")
        if failure == "userinfo_non_object":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"data": {"tenant_key": "not-permitted-test-tenant", "open_id": "test-user", "name": "Test"}})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(settings, auth_store=store, http=http)) as client:
        dest = "http://127.0.0.1:4173/__audit-native?task=task-before-login&report=1#clause-before-login"
        start = client.get("/login/feishu", params={"return_to": dest}, follow_redirects=False)
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        response = client.get("/login/feishu/callback", params={"state": state, "code": "test-code"}, follow_redirects=False)
        assert response.status_code == 303
        reason = "tenant_not_allowed" if failure == "denied" else "failed"
        assert response.headers["location"] == f"http://127.0.0.1:4173/__audit-native?task=task-before-login&report=1&auth_error={reason}#clause-before-login"
        assert not response.cookies.get(SESSION_COOKIE)
        assert client.get("/me").status_code == 401


def test_success_keeps_the_deep_link_and_untrusted_states_never_supply_a_return_path(auth_store_factory):
    settings = Settings(feishu_app_id="login-recovery-" + uuid.uuid4().hex, feishu_app_secret="test-secret", web_url="http://127.0.0.1:4173")
    store = auth_store_factory(settings, reset=False, role="api")
    tenant = "login-recovery-tenant-" + uuid.uuid4().hex

    def provider(request):
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "access_token": "test-access", "expires_in": 3600})
        return httpx.Response(200, json={"data": {"tenant_key": tenant, "open_id": "test-user", "name": "Test"}})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(settings, auth_store=store, http=http)) as client:
        dest = "http://127.0.0.1:4173/__audit-native?task=original-task&view=usage&report=1#original-clause"
        start = client.get("/login/feishu", params={"return_to": dest}, follow_redirects=False)
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        response = client.get("/login/feishu/callback", params={"state": state, "code": "test-code"}, follow_redirects=False)
        assert response.headers["location"] == dest
        assert response.cookies.get(SESSION_COOKIE)
        assert client.get("/me").json()["identity"] == {"provider": "feishu", "app_id": settings.feishu_app_id, "tenant_key": tenant, "subject": "test-user"}
        for candidate in (state, "forged-state", ""):
            refused = client.get("/login/feishu/callback", params={"state": candidate, "code": "test-code", "return_to": dest}, follow_redirects=False)
            assert refused.headers["location"] == "http://127.0.0.1:4173/"
            assert not refused.cookies.get(SESSION_COOKIE)
        foreign = client.get("/login/feishu", params={"return_to": "https://foreign.example/task"}, follow_redirects=False)
        foreign_state = parse_qs(urlsplit(foreign.headers["location"]).query)["state"][0]
        refused = client.get("/login/feishu/callback", params={"state": foreign_state, "error": "access_denied"}, follow_redirects=False)
        assert refused.headers["location"] == "http://127.0.0.1:4173/?auth_error=cancelled"


@pytest.mark.parametrize("failure", ["timeout", "invalid_json", "non_object", "invalid_credentials", "connect_timeout"])
def test_provider_refresh_retries_only_when_transport_proves_it_was_not_sent(auth_store_factory, failure):
    settings = Settings(feishu_app_id="login-recovery-" + uuid.uuid4().hex, feishu_app_secret="test-secret")
    store = auth_store_factory(settings, reset=False, role="api")
    principal = store.upsert_feishu_identity("login-recovery-tenant-" + uuid.uuid4().hex, "test-user")
    now = datetime.now(timezone.utc)
    raw = store.create_session(principal, UserTokens("test-expired-access", "test-refresh", now - timedelta(seconds=2), now + timedelta(days=1)))
    recovered = False

    def provider(request):
        assert request.method == "POST"
        if not recovered:
            if failure == "connect_timeout":
                raise httpx.ConnectTimeout("controlled failure before sending", request=request)
            if failure == "timeout":
                raise httpx.ReadTimeout("controlled provider timeout", request=request)
            if failure == "invalid_json":
                return httpx.Response(502, content=b"not JSON")
            if failure == "non_object":
                return httpx.Response(200, json=[])
            return httpx.Response(400, json={"code": 1, "msg": "invalid refresh credentials"})
        return httpx.Response(200, json={"code": 0, "access_token": "test-refreshed", "expires_in": 3600, "refresh_token": "test-next-refresh", "refresh_token_expires_in": 86400})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(settings, auth_store=store, http=http), raise_server_exceptions=False) as client:
        client.cookies.set(SESSION_COOKIE, raw)
        first = client.get("/me")
        assert first.status_code == (503 if failure == "connect_timeout" else 401)
        recovered = True
        second = client.get("/me")
        if failure == "connect_timeout":
            assert first.headers["retry-after"] == "5"
            assert second.status_code == 200
            assert second.json()["user_id"] == principal.user_id
        else:
            # A lost/malformed response may already have consumed the rotating
            # credential. Recovery is a new login, not replay of the old token.
            assert second.status_code == 401
