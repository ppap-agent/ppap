"""Who an MCP client is (#356): a platform-registered web client, a client named by its own HTTPS metadata document, or
only what it says of itself. A document is read with the same care as a tenant's model address."""

import json
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull
from product.mcp import DOCUMENT, DOCUMENT_MAX_TTL, McpError, client_identity, document_fresh, fresh_until, read_client_document
from product.settings import Settings, VerifiedClient
from pydantic import AnyUrl, ValidationError

URL = "https://client.example/ppap/client.json"
PUBLIC = "93.184.216.34"
CALLBACK = "http://127.0.0.1:4567/callback"
REGISTERED = Settings().mcp_verified_clients


def document(**overrides):
    return {"client_id": URL, "client_name": "Doc Client", "redirect_uris": [CALLBACK], **overrides}


def serve(handler, *, addresses=(PUBLIC,)):
    """Read URL from a fake network: `handler` answers each request, `addresses` is what the name resolves to."""
    seen = []

    def respond(request):
        seen.append(request)
        return handler(request)

    def resolve(host, port):
        assert (host, port) == ("client.example", 443)
        return list(addresses)

    return lambda url=URL: read_client_document(url, resolve=resolve, transport=httpx.MockTransport(respond)), seen


def json_response(body, **headers):
    return lambda request: httpx.Response(200, content=json.dumps(body).encode(), headers=headers)


def test_a_document_naming_its_own_address_becomes_the_clients_record():
    read, seen = serve(json_response(document(), **{"Cache-Control": "max-age=120"}))
    record = read()
    assert record["client_id"] == URL and record["client_name"] == "Doc Client" and record["redirect_uris"] == [CALLBACK]
    assert record["token_endpoint_auth_method"] == "none" and record["grant_types"] == ["authorization_code", "refresh_token"]
    assert 115 <= record[DOCUMENT]["fresh_until"] - time.time() <= 120
    # The connection goes to the address that was checked, under the document's own name.
    (request,) = seen
    assert request.url.host == PUBLIC and request.url.path == "/ppap/client.json" and request.headers["host"] == "client.example"
    assert request.extensions["sni_hostname"] == "client.example"


def test_a_document_must_name_exactly_its_own_address():
    for other in (URL + "/", "https://client.example/ppap/other.json", URL.upper(), None):
        read, _ = serve(json_response(document(client_id=other)))
        with pytest.raises(McpError, match="client_document_mismatch"):
            read()


@pytest.mark.parametrize(
    "url", ["http://client.example/ppap/client.json", "https://client.example", "https://client.example/", "https://localhost/client.json", "https://client.example/a/../client.json"]
)
def test_only_a_canonical_https_address_with_a_path_is_read(url):
    read, seen = serve(json_response(document(client_id=url)))
    with pytest.raises(McpError, match="invalid_client_id"):
        read(url)
    assert not seen


@pytest.mark.parametrize("addresses", [("10.0.0.5",), ("127.0.0.1",), ("169.254.169.254",), ("::1",), ("::ffff:10.0.0.1",), ("64:ff9b::a00:1",), (PUBLIC, "192.168.1.2"), ()])
def test_a_name_resolving_to_a_private_or_local_address_is_not_read(addresses):
    read, seen = serve(json_response(document()), addresses=addresses)
    with pytest.raises(McpError, match="client_document_address"):
        read()
    assert not seen


def test_a_private_address_written_as_the_host_is_not_read():
    url = "https://10.1.2.3/client.json"
    with pytest.raises(McpError, match="client_document_address"):
        read_client_document(url, transport=httpx.MockTransport(lambda request: pytest.fail("fetched a private address")))


def test_an_oversized_document_is_refused():
    big = document(client_name="x" * 100, contacts=["a" * 6000])
    read, _ = serve(json_response(big))
    with pytest.raises(McpError, match="client_document_too_large"):
        read()
    # Without a declared length the body is cut off as it streams.
    read, _ = serve(lambda request: httpx.Response(200, content=iter([b"{" + b" " * 4096, b" " * 4096])))
    with pytest.raises(McpError, match="client_document_too_large"):
        read()


def test_a_slow_document_times_out():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    read, _ = serve(slow)
    with pytest.raises(McpError, match="client_document_timeout"):
        read()


def test_a_redirect_is_not_followed():
    read, seen = serve(lambda request: httpx.Response(302, headers={"Location": "https://elsewhere.example/client.json"}))
    with pytest.raises(McpError, match="client_document_unavailable"):
        read()
    assert len(seen) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"redirect_uris": []},
        {"redirect_uris": ["http://callback.example/cb"]},
        {"client_secret": "shared"},
        {"token_endpoint_auth_method": "client_secret_post"},
        {"grant_types": ["client_credentials"]},
        {"client_name": None},
    ],
)
def test_a_document_without_usable_callbacks_or_with_a_secret_is_refused(overrides):
    read, _ = serve(json_response(document(**overrides)))
    with pytest.raises(McpError, match="invalid_client_metadata"):
        read()


def test_only_callbacks_the_document_declares_are_accepted():
    read, _ = serve(json_response(document()))
    client = OAuthClientInformationFull.model_validate(read())
    assert str(client.validate_redirect_uri(AnyUrl(CALLBACK))) == CALLBACK
    with pytest.raises(InvalidRedirectUriError):
        client.validate_redirect_uri(AnyUrl("http://127.0.0.1:4567/elsewhere"))


def test_a_read_document_holds_as_its_cache_headers_say():
    now = 1_800_000_000.0
    assert fresh_until({}, now) == now + 3600
    assert fresh_until({"cache-control": "max-age=60"}, now) == now + 60
    assert fresh_until({"cache-control": "public, max-age=999999999"}, now) == now + DOCUMENT_MAX_TTL
    assert fresh_until({"cache-control": "no-store"}, now) == now
    assert fresh_until({"cache-control": "no-cache, max-age=60"}, now) == now
    later = datetime.fromtimestamp(now, UTC) + timedelta(minutes=10)
    assert fresh_until({"expires": format_datetime(later, usegmt=True)}, now) == now + 600
    assert fresh_until({"expires": "0"}, now) == now
    assert document_fresh({DOCUMENT: {"fresh_until": now + 1}}, now) and not document_fresh({DOCUMENT: {"fresh_until": now}}, now) and not document_fresh({}, now)


def test_a_preregistered_web_client_is_shown_as_verified_under_its_registered_name():
    for callback in ("https://chatgpt.com/connector/oauth/yjAMZ-kP_fvM", "https://chatgpt.com/connector_platform_oauth_redirect"):
        identity = client_identity("dcr-id", {"client_name": "probe", "redirect_uris": [callback]}, REGISTERED)
        assert identity == {"level": "verified", "name": "ChatGPT", "domain": "chatgpt.com", "label": "ChatGPT · 已验证（chatgpt.com）"}
    claude = client_identity("dcr-id", {"client_name": "probe", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]}, REGISTERED)
    assert claude["label"] == "Claude · 已验证（claude.ai）"


@pytest.mark.parametrize(
    "callbacks",
    [
        ["https://chatgpt.com/connector/oauth/yjAMZ-kP_fvM/../../share"],
        ["https://chatgpt.com/connector/oauth/x?next=https://evil.example"],
        ["https://chatgpt.com/connector/oauth/"],
        ["https://chatgpt.com.evil.example/connector/oauth/x"],
        ["https://chatgpt.com:8443/connector/oauth/x"],
        ["https://chatgpt.com/connector_platform_oauth_redirect/more"],
        ["https://chatgpt.com/connector/oauth/x", "https://evil.example/callback"],
        ["https://chatgpt.com/connector/oauth/x", "http://127.0.0.1:1/callback"],
    ],
)
def test_a_client_whose_callbacks_are_not_all_registered_is_not_shown_as_verified(callbacks):
    identity = client_identity("dcr-id", {"client_name": "ChatGPT", "redirect_uris": callbacks}, REGISTERED)
    assert identity["level"] == "unverified" and identity["label"] == "未验证（自称：ChatGPT）"


def test_a_registered_client_is_shown_by_what_it_says_of_itself():
    identity = client_identity("dcr-id", {"client_name": "PPAP 官方助手", "redirect_uris": ["https://app.example/callback"]}, REGISTERED)
    assert identity == {"level": "unverified", "name": "PPAP 官方助手", "domain": None, "label": "未验证（自称：PPAP 官方助手）"}
    assert client_identity("dcr-id", {"client_name": None, "redirect_uris": ["https://app.example/callback"]}, REGISTERED)["label"] == "未验证（自称：MCP 客户端）"


@pytest.mark.parametrize("callback", ["http://127.0.0.1:54121/callback", "http://localhost:8080/cb", "http://[::1]:9/cb"])
def test_a_client_calling_back_to_this_machine_is_a_local_app_of_unknown_origin(callback):
    identity = client_identity("dcr-id", {"client_name": "Pi MCP test", "redirect_uris": [callback]}, REGISTERED)
    assert identity["level"] == "local" and identity["label"] == "本机应用 · 无法验证来源（自称：Pi MCP test）"


def test_a_document_client_is_shown_with_the_documents_domain():
    read, _ = serve(json_response(document()))
    record = read()
    assert client_identity(URL, record, REGISTERED) == {"level": "metadata", "name": "Doc Client", "domain": "client.example", "label": "Doc Client · 来自 client.example"}
    # The record of a registration cannot pass as a document, nor a document as another client.
    assert client_identity("dcr-id", {**record, "client_id": "dcr-id"}, REGISTERED)["level"] == "local"
    assert client_identity("https://client.example/other.json", record, REGISTERED)["level"] == "local"
    # A platform registration still comes first.
    chatgpt = {**record, "redirect_uris": ["https://chatgpt.com/connector/oauth/abc"]}
    assert client_identity(URL, chatgpt, REGISTERED)["level"] == "verified"


def test_verified_clients_are_configured_with_callbacks_on_their_own_domain(monkeypatch):
    monkeypatch.setenv("PPAP_MCP_VERIFIED_CLIENTS", json.dumps([{"name": "Agent", "domain": "agent.example", "redirect_uris": ["https://app.agent.example/oauth/*"]}]))
    (entry,) = Settings().mcp_verified_clients
    assert client_identity("x", {"client_name": "y", "redirect_uris": ["https://app.agent.example/oauth/abc"]}, [entry])["label"] == "Agent · 已验证（agent.example）"
    for callbacks in (["https://evil.example/cb"], ["http://agent.example/cb"], ["https://agent.example:444/cb"], ["https://agent.example/*/cb"], ["https://agent.example/cb?x=1"]):
        with pytest.raises(ValidationError):
            VerifiedClient(name="Agent", domain="agent.example", redirect_uris=tuple(callbacks))
