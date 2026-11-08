"""An explicit OCR policy belongs to its grant, never to ambient credentials."""

import asyncio
import json

import httpx
import pytest
from documents.model_access import ModelAccess, model_access_scope
from documents.ocr.client import call_llm_async
from documents.ocr.env import ocr_extra_body, ocr_policy_identity
from pydantic import SecretStr


def access(**kwargs):
    return ModelAccess(SecretStr("controlled-key"), model="controlled-model", base_url="https://models.example.test/v1", **kwargs)


def test_explicit_ocr_options_reach_the_wire_and_change_both_policy_identities(monkeypatch):
    requests = []

    async def send(client, request, **kwargs):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "controlled-response",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "controlled-model",
                "output": [{"type": "message", "id": "controlled-message", "role": "assistant", "content": [{"type": "output_text", "text": '{"present":true}'}]}],
                "usage": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8, "output_tokens_details": {"reasoning_tokens": 0}},
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    monkeypatch.setenv("OCR_OPENAI_EXTRA_BODY_JSON", '{"reasoning":{"effort":"high"}}')
    plain = access()
    explicit = access(ocr_extra_body_json='{"reasoning":{"effort":"none"}}')
    with model_access_scope(plain):
        original_policy = ocr_policy_identity()
        assert ocr_extra_body() == {}
    with model_access_scope(explicit):
        assert ocr_policy_identity() != original_policy
        assert ocr_extra_body() == {"reasoning": {"effort": "none"}}
        schema = {"name": "fact", "strict": True, "schema": {"type": "object", "properties": {"present": {"type": "boolean"}}, "required": ["present"], "additionalProperties": False}}
        assert asyncio.run(call_llm_async(b"synthetic", "Observe.", response_schema=schema)) == {"present": True}
    assert explicit.identity() != plain.identity()
    assert requests[0]["reasoning"] == {"effort": "none"}
    assert requests[0]["max_output_tokens"] == 4096 and requests[0]["store"] is False
    assert len(requests) == 1


def test_ocr_policy_is_canonical_and_cannot_leak_between_grants():
    one = access(ocr_extra_body_json='{"reasoning":{"effort":"none"},"provider":{"require_parameters":true}}')
    two = access(ocr_extra_body_json='{"provider": {"require_parameters": true}, "reasoning": {"effort": "none"}}')
    assert one.identity() == two.identity()
    assert "controlled-key" not in json.dumps(one.identity())
    with model_access_scope(one):
        options = ocr_extra_body()
        options["reasoning"]["effort"] = "high"
        assert ocr_extra_body()["reasoning"]["effort"] == "none"
        with model_access_scope(access()):
            assert ocr_extra_body() == {}


@pytest.mark.parametrize("raw", ["[]", "null", "private-not-json"])
def test_invalid_explicit_options_are_rejected_without_disclosing_the_value(raw):
    with model_access_scope(access(ocr_extra_body_json=raw)), pytest.raises(ValueError, match="one JSON object") as failure:
        ocr_extra_body()
    assert raw not in str(failure.value)
