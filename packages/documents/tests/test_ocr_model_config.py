"""OCR uses dedicated settings and never inherits a text-model credential."""

import asyncio
from unittest.mock import patch

import pytest
from documents.ocr import client, env


def test_ocr_configuration_does_not_inherit_text_settings(monkeypatch):
    for key in ("MODEL_NAME", "LLM_MODEL", "LLM_TEXT_MODEL", "OPENAI_API_KEY", "LLM_API_KEY", "OPENAI_BASE_URL", "LLM_BASE_URL"):
        monkeypatch.setenv(key, "text-only")
    for key in ("OCR_MODEL_NAME", "OCR_OPENAI_API_KEY", "OCR_OPENAI_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    assert env.ocr_openai_api_key() == env.ocr_openai_base_url() == ""
    with pytest.raises(ValueError, match="OCR model is not configured"):
        env.ocr_model_name()
    with patch.object(client, "_get_llm_client") as provider:
        with pytest.raises(ValueError, match="OCR model is not configured"):
            asyncio.run(client.call_llm_async(b"PNG", "inspect", response_schema={}))
        provider.assert_not_called()


def test_ocr_dedicated_model_and_extra_body_are_canonical(monkeypatch):
    monkeypatch.setenv("OCR_MODEL_NAME", "dedicated-ocr-model")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://ocr.example.test/v1")
    monkeypatch.setenv("OCR_OPENAI_EXTRA_BODY_JSON", '{"reasoning":true,"a":1}')
    first = env.ocr_policy_identity()
    assert env.ocr_model_name() == "dedicated-ocr-model"
    assert env.ocr_extra_body() == {"reasoning": True, "a": 1}
    monkeypatch.setenv("OCR_OPENAI_EXTRA_BODY_JSON", '{"a":1,"reasoning":true}')
    assert env.ocr_policy_identity() == first
    monkeypatch.setenv("OCR_OPENAI_EXTRA_BODY_JSON", '{"a":1,"reasoning":false}')
    assert env.ocr_policy_identity() != first


def test_ocr_transcription_defaults_to_no_reasoning_without_overriding_user(monkeypatch):
    monkeypatch.delenv("OCR_OPENAI_EXTRA_BODY_JSON", raising=False)
    assert env.ocr_extra_body() == {"reasoning": {"effort": "none"}}
    default_identity = env.ocr_policy_identity()
    monkeypatch.setenv("OCR_OPENAI_EXTRA_BODY_JSON", '{"reasoning":{"effort":"high"},"other":true}')
    assert env.ocr_extra_body() == {"reasoning": {"effort": "high"}, "other": True}
    assert env.ocr_policy_identity() != default_identity


@pytest.mark.parametrize("raw", ["[]", "null", "not-json"])
def test_invalid_ocr_extra_body_fails_without_network(monkeypatch, raw):
    monkeypatch.setenv("OCR_OPENAI_EXTRA_BODY_JSON", raw)
    with pytest.raises(ValueError, match="one JSON object"):
        env.ocr_extra_body()


def test_missing_ocr_credential_cannot_use_text_credential(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "text-only")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://ocr.example.test/v1")
    monkeypatch.delenv("OCR_OPENAI_API_KEY", raising=False)
    with patch.object(client, "AsyncOpenAI") as sdk:
        with pytest.raises(ValueError, match="OCR endpoint and credential"):
            client._get_llm_client()
        sdk.assert_not_called()
