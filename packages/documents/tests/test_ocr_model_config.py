"""OCR uses dedicated settings and never inherits a text-model credential."""

import pytest
from documents.model_access import ModelUnavailable, current_model_access
from documents.ocr import env


def test_ocr_configuration_does_not_inherit_text_settings(monkeypatch):
    for key in ("PPAP_MODEL_TEXT_MODEL", "PPAP_MODEL_TEXT_API_KEY", "PPAP_MODEL_TEXT_BASE_URL"):
        monkeypatch.setenv(key, "text-only")
    for key in ("PPAP_MODEL_VISION_MODEL", "PPAP_MODEL_VISION_API_KEY", "PPAP_MODEL_VISION_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    for read in (env.ocr_openai_api_key, env.ocr_openai_base_url, env.ocr_model_name):
        with pytest.raises(ValueError, match="Vision model is not configured"):
            read()
    with pytest.raises(ModelUnavailable, match="platform_model_unconfigured"):
        current_model_access("vision")


@pytest.fixture
def vision_key(monkeypatch):
    monkeypatch.setenv("PPAP_MODEL_VISION_API_KEY", "dedicated-ocr-key")
    monkeypatch.setenv("PPAP_MODEL_VISION_BASE_URL", "https://ocr.example.test/v1")
    monkeypatch.setenv("PPAP_MODEL_VISION_MODEL", "dedicated-ocr-model")


def test_ocr_dedicated_model_and_extra_body_are_canonical(monkeypatch, vision_key):
    monkeypatch.setenv("PPAP_MODEL_VISION_EXTRA_BODY_JSON", '{"reasoning":true,"a":1}')
    first = env.ocr_policy_identity()
    assert env.ocr_model_name() == "dedicated-ocr-model"
    assert env.ocr_extra_body() == {"reasoning": True, "a": 1}
    monkeypatch.setenv("PPAP_MODEL_VISION_EXTRA_BODY_JSON", '{"a":1,"reasoning":true}')
    assert env.ocr_policy_identity() == first
    monkeypatch.setenv("PPAP_MODEL_VISION_EXTRA_BODY_JSON", '{"a":1,"reasoning":false}')
    assert env.ocr_policy_identity() != first


def test_ocr_transcription_defaults_to_no_reasoning_without_overriding_user(monkeypatch, vision_key):
    monkeypatch.delenv("PPAP_MODEL_VISION_EXTRA_BODY_JSON", raising=False)
    assert env.ocr_extra_body() == {"reasoning": {"effort": "none"}}
    default_identity = env.ocr_policy_identity()
    monkeypatch.setenv("PPAP_MODEL_VISION_EXTRA_BODY_JSON", '{"reasoning":{"effort":"high"},"other":true}')
    assert env.ocr_extra_body() == {"reasoning": {"effort": "high"}, "other": True}
    assert env.ocr_policy_identity() != default_identity


@pytest.mark.parametrize("raw", ["[]", "null", "not-json"])
def test_invalid_ocr_extra_body_fails_without_network(monkeypatch, vision_key, raw):
    monkeypatch.setenv("PPAP_MODEL_VISION_EXTRA_BODY_JSON", raw)
    with pytest.raises(ValueError, match="one JSON object"):
        env.ocr_extra_body()


def test_explicit_model_grant_owns_ocr_model_endpoint_and_policy(monkeypatch):
    from documents.model_access import ModelAccess, model_access_scope
    from pydantic import SecretStr

    monkeypatch.setenv("PPAP_MODEL_VISION_API_KEY", "ambient-key")
    monkeypatch.setenv("PPAP_MODEL_VISION_BASE_URL", "https://ambient.invalid/v1")
    monkeypatch.setenv("PPAP_MODEL_VISION_MODEL", "ambient-model")
    first = ModelAccess(SecretStr("granted-key"), provider="test", model="granted-model", base_url="http://127.0.0.1:12345/model")
    with model_access_scope(first):
        assert env.ocr_openai_api_key() == "granted-key"
        assert env.ocr_model_name() == "granted-model"
        assert env.ocr_openai_base_url() == "http://127.0.0.1:12345/model"
        assert env.ocr_extra_body() == {}
        identity = env.ocr_policy_identity()
        assert "granted-key" not in identity
        monkeypatch.setenv("PPAP_MODEL_VISION_MODEL", "changed-ambient-model")
        assert env.ocr_policy_identity() == identity
    with model_access_scope(ModelAccess(SecretStr("rotated-key"), provider="test", model=first.model, base_url=first.base_url)):
        assert env.ocr_policy_identity() == identity
    with model_access_scope(ModelAccess(first.api_key, provider="test", model="another-model", base_url=first.base_url)):
        assert env.ocr_policy_identity() != identity
    with model_access_scope(ModelAccess(first.api_key, provider="test", model=first.model, base_url="http://127.0.0.1:54321/model")):
        assert env.ocr_policy_identity() != identity


def test_unavailable_model_scope_never_borrows_ambient_ocr_configuration(monkeypatch):
    from documents.model_access import model_access_scope

    monkeypatch.setenv("PPAP_MODEL_VISION_API_KEY", "ambient-key")
    monkeypatch.setenv("PPAP_MODEL_VISION_BASE_URL", "https://ambient.invalid/v1")
    monkeypatch.setenv("PPAP_MODEL_VISION_MODEL", "ambient-model")
    ambient = env.ocr_policy_identity()
    with model_access_scope(None):
        for read in (env.ocr_openai_api_key, env.ocr_openai_base_url, env.ocr_model_name):
            with pytest.raises(ValueError, match="Vision model is not configured"):
                read()
        assert env.ocr_policy_identity() != ambient
