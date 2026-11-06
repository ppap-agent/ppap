"""Self-service API responses never expose keys, including validation failures."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from api.model_credentials import register_model_credential_routes
from cryptography.fernet import Fernet
from documents.model_access import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, ModelUnavailable
from fastapi import FastAPI
from fastapi.testclient import TestClient
from product.settings import Settings
from product.store import AuthStore
from product.store.model_credentials import decrypt_credential, encrypt_credential, public_config
from pydantic import SecretStr


def controlled_api():
    config = Settings(model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()), personal_model_allowed_base_urls="https://models.company.test/v1")
    tenant = str(uuid4())
    alice, bob = (SimpleNamespace(tenant_id=tenant, user_id=str(uuid4())) for _ in range(2))
    principal = [alice]
    rows = {}

    def get(p):
        if p.user_id not in rows:
            return public_config()
        ciphertext, updated_at = rows[p.user_id]
        access = decrypt_credential(config, p.tenant_id, p.user_id, ciphertext)
        return public_config(updated_at, provider=access.provider, model=access.model, base_url=access.base_url, revision=access.revision)

    def put(p, key, *, base_url=DEEPSEEK_BASE_URL, model=DEEPSEEK_MODEL):
        rows[p.user_id] = (encrypt_credential(config, p.tenant_id, p.user_id, key, base_url=base_url, model=model, revision=str(uuid4())), datetime.now(timezone.utc))
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


def test_database_roles_encrypted_owner_binding_and_member_rejoin(auth_store_factory):
    config = Settings(feishu_app_id="controlled-app", model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    setup = auth_store_factory(config)
    alice = setup.upsert_feishu_identity("controlled-tenant", "alice")
    bob = setup.upsert_feishu_identity("controlled-tenant", "bob")
    api = AuthStore(config, objects=setup.objects, role="api")
    worker = AuthStore(config, objects=setup.objects, role="worker")
    alice_key, bob_key = SecretStr(str(uuid4())), SecretStr(str(uuid4()))
    api.put_personal_model_config(alice, alice_key)
    api.put_personal_model_config(bob, bob_key)
    assert worker.personal_model_access(alice.tenant_id, alice.user_id).api_key == alice_key
    assert worker.personal_model_access(bob.tenant_id, bob.user_id).api_key == bob_key
    # Compromised row placement cannot lend a different owner's decrypted key.
    with setup._conn() as conn:
        cipher = conn.execute("SELECT key_ciphertext FROM product.personal_model_credentials WHERE user_id=%s", (alice.user_id,)).fetchone()[0]
        conn.execute("UPDATE product.personal_model_credentials SET key_ciphertext=%s WHERE user_id=%s", (cipher, bob.user_id))
    with pytest.raises(ModelUnavailable):
        worker.personal_model_access(bob.tenant_id, bob.user_id)
    assert worker.personal_model_access(alice.tenant_id, alice.user_id).api_key == alice_key
    # The worker can resolve keys, but cannot save/revoke another user's config.
    import psycopg

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        worker.put_personal_model_config(alice, SecretStr(str(uuid4())))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        worker.delete_personal_model_config(alice)
    with setup._conn() as conn:
        conn.execute("DELETE FROM product.memberships WHERE user_id=%s AND tenant_id=%s", (alice.user_id, alice.tenant_id))
        assert conn.execute("SELECT 1 FROM product.personal_model_credentials WHERE user_id=%s", (alice.user_id,)).fetchone() is None
    with pytest.raises(ModelUnavailable):
        api.put_personal_model_config(alice, SecretStr(str(uuid4())))
    with setup._conn() as conn:
        conn.execute("INSERT INTO product.memberships (user_id,tenant_id,role) VALUES (%s,%s,'member')", (alice.user_id, alice.tenant_id))
    assert api.personal_model_config(alice)["configured"] is False
    with pytest.raises(ModelUnavailable):
        worker.personal_model_access(alice.tenant_id, alice.user_id)


def test_database_account_deletion_cleans_keys_after_identity_cleanup(auth_store_factory):
    config = Settings(feishu_app_id="controlled-app", model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(config)
    alice = store.upsert_feishu_identity("controlled-tenant", "alice")
    store.put_personal_model_config(alice, SecretStr(str(uuid4())))
    # Existing product history can retain a user row. This verifies physical
    # removal only after the existing identity/membership references are gone.
    with store._conn() as conn, conn.transaction():
        conn.execute("DELETE FROM product.identities WHERE user_id=%s", (alice.user_id,))
        conn.execute("DELETE FROM product.memberships WHERE user_id=%s", (alice.user_id,))
        conn.execute("DELETE FROM product.users WHERE id=%s", (alice.user_id,))
        assert conn.execute("SELECT 1 FROM product.personal_model_credentials WHERE user_id=%s", (alice.user_id,)).fetchone() is None
    with pytest.raises(ModelUnavailable):
        store.personal_model_access(alice.tenant_id, alice.user_id)


def test_custom_config_is_complete_secret_safe_and_does_not_call_provider(monkeypatch):
    import httpx

    def no_provider(*args, **kwargs):
        raise AssertionError("Saving configuration must not call a model provider")

    # TestClient is in-process; block both actual HTTP network transports.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_provider)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_provider)
    client, _, alice, _, rows, config = controlled_api()
    old_key, new_key = str(uuid4()), str(uuid4())
    assert client.get("/me/model-config").json()["revision"] is None
    initial = client.put("/me/model-config", json={"api_key": old_key})
    assert initial.status_code == 200
    assert initial.json()["revision"]
    destination = {"base_url": "https://models.company.test/v1", "model": "internal-reasoner"}
    # Never borrow the old key when changing a destination or model.
    rejected = client.put("/me/model-config", json=destination)
    assert rejected.status_code == 400
    assert client.get("/me/model-config").json()["base_url"] == DEEPSEEK_BASE_URL
    saved = client.put("/me/model-config", json={"api_key": new_key, **destination})
    assert saved.status_code == 200
    assert saved.json() == {"provider": "responses-compatible", **destination, "configured": True, "updated_at": saved.json()["updated_at"], "revision": saved.json()["revision"]}
    assert client.get("/me/model-config").json() == saved.json()
    assert saved.json()["revision"] != initial.json()["revision"]
    assert old_key not in saved.text and new_key not in saved.text
    access = decrypt_credential(config, alice.tenant_id, alice.user_id, rows[alice.user_id][0])
    assert access.api_key.get_secret_value() == new_key
    assert access.base_url == destination["base_url"] and access.model == destination["model"]
    assert set(saved.json()) == {"provider", "model", "base_url", "configured", "updated_at", "revision"}


@pytest.mark.parametrize(
    "patch,code",
    [
        ({"base_url": None}, "invalid_model_endpoint"),
        ({"base_url": ["secret-in-url"]}, "invalid_model_endpoint"),
        ({"base_url": "x" * 2049}, "invalid_model_endpoint"),
        ({"base_url": "http://models.company.test/v1"}, "invalid_model_endpoint"),
        ({"base_url": "https://secret-in-url@models.company.test/v1"}, "invalid_model_endpoint"),
        ({"base_url": "https://models.company.test/v1?api_key=secret-in-url"}, "invalid_model_endpoint"),
        ({"base_url": "https://unapproved.example/v1"}, "model_endpoint_not_allowed"),
        ({"model": None}, "invalid_model_model"),
        ({"model": ["secret-in-model"]}, "invalid_model_model"),
        ({"model": ""}, "invalid_model_model"),
        ({"model": "x" * 201}, "invalid_model_model"),
    ],
)
def test_model_input_rejections_are_fixed_codes_without_echo(patch, code):
    client, _, _, _, rows, _ = controlled_api()
    key = str(uuid4())
    result = client.put("/me/model-config", json={"api_key": key, "base_url": "https://models.company.test/v1", "model": "internal-reasoner", **patch})
    assert result.status_code == 400
    assert result.json() == {"error": code}
    assert key not in result.text and "secret-in-" not in result.text
    assert not rows


def test_malformed_partial_or_oversized_config_never_echoes_secret():
    client, _, _, _, rows, _ = controlled_api()
    key = str(uuid4())
    for body in [
        {"api_key": key, "base_url": "https://models.company.test/v1"},
        {"api_key": key, "model": "internal-reasoner"},
        {"api_key": key, "base_url": "https://models.company.test/v1", "model": "internal-reasoner", "revision": key},
    ]:
        response = client.put("/me/model-config", json=body)
        assert response.status_code == 400
        assert key not in response.text
    for raw in [b'{"api_key":"' + key.encode(), b'{"api_key":"' + key.encode() + b'"' + b" " * 8192 + b"}", b"\xff" + key.encode()]:
        response = client.put("/me/model-config", content=raw, headers={"Content-Type": "application/json"})
        assert response.status_code == 400
        assert response.json() == {"error": "invalid_model_credential"}
        assert key not in response.text
    assert not rows
