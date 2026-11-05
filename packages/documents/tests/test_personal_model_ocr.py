"""A real async SDK call shape, with a controlled HTTP boundary (not paid proof)."""

import json
from uuid import uuid4

import httpx
import pytest
from documents.model_access import ModelAccess, ModelUnavailable, model_access_scope
from documents.ocr.client import call_llm_async, ocr_usage_scope
from pydantic import SecretStr


@pytest.fixture(autouse=True)
def controlled_transport_environment(monkeypatch):
    # All HTTP is intercepted below. Host proxy settings are irrelevant here.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [None, {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9}])
async def test_visual_requests_use_personal_key_input_image_and_reasoning(monkeypatch, usage):
    calls, receipts = [], []
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "must-not-use-platform")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://platform.invalid")

    async def send(_client, request, **kwargs):
        calls.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "response-controlled",
                "object": "response",
                "created_at": 0,
                "model": "deepseek-flash",
                "status": "completed",
                "output": [
                    {"type": "message", "id": "message-controlled", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": '{"present":true}', "annotations": []}]}
                ],
                "usage": usage,
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    schema = {"name": "fact", "schema": {"type": "object", "properties": {"present": {"type": "boolean"}}, "required": ["present"], "additionalProperties": False}, "strict": True}
    keys = [SecretStr(str(uuid4())), SecretStr(str(uuid4()))]
    for key in keys:
        with model_access_scope(ModelAccess(api_key=key)), ocr_usage_scope(receipts.append):
            assert await call_llm_async(b"controlled-image", "Read a fact.", response_schema=schema) == {"present": True}
    assert len(calls) == 2

    for key, request in zip(keys, calls):
        assert request.headers["authorization"] == "Bearer " + key.get_secret_value()
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        assert body["reasoning"] == {"effort": "none"}
        assert body["input"][0]["role"] == "user"
        assert body["input"][0]["content"][1]["type"] == "input_image"
        assert body["store"] is False
        assert key.get_secret_value() not in json.dumps(receipts)
    assert ("input_tokens" in receipts[0]) is (usage is not None)
    with model_access_scope(None), pytest.raises(ModelUnavailable):
        await call_llm_async(b"controlled-image", "Read a fact.", response_schema=schema)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_uncertain_visual_request_cannot_be_replayed_by_later_clause(monkeypatch):
    calls = []

    async def send(_client, request, **kwargs):
        calls.append(request)
        raise httpx.ReadTimeout("controlled timeout", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    schema = {"name": "fact", "schema": {"type": "object"}, "strict": True}
    with model_access_scope(ModelAccess(api_key=SecretStr(str(uuid4())))):
        with pytest.raises(Exception):
            await call_llm_async(b"controlled-image", "Read a fact.", response_schema=schema)
        with pytest.raises(ModelUnavailable, match="personal_model_request_uncertain"):
            await call_llm_async(b"controlled-image", "Read a fact.", response_schema=schema)
    assert len(calls) == 1
