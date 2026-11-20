"""JSAPI signing through the actual route, without a database or Feishu."""

import hashlib
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
from api.feishu_jsapi_routes import register_feishu_jsapi_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from product.feishu_jsapi import JsapiTickets, signed_page

ORIGIN = "https://ppap.example.com"


class TicketStore:
    def app_ticket(self, app_id):
        assert app_id == "cli_test"
        return "app-ticket"


@contextmanager
def jsapi_client(feishu, *, signed_in=True, clock=lambda: 0.0):
    principal = SimpleNamespace(provider="feishu", app_id="cli_test", tenant_key="tenant-one")
    settings = SimpleNamespace(feishu_app_id="cli_test", feishu_app_secret="test-secret", canonical_web_url=lambda: ORIGIN)
    app = FastAPI()
    with httpx.Client(transport=httpx.MockTransport(feishu)) as http:
        register_feishu_jsapi_routes(app, TicketStore(), lambda _request: SimpleNamespace(principal=principal) if signed_in else None, http, settings, JsapiTickets(clock))
        with TestClient(app) as client:
            yield client


def feishu_ok(calls):
    def handle(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/auth/v3/app_access_token"):
            return httpx.Response(200, json={"code": 0, "app_access_token": "app-token"})
        if request.url.path.endswith("/auth/v3/tenant_access_token"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        assert request.url.path.endswith("/jssdk/ticket/get")
        assert request.headers["authorization"] == "Bearer tenant-token"
        return httpx.Response(200, json={"code": 0, "data": {"ticket": "jsapi-ticket", "expire_in": 7200}})

    return handle


def test_signs_our_page_without_its_fragment_and_never_returns_the_ticket():
    calls = []
    with jsapi_client(feishu_ok(calls)) as client:
        reply = client.get("/integrations/feishu/jsapi-config", params={"url": ORIGIN + "/tasks/new?x=1#files"})
    assert reply.status_code == 200
    body = reply.json()
    assert set(body) == {"app_id", "timestamp", "nonce_str", "signature"} and body["app_id"] == "cli_test"
    expected = hashlib.sha1(f"jsapi_ticket=jsapi-ticket&noncestr={body['nonce_str']}&timestamp={body['timestamp']}&url={ORIGIN}/tasks/new?x=1".encode()).hexdigest()
    assert body["signature"] == expected
    assert "jsapi-ticket" not in reply.text
    assert reply.headers["cache-control"] == "private, no-store"


def test_the_ticket_is_fetched_once_per_tenant_until_shortly_before_it_expires():
    calls, now = [], [0.0]
    with jsapi_client(feishu_ok(calls), clock=lambda: now[0]) as client:
        for _ in range(3):
            assert client.get("/integrations/feishu/jsapi-config", params={"url": ORIGIN + "/"}).status_code == 200
        assert calls.count("/open-apis/jssdk/ticket/get") == 1
        now[0] = 7200.0
        assert client.get("/integrations/feishu/jsapi-config", params={"url": ORIGIN + "/"}).status_code == 200
    assert calls.count("/open-apis/jssdk/ticket/get") == 2


def test_other_sites_and_signed_out_visitors_get_no_signature():
    calls = []
    with jsapi_client(feishu_ok(calls)) as client:
        for url in ("https://evil.example.com/", "http://ppap.example.com/", "https://user:pw@ppap.example.com/", ""):
            assert client.get("/integrations/feishu/jsapi-config", params={"url": url}).json() == {"error": "invalid_jsapi_url"}
    with jsapi_client(feishu_ok(calls), signed_in=False) as client:
        assert client.get("/integrations/feishu/jsapi-config", params={"url": ORIGIN + "/"}).status_code == 401
    assert calls == []


def test_a_feishu_refusal_is_reported_as_unavailable():
    def refused(request):
        if request.url.path.endswith("/jssdk/ticket/get"):
            return httpx.Response(200, json={"code": 99991401, "msg": "ip denied"})
        return feishu_ok([])(request)

    with jsapi_client(refused) as client:
        reply = client.get("/integrations/feishu/jsapi-config", params={"url": ORIGIN + "/"})
    assert (reply.status_code, reply.json()) == (503, {"error": "feishu_jsapi_unavailable"})


def test_signed_page_keeps_the_query_and_drops_the_fragment():
    assert signed_page(ORIGIN + "/a/b?c=1&d=%E4%B8%AD#x", ORIGIN) == ORIGIN + "/a/b?c=1&d=%E4%B8%AD"
    assert signed_page(ORIGIN + ".evil.com/", ORIGIN) is None
