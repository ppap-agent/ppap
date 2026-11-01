"""Real browser form navigation and PKCE over HTTP/PG; Feishu identity is a fixture."""

import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
import uvicorn
from api import create_app
from product.mcp import pkce_challenge

playwright = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize("callback_host", ["127.0.0.1", "localhost", "[::1]"])
@pytest.mark.parametrize("approve", [True, False])
def test_browser_consent_reaches_registered_callback_with_strict_csp(mcp_app, callback_host, approve):
    _, store = mcp_app
    received = []

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(parse_qs(urlsplit(self.path).query))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Client callback received")

        def log_message(self, *args):
            pass  # Never log authorization codes.

    class CallbackServer(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if callback_host == "[::1]" else socket.AF_INET

    try:
        callback_server = CallbackServer(("::1" if callback_host == "[::1]" else "127.0.0.1", 0), Callback)
    except OSError:
        if callback_host == "[::1]":
            pytest.skip("IPv6 loopback unavailable")
        raise
    callback = f"http://{callback_host}:{callback_server.server_port}/callback?kept=1"
    callback_thread = threading.Thread(target=callback_server.serve_forever, daemon=True)
    callback_thread.start()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    base = "http://127.0.0.1:" + str(sock.getsockname()[1])
    store.settings.public_api_url = base
    server = uvicorn.Server(uvicorn.Config(create_app(store.settings, auth_store=store), log_level="critical", access_log=False))
    server_thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    server_thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        with httpx.Client(base_url=base) as client, playwright.sync_playwright() as pw:
            registered = client.post("/oauth/register", json={"client_name": "Browser callback test", "redirect_uris": [callback], "token_endpoint_auth_method": "none"})
            assert registered.status_code == 201
            client_id = registered.json()["client_id"]
            verifier = secrets.token_urlsafe(48)
            state = "browser-state&=中文"
            params = {
                "client_id": client_id,
                "redirect_uri": callback,
                "response_type": "code",
                "code_challenge_method": "S256",
                "code_challenge": pkce_challenge(verifier),
                "resource": base + "/mcp",
                "scope": "identity:read",
                "state": state,
            }
            browser = pw.chromium.launch()
            try:
                context = browser.new_context()
                context.add_cookies([{"name": "ppap_session", "value": store.create_session(store.principal_for_fixture("alice")), "url": base}])
                page = context.new_page()
                page.goto(base + "/oauth/authorize?" + urlencode(params))
                with page.expect_response(lambda r: "/oauth/consent/" in r.url and r.request.method == "POST") as decision:
                    page.get_by_role("button", name="允许所选权限" if approve else "拒绝", exact=True).click()
                page.wait_for_url(callback + "**", wait_until="domcontentloaded")
                assert len(received) == 1
                assert received[0]["state"] == [state] and received[0]["kept"] == ["1"]
                assert decision.value.status == 200
                assert "form-action 'self';" in decision.value.headers["content-security-policy"]
                assert decision.value.headers["cache-control"] == "no-store"
                assert decision.value.headers["referrer-policy"] == "no-referrer"
                if not approve:
                    assert received[0]["error"] == ["access_denied"] and "code" not in received[0]
                    return
                exchange = {"grant_type": "authorization_code", "client_id": client_id, "redirect_uri": callback, "code": received[0]["code"][0], "code_verifier": verifier, "resource": base + "/mcp"}
                token = client.post("/oauth/token", data=exchange)
                assert token.status_code == 200
                assert token.json()["scope"] == "identity:read"
                assert client.post("/oauth/token", data=exchange).status_code == 400
            finally:
                browser.close()
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)
        sock.close()
        callback_server.shutdown()
        callback_server.server_close()
        callback_thread.join(timeout=5)
