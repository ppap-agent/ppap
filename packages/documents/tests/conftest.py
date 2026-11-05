"""Owner tests may use loopback services or explicit transports, never live OCR."""

from urllib.parse import urlsplit

import httpx
import pytest
import requests


@pytest.fixture(autouse=True)
def deny_live_document_services(monkeypatch):
    attempted = []
    sync_send, async_send, request_send = httpx.Client.send, httpx.AsyncClient.send, requests.Session.send

    def local(url):
        return urlsplit(str(url)).hostname in {"localhost", "127.0.0.1", "::1"}

    def send(client, request, *args, **kwargs):
        if not local(request.url) and not isinstance(client._transport, httpx.MockTransport):
            attempted.append("httpx")
            raise AssertionError("Documents owner tests must not contact a live service")
        return sync_send(client, request, *args, **kwargs)

    async def asend(client, request, *args, **kwargs):
        if not local(request.url) and not isinstance(client._transport, httpx.MockTransport):
            attempted.append("async-httpx")
            raise AssertionError("Documents owner tests must not contact a live service")
        return await async_send(client, request, *args, **kwargs)

    def rsend(session, request, *args, **kwargs):
        if not local(request.url):
            attempted.append("requests")
            raise AssertionError("Documents owner tests must not contact a live service")
        return request_send(session, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setattr(httpx.AsyncClient, "send", asend)
    monkeypatch.setattr(requests.Session, "send", rsend)
    yield
    assert not attempted, "A caught exception must not hide a live provider attempt"
