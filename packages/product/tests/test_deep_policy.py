"""The accepted execution revision identifies configuration, not a protocol label."""

from pathlib import Path

import audit.policy as deep_policy
import pytest
from audit.audits import ANALYSIS_STANDARD, DEEP_PROTOCOL_REVISION, job_payload
from product.settings import Settings


def test_pinned_grok_upgrade_changes_the_frozen_execution_revision(monkeypatch):
    assert deep_policy.GROK_VERSION == "1.0.70"
    policy = deep_policy.DeepExecutionPolicy(model="test-model", model_revision="0" * 64, app_revision="a" * 40, sandbox_profile="off")
    current = policy.engine_revision()
    with monkeypatch.context() as legacy:
        legacy.setattr(deep_policy, "GROK_VERSION", "1.0.40")
        assert policy.engine_revision() != current
    assert policy.engine_revision() == current


def test_deep_revision_changes_with_effective_policy_not_provider_credentials(monkeypatch):
    monkeypatch.setenv("PPAP_PROFILE", "isolated-dev")
    settings = Settings(git_sha="a" * 40, llm_base_url="https://model.example/v1", llm_model="model-a", deep_executor_revision="release-a")
    original = settings.deep_execution_revision()
    assert len(original) == 64
    assert original != "release-a"
    for field, value in (("git_sha", "b" * 40), ("llm_base_url", "https://other.example/v1"), ("llm_model", "model-b"), ("deep_executor_revision", "release-b"), ("deep_sandbox_profile", "ppap")):
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


def test_acceptance_and_runtime_use_the_same_effective_policy():
    from agent.execution.isolation import RuntimeConfig

    settings = Settings(git_sha="a" * 40, llm_base_url="https://model.example/v1", llm_model="model-a", deep_executor_revision="release-a", deep_sandbox_profile="off")
    config = RuntimeConfig(state=Path("/unused"), gateway_socket=Path("/unused/gateway.sock"), gateway_host="127.0.0.1", **settings.deep_execution_policy().model_dump())
    accepted = settings.deep_execution_revision()
    assert config.engine_revision() == accepted
    for field, value in (("app_revision", "b" * 40), ("model", "model-b"), ("model_revision", "b" * 64), ("sandbox_profile", "ppap"), ("memory_mib", 2048), ("max_processes", 128)):
        changed = config.model_copy(update={field: value})
        assert changed.engine_revision() != accepted
