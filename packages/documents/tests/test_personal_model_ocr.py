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
                "model": "test-model",
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
        with model_access_scope(ModelAccess(api_key=key, model="test-model", base_url="https://models.example.test")), ocr_usage_scope(receipts.append):
            assert await call_llm_async(b"controlled-image", "Read a fact.", response_schema=schema) == {"present": True}
    assert len(calls) == 2

    for key, request in zip(keys, calls):
        assert request.headers["authorization"] == "Bearer " + key.get_secret_value()
        body = json.loads(request.content)
        assert body["model"] == "test-model"
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
    with model_access_scope(ModelAccess(api_key=SecretStr(str(uuid4())), model="test-model", base_url="https://models.example.test")):
        with pytest.raises(Exception):
            await call_llm_async(b"controlled-image", "Read a fact.", response_schema=schema)
        with pytest.raises(ModelUnavailable, match="personal_model_request_uncertain"):
            await call_llm_async(b"controlled-image", "Read a fact.", response_schema=schema)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_provider_error_body_cannot_escape_as_a_visual_diagnostic(monkeypatch, caplog):
    key = str(uuid4())

    async def send(_client, request, **kwargs):
        return httpx.Response(401, request=request, json={"error": {"message": key, "type": "authentication_error"}})

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    schema = {"name": "fact", "schema": {"type": "object"}, "strict": True}
    with (
        model_access_scope(ModelAccess(api_key=SecretStr(key), model="test-model", base_url="https://models.example.test")),
        pytest.raises(ModelUnavailable, match="personal_model_request_failed") as failure,
    ):
        await call_llm_async(b"controlled-image", "Read a fact.", response_schema=schema)
    assert key not in str(failure.value)
    assert failure.value.__suppress_context__
    assert key not in caplog.text


@pytest.mark.asyncio
async def test_visual_transport_uses_custom_model_and_rejects_provider_redirect(monkeypatch):
    calls = []
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted-proxy.invalid:8888")

    async def handle(request):
        calls.append(request)
        return httpx.Response(307, request=request, headers={"location": "https://other.invalid/steal"})

    initialize = httpx.AsyncClient.__init__

    def controlled(client, **kwargs):
        initialize(client, **kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(httpx.AsyncClient, "__init__", controlled)
    access = ModelAccess(api_key=SecretStr(str(uuid4())), provider="responses-compatible", base_url="https://models.company.test/inference", model="gpt-6.1-sol")
    schema = {"name": "fact", "schema": {"type": "object"}, "strict": True}
    with model_access_scope(access), pytest.raises(ModelUnavailable):
        await call_llm_async(b"controlled-image", "Read a fact.", response_schema=schema)
    assert len(calls) == 1
    assert str(calls[0].url) == "https://models.company.test/inference/responses"
    assert calls[0].headers["authorization"] == "Bearer " + access.api_key.get_secret_value()
    assert json.loads(calls[0].content)["model"] == "gpt-6.1-sol"


@pytest.mark.asyncio
async def test_visual_personal_client_disables_proxy_and_redirects():
    from documents.ocr.client import _get_llm_client

    with model_access_scope(ModelAccess(api_key=SecretStr(str(uuid4())), model="test-model", base_url="https://models.example.test")):
        client = _get_llm_client()
    try:
        assert not client._client.follow_redirects and not client._client.trust_env
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "provider-private-value"])
async def test_success_status_malformed_provider_fields_cannot_escape_into_logs_or_receipts(monkeypatch, caplog, status):
    private = str(uuid4())
    receipts = []

    async def send(_client, request, **kwargs):
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "controlled",
                "object": "response",
                "created_at": 0,
                "model": private,
                "status": status if status == "completed" else private,
                "incomplete_details": {"reason": private},
                "output": [{"type": "message", "id": "controlled", "role": "assistant", "content": [{"type": "output_text", "text": "not JSON " + private, "annotations": []}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    schema = {"name": "fact", "schema": {"type": "object"}, "strict": True}
    from documents.ocr.client import ResponsesEvidenceError

    with (
        model_access_scope(ModelAccess(api_key=SecretStr(private), model="chosen-model", base_url="https://models.example.test")),
        ocr_usage_scope(receipts.append),
        pytest.raises(ResponsesEvidenceError) as failure,
    ):
        await call_llm_async(b"controlled-image", "Read a fact.", response_schema=schema)
    assert private not in str(failure.value) and private not in caplog.text and private not in json.dumps(receipts)
    assert receipts[0]["model"] == "chosen-model"
