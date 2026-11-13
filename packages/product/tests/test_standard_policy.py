"""Configuration identity is distinct from secrets and model output quality."""

import json

import pytest
from product.settings import Settings


@pytest.mark.parametrize(
    "field,value",
    [
        ("git_sha", "b" * 40),
        ("MAX_FILE_SIZE", "10MB"),
    ],
)
def test_standard_revision_tracks_effective_configuration(field, value):
    settings = Settings(git_sha="a" * 40)
    original = settings.standard_execution_policy()
    changed = settings.model_copy(update={field: value}).standard_execution_policy()
    assert original.engine_revision().startswith("standard-v1:")
    assert changed.engine_revision() != original.engine_revision()
    assert original.app_revision == "a" * 40
    assert original.model == "unconfigured"


def test_policy_contains_no_secrets_or_raw_provider_options():
    settings = Settings(git_sha="a" * 40, model_text_base_url="https://private.invalid/v1")
    original = settings.standard_execution_policy()
    for key in ("model_text_api_key", "model_coding_api_key", "s3_secret_key", "pg_worker_password", "feishu_app_secret"):
        setattr(settings, key, "rotated-secret")
    assert settings.standard_execution_policy() == original
    serialized = original.model_dump_json()
    assert set(json.loads(serialized)) == {"executor_label", "app_revision", "model", "model_config_revision", "tool_config_revision"}
    for private in ("private.invalid", "rotated-secret"):
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


@pytest.mark.parametrize(
    "field",
    [
        "model_text_api_key",
        "model_text_model",
        "model_text_base_url",
        "model_coding_api_key",
        "model_coding_model",
        "model_coding_base_url",
    ],
)
def test_personal_policy_never_inherits_deployment_model_configuration(field):
    settings = Settings()
    original = settings.standard_execution_policy()
    setattr(settings, field, "ignored-deployment-value")
    assert settings.standard_execution_policy() == original
    assert original.model == "unconfigured"
