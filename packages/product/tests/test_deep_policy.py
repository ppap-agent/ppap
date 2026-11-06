"""The accepted execution revision identifies configuration, not a protocol label."""

import audit.policy as deep_policy
import pytest
from audit.audits import ANALYSIS_STANDARD, DEEP_PROTOCOL_REVISION, job_payload
from product.settings import Settings


def test_pi_version_changes_the_frozen_execution_revision(monkeypatch):
    policy = deep_policy.DeepExecutionPolicy(model="test-model", model_revision="0" * 64, app_revision="a" * 40, sandbox_profile="off")
    current = policy.engine_revision()
    with monkeypatch.context() as changed:
        changed.setattr(deep_policy, "PI_VERSION", deep_policy.PI_VERSION + "-alternate")
        assert policy.engine_revision() != current
    assert policy.engine_revision() == current


def test_deep_revision_changes_with_effective_policy_not_provider_credentials(monkeypatch):
    monkeypatch.setenv("PPAP_PROFILE", "isolated-dev")
    settings = Settings(git_sha="a" * 40, llm_base_url="https://model.example/v1", llm_model="model-a", deep_executor_revision="release-a")
    original = settings.deep_execution_revision()
    assert len(original) == 64
    assert original != "release-a"
    for field, value in (("git_sha", "b" * 40), ("deep_executor_revision", "release-b"), ("deep_sandbox_profile", "ppap")):
        changed = settings.model_copy(update={field: value})
        assert changed.deep_execution_revision() != original
    settings.llm_api_key = "rotated-provider-credential"
    assert settings.deep_execution_revision() == original


def test_standard_does_not_require_a_deep_policy_or_valid_build_identity(monkeypatch):
    monkeypatch.setenv("PPAP_PROFILE", "isolated-dev")
    settings = Settings(git_sha="", deep_sandbox_profile="unsupported")
    assert settings.public_config()["usage_charge_enabled"] is True
    assert job_payload("round-a", ANALYSIS_STANDARD)["executor"] == "ppap-standard"
    with pytest.raises(ValueError):
        settings.deep_execution_revision()


def test_default_and_explicit_protocol_labels_have_the_same_policy(monkeypatch):
    monkeypatch.setenv("PPAP_PROFILE", "isolated-dev")
    settings = Settings(git_sha="a" * 40, deep_executor_revision="")
    explicit = settings.model_copy(update={"deep_executor_revision": DEEP_PROTOCOL_REVISION})
    assert settings.deep_execution_revision() == explicit.deep_execution_revision()


def test_deep_model_override_is_complete_and_independent_of_standard():
    settings = Settings(
        git_sha="a" * 40,
        llm_base_url="https://standard.example/v1",
        llm_model="standard",
        llm_api_key="standard-key",
        deep_llm_base_url="https://deep.example/backend-api/codex",
        deep_llm_model="deep",
        deep_llm_api_key="deep-key",
    )
    revision = settings.deep_execution_revision()
    assert settings.deep_model_config() == ("https://deep.example/backend-api/codex", "deep", "deep-key")
    assert settings.deep_execution_policy().model == "deepseek-flash"
    assert settings.llm_api_key == "standard-key" and settings.llm_model == "standard"
    assert settings.model_copy(update={"deep_llm_api_key": "rotated"}).deep_execution_revision() == revision
    assert settings.model_copy(update={"llm_model": "other-standard"}).deep_execution_revision() == revision
    assert settings.model_copy(update={"deep_llm_model": "other-deep"}).deep_execution_revision() == revision
    without_key = settings.model_copy(update={"deep_llm_api_key": ""})
    assert without_key.deep_execution_revision() == revision
    with pytest.raises(ValueError, match="credential is unavailable"):
        without_key.deep_model_config()
    assert settings.model_copy(update={"deep_reasoning_effort": "high"}).deep_execution_revision() != revision
