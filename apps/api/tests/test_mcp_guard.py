"""The MCP entry checks content type, Host and Origin only on requests that carry a token."""

from types import SimpleNamespace

from api.mcp_server import McpRequestGuard
from fastapi.testclient import TestClient


def guarded():
    async def auth_layer(scope, receive, send):
        token = dict(scope["headers"]).get(b"authorization")
        status = 200 if token else 401
        await send({"type": "http.response.start", "status": status, "headers": [] if token else [(b"www-authenticate", b'Bearer resource_metadata="x"')]})
        await send({"type": "http.response.body", "body": b""})

    settings = SimpleNamespace(web_origins=lambda: ["https://ppap.example"], mcp_allowed_origins=[])
    provider = SimpleNamespace(base="https://ppap.example/ppap", settings=settings, store=SimpleNamespace(rate_limit=lambda *args: None))
    return TestClient(McpRequestGuard(auth_layer, provider), base_url="https://ppap.example")


def test_probe_without_token_in_any_content_type_reaches_the_auth_challenge():
    response = guarded().post("/mcp", headers={"Content-Type": "application/octet-stream"}, content=b"")
    assert response.status_code == 401 and "resource_metadata" in response.headers["www-authenticate"]


def test_requests_with_a_token_still_need_json_and_an_allowed_origin():
    client = guarded()
    bearer = {"Authorization": "Bearer t", "Accept": "application/json, text/event-stream"}
    assert client.post("/mcp", headers={**bearer, "Content-Type": "application/octet-stream"}, content=b"{}").status_code == 400
    assert client.post("/mcp", headers={**bearer, "Content-Type": "application/json", "Origin": "https://attacker.example"}, content=b"{}").status_code == 403
    assert client.post("/mcp", headers={**bearer, "Content-Type": "application/json"}, content=b"{}").status_code == 200
