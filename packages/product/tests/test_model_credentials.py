"""Personal secrets are encrypted, owner-bound, and never model fallbacks."""

from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from documents.model_access import ModelUnavailable, current_model_access, model_access_scope, model_access_scoped, model_request_scope
from product.model_access import personal_model_scope
from product.settings import Settings
from product.store.model_credentials import ModelCredentialError, decrypt_credential, encrypt_credential, public_config
from pydantic import SecretStr


def settings():
    return Settings(model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()), personal_model_allowed_base_urls="https://models.example.test")


def test_ciphertext_is_authenticated_and_bound_to_tenant_and_user():
    config = settings()
    tenant, alice, bob = (str(uuid4()) for _ in range(3))
    # Random throwaway test material, never a fixture containing a user's key.
    key = SecretStr(str(uuid4()))
    encrypted = encrypt_credential(config, tenant, alice, key, base_url="https://models.example.test", model="test-model")
    assert key.get_secret_value() not in encrypted
    assert encrypt_credential(config, tenant, alice, key, base_url="https://models.example.test", model="test-model") != encrypted
    access = decrypt_credential(config, tenant, alice, encrypted)
    assert access.api_key == key
    assert key.get_secret_value() not in repr(access)
    assert key.get_secret_value() not in repr(asdict(access))
    assert config.model_credential_encryption_key.get_secret_value() not in config.model_dump_json()
    for owner_tenant, owner_user, ciphertext, cfg in [
        (tenant, bob, encrypted, config),
        (str(uuid4()), alice, encrypted, config),
        (tenant, alice, encrypted[:-4] + "AAAA", config),
        (tenant, alice, encrypted, settings()),
    ]:
        with pytest.raises(ModelUnavailable, match="personal_model_unavailable"):
            decrypt_credential(cfg, owner_tenant, owner_user, ciphertext)
    assert set(public_config()) == {"provider", "model", "base_url", "revision", "configured", "updated_at"}
    assert public_config()["configured"] is False


def test_missing_encryption_key_never_stores_plaintext_or_uses_deployment_secret():
    with pytest.raises(ModelCredentialError, match="model_credential_encryption_unavailable"):
        encrypt_credential(Settings(llm_api_key=str(uuid4())), str(uuid4()), str(uuid4()), SecretStr(str(uuid4())), base_url="https://models.example.test", model="test-model")


def test_each_model_call_resolves_the_current_initiator_and_revocation():
    tenant, alice, bob = (str(uuid4()) for _ in range(3))
    config = settings()
    rows = {(tenant, user): encrypt_credential(config, tenant, user, SecretStr(str(uuid4())), base_url="https://models.example.test", model="test-model") for user in (alice, bob)}

    def access(tenant_id, user_id):
        if (tenant_id, user_id) not in rows:
            raise ModelUnavailable()
        return decrypt_credential(config, tenant_id, user_id, rows[tenant_id, user_id])

    store = SimpleNamespace(personal_model_access=access)
    assert not model_access_scoped()
    with personal_model_scope(store, tenant_id=tenant, initiating_user_id=alice):
        first = current_model_access()
        with personal_model_scope(store, tenant_id=tenant, initiating_user_id=bob):
            second = current_model_access()
            assert first.api_key != second.api_key
        del rows[tenant, alice]
        with pytest.raises(ModelUnavailable):
            current_model_access()
        # No key is required for operations that do not request a model.
        assert sum([1, 2, 3]) == 6
    assert not model_access_scoped()
    with personal_model_scope(store, tenant_id=tenant, initiating_user_id=bob):
        assert current_model_access().api_key == second.api_key


def test_pending_requests_are_private_to_scope_and_block_concurrent_duplicate():
    request = {"input": "controlled text", "model": "test-model"}
    with model_access_scope(None):
        with model_request_scope("https://models.example.test", request):
            with pytest.raises(ModelUnavailable, match="personal_model_request_uncertain"):
                with model_request_scope("https://models.example.test", request):
                    pytest.fail("duplicate request must not reach the provider")
            with model_access_scope(None), model_request_scope("https://models.example.test", request):
                pass
        with pytest.raises(ModelUnavailable, match="personal_model_request_uncertain"):
            with model_request_scope("https://models.example.test", request):
                pytest.fail("unknown request must stay blocked in the outer scope")


def test_encrypted_configuration_binds_endpoint_model_and_revision_without_exposing_key():
    config = settings()
    config.personal_model_allowed_base_urls = "https://models.company.test/v1,https://other.company.test/v1"
    tenant, alice, revision = (str(uuid4()) for _ in range(3))
    key = SecretStr(str(uuid4()))
    encrypted = encrypt_credential(config, tenant, alice, key, base_url="https://models.company.test/v1/", model="gpt-6.1-sol", revision=revision)
    access = decrypt_credential(config, tenant, alice, encrypted)
    assert access.base_url == "https://models.company.test/v1" and access.model == "gpt-6.1-sol"
    assert access.provider == "responses-compatible" and access.revision == revision
    assert access.api_key == key
    assert access.base_url not in str(access.identity()) and key.get_secret_value() not in str(access.identity())
    assert decrypt_credential(config, tenant, alice, encrypted, expected_identity=access.identity()) == access
    for field, value in [("provider", "other"), ("model", "other"), ("revision", str(uuid4())), ("deployment", "0" * 64)]:
        with pytest.raises(ModelUnavailable):
            decrypt_credential(config, tenant, alice, encrypted, expected_identity={**access.identity(), field: value})
    config.personal_model_allowed_base_urls = ""
    with pytest.raises(ModelUnavailable):
        decrypt_credential(config, tenant, alice, encrypted)


def test_envelope_without_its_endpoint_never_resolves():
    import json

    config = settings()
    tenant, alice = str(uuid4()), str(uuid4())
    body = {"tenant_id": tenant, "user_id": alice, "provider": "responses-compatible", "api_key": str(uuid4())}
    ciphertext = Fernet(config.model_credential_encryption_key.get_secret_value().encode()).encrypt(json.dumps(body).encode()).decode()
    with pytest.raises(ModelUnavailable):
        decrypt_credential(config, tenant, alice, ciphertext)
