"""Configuration identity is distinct from secrets and model output quality."""

import json

import pytest
from ppap_core.settings import Settings


@pytest.mark.parametrize(
    "field,value",
    [
        ("git_sha", "b" * 40),
        ("MODEL_NAME", "another-model"),
        ("OPENAI_BASE_URL", "https://another.invalid/v1"),
        ("OPENAI_REASONING_EFFORT", "high"),
        ("OPENAI_TEMPERATURE", 0.7),
        ("OPENAI_TOP_P", 0.3),
        ("OPENAI_EXTRA_BODY_JSON", '{"provider":{"order":["other"]}}'),
        ("OCR_ADAPTER_MODE", "remote"),
        ("OCR_REMOTE_URL", "https://ocr.invalid"),
        ("OCR_MODEL_NAME", "ocr-other"),
        ("OCR_OPENAI_BASE_URL", "https://ocr-model.invalid/v1"),
        ("PADDLE_OCR_URL", "https://paddle.invalid"),
        ("MAX_FILE_SIZE", "10MB"),
    ],
)
def test_standard_revision_tracks_effective_configuration(field, value):
    settings = Settings(git_sha="a" * 40, MODEL_NAME="model-a", OPENAI_BASE_URL="https://provider.invalid/v1", OPENAI_EXTRA_BODY_JSON="")
    original = settings.standard_execution_policy()
    changed = settings.model_copy(update={field: value}).standard_execution_policy()
    assert original.engine_revision().startswith("standard-v1:")
    assert changed.engine_revision() != original.engine_revision()
    assert original.app_revision == "a" * 40
    assert original.model == "model-a"


def test_policy_contains_no_secrets_or_raw_provider_options():
    settings = Settings(git_sha="a" * 40, OPENAI_BASE_URL="https://private.invalid/v1", OPENAI_EXTRA_BODY_JSON='{"private_option":"private-value"}')
    original = settings.standard_execution_policy()
    for key in ("llm_api_key", "OPENAI_API_KEY", "OCR_OPENAI_API_KEY", "PADDLE_TOKEN", "s3_secret_key", "pg_worker_password", "feishu_app_secret"):
        setattr(settings, key, "rotated-secret")
    assert settings.standard_execution_policy() == original
    serialized = original.model_dump_json()
    assert set(json.loads(serialized)) == {"executor_label", "app_revision", "model", "model_config_revision", "tool_config_revision"}
    for private in ("private.invalid", "private_option", "private-value", "rotated-secret"):
        assert private not in serialized


def test_equivalent_extra_body_and_unconfigured_build_remain_truthful():
    settings = Settings(git_sha="", OPENAI_EXTRA_BODY_JSON='{"a":1,"b":2}')
    first = settings.standard_execution_policy()
    settings.OPENAI_EXTRA_BODY_JSON = '{"b": 2, "a": 1}'
    assert settings.standard_execution_policy() == first
    assert first.app_revision is None
    settings.OPENAI_EXTRA_BODY_JSON = "invalid"
    invalid = settings.standard_execution_policy()
    settings.OPENAI_EXTRA_BODY_JSON = "[]"
    assert settings.standard_execution_policy() == invalid


@pytest.mark.parametrize("name", ["PPAP_NG_TOPN", "CONCURRENT_SIZE", "LEGACY_DOC_CONVERTER_EXECUTABLE", "LEGACY_DOC_SANDBOX_EXECUTABLE", "LEGACY_DOC_CONVERTER_SOCKET"])
def test_tool_environment_changes_identity_without_disclosing_paths(monkeypatch, name):
    monkeypatch.delenv(name, raising=False)
    settings = Settings()
    original = settings.standard_execution_policy()
    monkeypatch.setenv(name, "private-alternate")
    changed = settings.standard_execution_policy()
    assert changed.engine_revision() != original.engine_revision()
    assert "private-alternate" not in changed.model_dump_json()
