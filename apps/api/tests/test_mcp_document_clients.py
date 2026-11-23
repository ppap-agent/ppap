"""A client named by its own metadata document (#356) may use only the callbacks that document declares."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from api.mcp_auth import OAuthProvider
from mcp.shared.auth import InvalidRedirectUriError
from product.mcp import read_client_document
from product.settings import Settings
from pydantic import AnyUrl

URL = "https://client.example/ppap/client.json"
CALLBACK = "http://127.0.0.1:4567/callback"


class Clients:
    """PPAP's copies of client records: none yet, and whatever was read is kept."""

    settings = Settings(public_api_url="https://ppap.example/ppap")

    def __init__(self):
        self.saved = {}

    def client(self, client_id):
        return self.saved.get(client_id)

    def save_document_client(self, client_id, data):
        self.saved[client_id] = data


def test_only_callbacks_the_document_declares_are_accepted():
    document = {"client_id": URL, "client_name": "Doc Client", "redirect_uris": [CALLBACK]}
    network = httpx.MockTransport(lambda request: httpx.Response(200, content=json.dumps(document).encode()))
    provider = OAuthProvider(Clients(), SimpleNamespace())
    provider.read_document = lambda url: read_client_document(url, resolve=lambda host, port: ["93.184.216.34"], transport=network)
    client = asyncio.run(provider.get_client(URL))
    assert str(client.validate_redirect_uri(AnyUrl(CALLBACK))) == CALLBACK
    with pytest.raises(InvalidRedirectUriError):
        client.validate_redirect_uri(AnyUrl("http://127.0.0.1:4567/elsewhere"))
