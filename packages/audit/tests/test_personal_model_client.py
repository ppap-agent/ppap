"""Controlled real SDK transport proves per-user Responses calls and no retries."""

import json
from uuid import uuid4

import httpx
import pytest
from audit.engine.checkers.client import AIClient
from audit.usage import get_token_tracker
from documents.model_access import ModelAccess, ModelUnavailable, model_access_scope
from pydantic import SecretStr


@pytest.fixture(autouse=True)
def controlled_transport_environment(monkeypatch):
    # All HTTP is intercepted below. Host proxy settings are irrelevant here.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


def response(request, usage):
    return httpx.Response(
        200,
        request=request,
        json={
            "id": "response-controlled",
            "object": "response",
            "created_at": 0,
            "model": "deepseek-flash",
            "status": "completed",
            "output": [{"type": "message", "id": "message-controlled", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "checked", "annotations": []}]}],
            "usage": usage,
        },
    )


@pytest.mark.parametrize("usage,complete", [(None, False), ({"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}, True)])
def test_text_clients_use_each_callers_key_and_receipts(monkeypatch, usage, complete):
    calls = []
    monkeypatch.setenv("LLM_API_KEY", "must-not-use-platform")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-use-other")

    def send(_client, request, **kwargs):
        calls.append(request)
        return response(request, usage)

    monkeypatch.setattr(httpx.Client, "send", send)
    tracker = get_token_tracker()
    tracking = tracker.start_request_tracking()
    try:
        keys = [SecretStr(str(uuid4())), SecretStr(str(uuid4()))]
        for key in keys:
            with model_access_scope(ModelAccess(api_key=key)):
                assert AIClient().response_text([{"role": "user", "content": "check"}], retry_limit=9) == "checked"
        summary = tracker.end_request_tracking(tracking).to_dict()
    finally:
        tracker.cleanup_request(tracking)
    assert summary["usage_complete"] is complete
    assert summary["total_tokens"] == (30 if complete else None)
    assert len(calls) == 2
    for key, request in zip(keys, calls):
        assert request.headers["authorization"] == "Bearer " + key.get_secret_value()
        assert str(request.url) == "https://api.deepseek.com/responses"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        assert body["reasoning"] == {"effort": "none"}
        assert body["store"] is False
        assert key.get_secret_value() not in json.dumps(summary)
    with model_access_scope(None), pytest.raises(ModelUnavailable):
        AIClient().response_text([{"role": "user", "content": "check"}])
    assert len(calls) == 2


def test_uncertain_http_request_never_retries_even_with_requested_retry_limit(monkeypatch):
    calls = []

    def send(_client, request, **kwargs):
        calls.append(request)
        raise httpx.ReadTimeout("controlled timeout", request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    with model_access_scope(ModelAccess(api_key=SecretStr(str(uuid4())))), pytest.raises(Exception):
        AIClient().response_text([{"role": "user", "content": "check"}], retry_limit=5)
    assert len(calls) == 1
