"""The accepted execution revision identifies configuration, not a protocol label."""

import audit.policy as deep_policy
import pytest
from audit.audits import DEEP_PROTOCOL_REVISION
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
    settings = Settings(git_sha="a" * 40, model_text_base_url="https://model.example/v1", model_text_model="model-a", deep_executor_revision="release-a")
    original = settings.deep_execution_revision()
    assert len(original) == 64
    assert original != "release-a"
    for field, value in (("git_sha", "b" * 40), ("deep_executor_revision", "release-b"), ("deep_sandbox_profile", "ppap"), ("deep_review_seconds", 900)):
        changed = settings.model_copy(update={field: value})
        assert changed.deep_execution_revision() != original
    settings.model_text_api_key = "rotated-provider-credential"
    assert settings.deep_execution_revision() == original


def test_standard_does_not_require_a_deep_policy_or_valid_build_identity(monkeypatch):
    monkeypatch.setenv("PPAP_PROFILE", "isolated-dev")
    settings = Settings(git_sha="", deep_sandbox_profile="unsupported")
    assert settings.public_config()["usage_charge_enabled"] is True
    with pytest.raises(ValueError):
        settings.deep_execution_revision()


def test_default_and_explicit_protocol_labels_have_the_same_policy(monkeypatch):
    monkeypatch.setenv("PPAP_PROFILE", "isolated-dev")
    settings = Settings(git_sha="a" * 40, deep_executor_revision="")
    explicit = settings.model_copy(update={"deep_executor_revision": DEEP_PROTOCOL_REVISION})
    assert settings.deep_execution_revision() == explicit.deep_execution_revision()


def test_deep_review_time_is_a_bounded_setting(monkeypatch):
    monkeypatch.setenv("PPAP_PROFILE", "isolated-dev")
    assert Settings().deep_review_seconds == deep_policy.DEEP_REVIEW_SECONDS
    monkeypatch.setenv("PPAP_DEEP_REVIEW_SECONDS", "900")
    assert Settings().deep_review_seconds == 900
    for wrong in ("0", "59", "1801"):
        monkeypatch.setenv("PPAP_DEEP_REVIEW_SECONDS", wrong)
        with pytest.raises(ValueError):
            Settings()
