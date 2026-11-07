"""Real PostgreSQL configuration identity, destination binding, and admission CAS."""

from uuid import uuid4

import pytest
from api import create_app
from cryptography.fernet import Fernet
from documents.model_access import ModelUnavailable
from fastapi.testclient import TestClient
from product.fixtures import RULE_A
from product.settings import Settings
from product.store.suites import SuiteStore
from pydantic import SecretStr


def configured_store(auth_store_factory):
    settings = Settings(
        usage_charge_enabled=False,
        git_sha="a" * 40,
        model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()),
        personal_model_allowed_base_urls="https://models.company.test/v1,https://other.company.test/inference",
    )
    store = auth_store_factory(settings)
    store.seed_isolated_dev()
    return store, store.principal_for_fixture("alice"), store.principal_for_fixture("bob")


def test_two_members_keep_complete_personal_config_and_tampered_destination_never_decrypts(auth_store_factory):
    store, alice, bob = configured_store(auth_store_factory)
    for person, url, model in [(alice, "https://models.company.test/v1", "gpt-6.1-sol"), (bob, "https://other.company.test/inference", "company-model")]:
        before = store.personal_model_identity(person.tenant_id, person.user_id)
        assert before["revision"] == "unconfigured"
        key = SecretStr(str(uuid4()))
        saved = store.put_personal_model_config(person, key, base_url=url, model=model)
        assert saved == store.personal_model_config(person)
        access = store.personal_model_access(person.tenant_id, person.user_id)
        assert access.api_key == key and access.base_url == url and access.model == model
        assert saved["revision"] == access.revision
        assert access.identity() == store.personal_model_identity(person.tenant_id, person.user_id)
    bob_identity = store.personal_model_identity(bob.tenant_id, bob.user_id)
    with store._conn() as conn:
        original = conn.execute("SELECT base_url,model,revision FROM product.personal_model_credentials WHERE user_id=%s", (alice.user_id,)).fetchone()
        for column, value in [("base_url", "https://other.company.test/inference"), ("model", "company-model"), ("revision", str(uuid4()))]:
            # Column names are fixed test constants, never input.
            conn.execute(f"UPDATE product.personal_model_credentials SET {column}=%s WHERE user_id=%s", (value, alice.user_id))
            with pytest.raises(ModelUnavailable):
                store.personal_model_access(alice.tenant_id, alice.user_id)
            conn.execute("UPDATE product.personal_model_credentials SET base_url=%s,model=%s,revision=%s WHERE user_id=%s", (*original, alice.user_id))
    assert store.personal_model_access(bob.tenant_id, bob.user_id).identity() == bob_identity


def test_replacement_rotates_identity_even_for_same_endpoint_and_revocation_is_per_user(auth_store_factory):
    store, alice, bob = configured_store(auth_store_factory)
    key = SecretStr(str(uuid4()))
    first = store.put_personal_model_config(alice, key, base_url="https://models.company.test/v1", model="gpt-6.1-sol")
    other = store.put_personal_model_config(bob, SecretStr(str(uuid4())), base_url="https://models.example.test", model="test-model")
    second = store.put_personal_model_config(alice, key, base_url="https://models.company.test/v1", model="gpt-6.1-sol")
    assert first["revision"] != second["revision"]
    store.delete_personal_model_config(alice)
    assert store.personal_model_config(alice)["revision"] is None
    assert store.personal_model_identity(alice.tenant_id, alice.user_id)["revision"] == "unconfigured"
    assert store.personal_model_config(bob) == other


def test_draft_admission_rejects_another_tabs_configuration_before_enqueuing_and_replays_old_receipt(auth_store_factory):
    store, alice, _ = configured_store(auth_store_factory)
    first = store.put_personal_model_config(alice, SecretStr(str(uuid4())), base_url="https://models.company.test/v1", model="gpt-6.1-sol")
    with TestClient(create_app(store.settings, auth_store=store)) as client:
        assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
        file = client.post("/files", json={"original_filename": "sample.txt", "byte_size": 1}).json()
        path = f"/files/{file['file_id']}/versions/{file['id']}"
        assert client.put(path + "/blob", content=b"x").status_code == 200
        assert client.post(path + "/finalize").status_code == 200
        draft = client.post("/drafts").json()["id"]
        assert client.put(f"/drafts/{draft}", json={"items": [{"file_version_id": file["id"], "role": "target", "rule_version_id": RULE_A}]}).status_code == 200
        second = store.put_personal_model_config(alice, SecretStr(str(uuid4())), base_url="https://other.company.test/inference", model="company-model")
        command = {"idempotency_key": "provider-admission", "expected_model_revision": first["revision"]}
        for revision in [first["revision"], None]:
            response = client.post(f"/drafts/{draft}/accept", json={**command, "expected_model_revision": revision})
            assert response.status_code == 409, response.text
            assert response.json()["error"] == "personal_model_configuration_changed"
        with store._conn() as conn:
            assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind='audit'").fetchone()[0] == 0
        command["expected_model_revision"] = second["revision"]
        accepted = client.post(f"/drafts/{draft}/accept", json=command)
        assert accepted.status_code == 201, accepted.text
        frozen = SuiteStore(store.settings, store.objects).read_snapshot(accepted.json()["id"], alice.tenant_id)
        assert frozen.model_identity == store.personal_model_identity(alice.tenant_id, alice.user_id)
        store.delete_personal_model_config(alice)
        replayed = client.post(f"/drafts/{draft}/accept", json=command)
        assert replayed.status_code == 201 and replayed.json()["id"] == accepted.json()["id"]
