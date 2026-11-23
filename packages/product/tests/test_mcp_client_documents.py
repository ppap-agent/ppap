"""A client may name itself by the HTTPS address of its own metadata document (#356). PPAP reads that document with the
same care as a tenant's model address, and keeps it as the client's record."""

import json
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from product.mcp import DOCUMENT, DOCUMENT_MAX_TTL, McpError, document_fresh, fresh_until, read_client_document

URL = "https://client.example/ppap/client.json"
PUBLIC = "93.184.216.34"
CALLBACK = "http://127.0.0.1:4567/callback"


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
