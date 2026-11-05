"""Self-service API responses never expose keys, including validation failures."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from api.model_credentials import register_model_credential_routes
from cryptography.fernet import Fernet
from documents.model_access import ModelUnavailable
from fastapi import FastAPI
from fastapi.testclient import TestClient
from product.settings import Settings
from product.store.model_credentials import decrypt_credential, encrypt_credential, public_config
from pydantic import SecretStr


def controlled_api():
    config = Settings(model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    tenant = str(uuid4())
    alice, bob = (SimpleNamespace(tenant_id=tenant, user_id=str(uuid4())) for _ in range(2))
    principal = [alice]
    rows = {}

    def get(p):
        return public_config(rows[p.user_id][1] if p.user_id in rows else None)

    def put(p, key):
        rows[p.user_id] = (encrypt_credential(config, p.tenant_id, p.user_id, key), datetime.now(timezone.utc))
        return get(p)

    def delete(p):
        rows.pop(p.user_id, None)
        return get(p)

    store = SimpleNamespace(personal_model_config=get, put_personal_model_config=put, delete_personal_model_config=delete)
    app = FastAPI()
    register_model_credential_routes(app, store, lambda request: principal[0])
    return TestClient(app), principal, alice, bob, rows, config


def test_self_service_is_owner_only_secret_safe_and_revocable():
    client, principal, alice, bob, rows, config = controlled_api()
    assert client.get("/me/model-config").json()["configured"] is False
    key = str(uuid4())
    saved = client.put("/me/model-config", json={"api_key": key})
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert saved.headers["cache-control"] == "no-store"
    assert key not in saved.text and key not in rows[alice.user_id][0]
    assert decrypt_credential(config, alice.tenant_id, alice.user_id, rows[alice.user_id][0]).api_key.get_secret_value() == key
    for body in [{"api_key": key, "user_id": bob.user_id}, {"api_key": [key]}, {"api_key": key[:4] + "\n" + key[4:]}, {"api_key": ""}, {"api_key": key, "model": "other"}]:
        result = client.put("/me/model-config", json=body)
        assert result.status_code == 400
        assert key not in result.text
    principal[0] = bob
    assert client.get("/me/model-config").json()["configured"] is False
    assert client.put("/me/model-config", json={"api_key": str(uuid4())}).status_code == 200
    principal[0] = alice
    assert client.delete("/me/model-config").json()["configured"] is False
    principal[0] = bob
    assert client.get("/me/model-config").json()["configured"] is True
    principal[0] = None
    assert client.get("/me/model-config").status_code == 401
    assert client.put("/me/model-config", json={"api_key": key}).status_code == 401
    assert client.delete("/me/model-config").status_code == 401


def test_invalid_configuration_is_fixed_error_not_raw_key():
    client, _, _, _, _, config = controlled_api()
    config.model_credential_encryption_key = SecretStr("")
    key = str(uuid4())
    response = client.put("/me/model-config", json={"api_key": key})
    assert response.status_code == 503
    assert response.json() == {"error": "model_credential_encryption_unavailable"}
    assert key not in response.text


def test_personal_store_database_owner_isolation_and_membership_cleanup(auth_store_factory):
    config = Settings(feishu_app_id="controlled-app", model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(config)
    alice = store.upsert_feishu_identity("controlled-tenant", "alice")
    bob = store.upsert_feishu_identity("controlled-tenant", "bob")
    other = store.upsert_feishu_identity("other-controlled-tenant", "alice")
    alice_key, bob_key = SecretStr(str(uuid4())), SecretStr(str(uuid4()))
    store.put_personal_model_config(alice, alice_key)
    store.put_personal_model_config(bob, bob_key)
    assert store.personal_model_access(alice.tenant_id, alice.user_id).api_key == alice_key
    assert store.personal_model_access(bob.tenant_id, bob.user_id).api_key == bob_key
    with pytest.raises(ModelUnavailable):
        store.personal_model_access(other.tenant_id, alice.user_id)
    with store._conn() as conn:
        rows = conn.execute("SELECT key_ciphertext FROM product.personal_model_credentials").fetchall()
    assert all(alice_key.get_secret_value() not in row[0] and bob_key.get_secret_value() not in row[0] for row in rows)
    store.delete_personal_model_config(alice)
    with pytest.raises(ModelUnavailable):
        store.personal_model_access(alice.tenant_id, alice.user_id)
    assert store.personal_model_access(bob.tenant_id, bob.user_id).api_key == bob_key
    with store._conn() as conn:
        conn.execute("DELETE FROM product.memberships WHERE user_id=%s AND tenant_id=%s", (bob.user_id, bob.tenant_id))
    with pytest.raises(ModelUnavailable):
        store.personal_model_access(bob.tenant_id, bob.user_id)
