"""OCR model configuration fails closed instead of selecting a retired Gemma default."""

import asyncio

import pytest
from documents.ocr import client, env


@pytest.mark.parametrize(
    ("ocr", "legacy", "llm", "expected"),
    [
        ("dedicated-ocr-model", "generic-model", "standard-model", "dedicated-ocr-model"),
        ("", "generic-model", "standard-model", "generic-model"),
        ("", "", "standard-model", "standard-model"),
    ],
)
def test_ocr_model_explicit_precedence(monkeypatch, ocr, legacy, llm, expected):
    monkeypatch.setenv("PPAP_PROFILE", "remote-dev")
    for name, value in (("OCR_MODEL_NAME", ocr), ("MODEL_NAME", legacy), ("LLM_MODEL", llm)):
        monkeypatch.setenv(name, value)

    assert env.ocr_model_name() == expected


def test_isolated_dev_uses_current_standard_model_when_not_overridden(monkeypatch):
    monkeypatch.setenv("PPAP_PROFILE", "isolated-dev")
    for name in ("OCR_MODEL_NAME", "MODEL_NAME", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)

    assert env.ocr_model_name() == env.ISOLATED_DEV_LLM_MODEL
    assert not hasattr(env, "DEFAULT_OCR_MODEL_NAME")


def test_missing_model_rejects_before_any_responses_request(monkeypatch):
    monkeypatch.setenv("PPAP_PROFILE", "production")
    for name in ("OCR_MODEL_NAME", "MODEL_NAME", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    requests = []

    def forbidden_client():
        requests.append(1)
        raise AssertionError("Must not create a provider client when OCR model is missing")

    monkeypatch.setattr(client, "_get_llm_client", forbidden_client)

    with pytest.raises(ValueError, match="OCR model is not configured"):
        env.ocr_model_name()

    with pytest.raises(ValueError, match="OCR model is not configured"):
        asyncio.run(
            client.call_llm_async(
                b"PNG",
                "OCR",
                response_schema={"name": "ocr_test", "strict": True, "schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
            )
        )

    assert requests == []
