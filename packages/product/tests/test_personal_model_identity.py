"""Frozen personal configuration and deployment budgets are execution inputs."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from documents.model_access import ModelAccess, ModelUnavailable, current_model_access
from product.model_access import personal_model_scope
from product.settings import Settings
from pydantic import SecretStr, ValidationError


def test_policies_bind_complete_identity_without_endpoint_or_platform_fallback():
    settings = Settings(git_sha="a" * 40, llm_model="platform-model", llm_base_url="https://platform.example", llm_api_key="platform-fixture-key")
    access = ModelAccess(api_key=SecretStr("personal-fixture-key"), base_url="https://enterprise.example/v1", model="enterprise-model", provider="responses-compatible", revision=str(uuid4()))
    identity = access.identity()
    deep = settings.deep_execution_policy(identity)
    standard = settings.standard_execution_policy(identity)
    assert deep.model == standard.model == "enterprise-model"
    for field, value in (("revision", str(uuid4())), ("model", "another-model"), ("base_url", "https://other.example/v1"), ("provider", "other-provider")):
        changed = replace(access, **{field: value}).identity()
        assert settings.deep_execution_policy(changed).engine_revision() != deep.engine_revision()
        assert settings.standard_execution_policy(changed).engine_revision() != standard.engine_revision()
    for policy in (deep, standard):
        rendered = policy.model_dump_json()
        assert access.base_url not in rendered and access.api_key.get_secret_value() not in rendered
        assert "platform" not in rendered


@pytest.mark.parametrize("field,value", [("personal_model_max_requests", 7), ("personal_model_max_input_tokens", 900), ("personal_model_max_output_tokens", 300)])
def test_budget_change_invalidates_frozen_execution_revision(field, value):
    settings = Settings(git_sha="a" * 40)
    changed = settings.model_copy(update={field: value})
    assert changed.deep_execution_revision() != settings.deep_execution_revision()
    with pytest.raises(ValidationError):
        Settings(**{field: 0})


def test_direct_worker_scope_rejects_config_change_without_requiring_key_for_native_work():
    chosen = ModelAccess(api_key=SecretStr("personal-fixture-key"), model="test-model", base_url="https://models.example.test", revision=str(uuid4()))
    current = [chosen]
    calls = []

    def resolve(tenant, user):
        calls.append((tenant, user))
        return current[0]

    store = SimpleNamespace(personal_model_access=resolve)
    expected = chosen.identity()
    with personal_model_scope(store, tenant_id="tenant", initiating_user_id="initiator", expected_identity=expected):
        assert calls == []
        assert current_model_access() is chosen
        expected["revision"] = "caller-mutation-does-not-rebind"
        current[0] = replace(chosen, revision=str(uuid4()), base_url="https://other.example/v1")
        with pytest.raises(ModelUnavailable):
            current_model_access()
    with personal_model_scope(store, tenant_id="tenant", initiating_user_id="initiator", expected_identity={}):
        with pytest.raises(ModelUnavailable):
            current_model_access()
    assert calls == [("tenant", "initiator")] * 3
