"""Configuration identity is distinct from secrets and model output quality."""

import json

import pytest
from product.settings import Settings


@pytest.mark.parametrize(
    "field,value",
    [
        ("git_sha", "b" * 40),
        ("MAX_FILE_SIZE", "10MB"),
        ("PADDLE_OCR_URL", "https://paddle.invalid"),
    ],
)
def test_standard_revision_tracks_effective_configuration(field, value):
    settings = Settings(git_sha="a" * 40, MODEL_NAME="model-a", OPENAI_BASE_URL="https://provider.invalid/v1", OPENAI_EXTRA_BODY_JSON="")
    original = settings.standard_execution_policy()
    changed = settings.model_copy(update={field: value}).standard_execution_policy()
    assert original.engine_revision().startswith("standard-v1:")
    assert changed.engine_revision() != original.engine_revision()
    assert original.app_revision == "a" * 40
    assert original.model == "deepseek-flash"


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


def test_unconfigured_build_identity_remains_unknown():
    assert Settings(git_sha="").standard_execution_policy().app_revision is None


@pytest.mark.parametrize("name", ["PPAP_NG_TOPN", "LEGACY_DOC_CONVERTER_EXECUTABLE", "LEGACY_DOC_SANDBOX_EXECUTABLE", "LEGACY_DOC_CONVERTER_SOCKET"])
def test_tool_environment_changes_identity_without_disclosing_paths(monkeypatch, name):
    monkeypatch.delenv(name, raising=False)
    settings = Settings()
    original = settings.standard_execution_policy()
    monkeypatch.setenv(name, "private-alternate")
    changed = settings.standard_execution_policy()
    assert changed.engine_revision() != original.engine_revision()
    assert "private-alternate" not in changed.model_dump_json()


def test_paddle_endpoint_changes_revision_without_disclosing_credentials():
    settings = Settings(PADDLE_OCR_URL="https://private-paddle.invalid", PADDLE_TOKEN="private-token")
    original = settings.standard_execution_policy()
    changed = settings.model_copy(update={"PADDLE_OCR_URL": "https://another.invalid"}).standard_execution_policy()
    assert changed != original
    assert settings.model_copy(update={"PADDLE_TOKEN": "rotated-token"}).standard_execution_policy() == original
    assert all(private not in original.model_dump_json() for private in ("private-paddle", "private-token"))


@pytest.mark.parametrize(
    "field",
    [
        "llm_api_key",
        "llm_model",
        "llm_base_url",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "MODEL_NAME",
        "OCR_MODEL_NAME",
        "OCR_OPENAI_BASE_URL",
        "OCR_OPENAI_API_KEY",
        "OPENAI_EXTRA_BODY_JSON",
        "OCR_OPENAI_EXTRA_BODY_JSON",
    ],
)
def test_personal_policy_never_inherits_deployment_model_configuration(field):
    settings = Settings()
    original = settings.standard_execution_policy()
    setattr(settings, field, "ignored-deployment-value")
    assert settings.standard_execution_policy() == original
    assert original.model == "deepseek-flash"
