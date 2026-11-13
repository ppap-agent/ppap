"""A tenant uses its own key when it has one, otherwise the platform models; never a silent swap."""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from documents.model_access import ModelUnavailable, current_model_access
from product.model_access import RETIRED_IDENTITY, tenant_model_scope
from product.settings import Settings
from product.store.model_credentials import ModelCredentialError, TenantModelStore, decrypt_credential, encrypt_credential, platform_selection
from pydantic import SecretStr

ENDPOINT = "https://models.example.test/v1"
MODELS = {"text": "tenant-text", "vision": "tenant-vision", "coding": "tenant-coding"}


@pytest.fixture(autouse=True)
def platform(monkeypatch):
    monkeypatch.setenv("PPAP_MODEL_TEXT_API_KEY", "platform-text-key")
    monkeypatch.setenv("PPAP_MODEL_TEXT_BASE_URL", "https://platform.example.test/v1")
    monkeypatch.setenv("PPAP_MODEL_TEXT_MODEL", "platform-text")
    monkeypatch.setenv("PPAP_MODEL_VISION_API_KEY", "platform-vision-key")
    monkeypatch.setenv("PPAP_MODEL_VISION_BASE_URL", "https://vision.example.test/v1")
    monkeypatch.setenv("PPAP_MODEL_VISION_MODEL", "platform-vision")
    for name in ("PPAP_MODEL_CODING_API_KEY", "PPAP_MODEL_CODING_BASE_URL", "PPAP_MODEL_CODING_MODEL"):
        monkeypatch.delenv(name, raising=False)


def settings(**changes):
    values = {"model_credential_encryption_key": SecretStr(Fernet.generate_key().decode()), "tenant_model_allowed_base_urls": ENDPOINT}
    return Settings(**(values | changes))


class Product:
    """The store surface TenantModelStore uses, with one in-memory row per tenant."""

    def __init__(self, config):
        self.settings, self.rows, self.tenants = config, {}, set()

    @contextmanager
    def _conn(self):
        yield self

    def execute(self, sql, params):
        if sql.lstrip().startswith("SELECT"):
            return SimpleNamespace(fetchone=lambda: self.rows.get(params[0]))
        if sql.lstrip().startswith("INSERT"):
            ciphertext, _provider, base_url, text, vision, coding, revision, _operator, tenant = params
            if tenant not in self.tenants:
                return SimpleNamespace(fetchone=lambda: None)
            self.rows[tenant] = (base_url, text, vision, coding, revision, ciphertext, datetime.now(timezone.utc))
            return SimpleNamespace(fetchone=lambda: (tenant,))
        self.rows.pop(params[0], None)
        return SimpleNamespace(fetchone=lambda: None)


def test_ciphertext_is_bound_to_tenant_endpoint_models_and_revision():
    config = settings()
    tenant, revision = str(uuid4()), str(uuid4())
    key = SecretStr(str(uuid4()))
    encrypted = encrypt_credential(config, tenant, key, base_url=ENDPOINT, models=MODELS, revision=revision)
    assert key.get_secret_value() not in encrypted
    accesses = decrypt_credential(config, tenant, encrypted, base_url=ENDPOINT, models=dict(MODELS), revision=revision)
    assert {purpose: access.model for purpose, access in accesses.items()} == MODELS
    assert all(access.api_key == key and access.source == "tenant" for access in accesses.values())
    assert key.get_secret_value() not in repr(accesses)
    for changed in (
        {"tenant_id": str(uuid4())},
        {"ciphertext": encrypted[:-4] + "AAAA"},
        {"models": {**MODELS, "coding": "other"}},
        {"revision": str(uuid4())},
        {"config": settings()},
    ):
        arguments = {"config": config, "tenant_id": tenant, "ciphertext": encrypted, "base_url": ENDPOINT, "models": dict(MODELS), "revision": revision} | changed
        with pytest.raises(ModelUnavailable, match="tenant_model_unavailable"):
            decrypt_credential(arguments.pop("config"), arguments.pop("tenant_id"), arguments.pop("ciphertext"), **arguments)


def test_missing_encryption_key_never_stores_plaintext():
    config = settings(model_credential_encryption_key=SecretStr(""))
    with pytest.raises(ModelCredentialError, match="model_credential_encryption_unavailable"):
        encrypt_credential(config, str(uuid4()), SecretStr("k"), base_url=ENDPOINT, models=MODELS, revision=str(uuid4()))


def test_endpoint_outside_the_operator_allowlist_is_rejected():
    store = TenantModelStore(Product(settings()))
    with pytest.raises(ModelCredentialError):
        store.candidate(str(uuid4()), SecretStr("k"), base_url="https://elsewhere.example.test/v1", text_model="m")


def test_without_a_tenant_key_the_platform_models_serve_each_use():
    store = TenantModelStore(Product(settings()))
    selection = store.selection(str(uuid4()))
    assert selection == platform_selection()
    assert set(selection.identity) == {"source", "provider", "model", "vision_model", "coding_model", "revision", "deployment"}
    assert (selection.identity["source"], selection.identity["model"], selection.identity["vision_model"], selection.identity["coding_model"]) == (
        "platform",
        "platform-text",
        "platform-vision",
        "platform-text",
    )
    assert selection.access("vision").api_key.get_secret_value() == "platform-vision-key"
    assert "platform-text-key" not in str(selection.identity)


def test_a_tenant_key_replaces_every_use_and_a_broken_one_is_never_swapped_for_the_platform():
    product = Product(settings())
    store = TenantModelStore(product)
    tenant = str(uuid4())
    product.tenants.add(tenant)
    candidate = store.candidate(tenant, SecretStr("tenant-key"), base_url=ENDPOINT, text_model="tenant-text", vision_model="tenant-vision", coding_model="tenant-coding")
    public = store.put(tenant, candidate, operator="ops")
    assert public["source"] == "tenant" and public["models"] == MODELS and "tenant-key" not in str(public)
    selection = store.selection(tenant)
    assert selection.identity["source"] == "tenant" and selection.identity["coding_model"] == "tenant-coding"
    assert all(selection.access(purpose).api_key.get_secret_value() == "tenant-key" for purpose in MODELS)
    row = list(product.rows[tenant])
    row[5] = row[5][:-4] + "AAAA"
    product.rows[tenant] = tuple(row)
    broken = store.selection(tenant)
    assert broken.identity["source"] == "tenant"
    for purpose in MODELS:
        with pytest.raises(ModelUnavailable, match="tenant_model_unavailable"):
            broken.access(purpose)
    assert store.delete(tenant) == {"source": "platform", "configured": False}
    with pytest.raises(ModelCredentialError, match="tenant_not_found"):
        store.put(str(uuid4()), candidate, operator="ops")


def test_operation_scope_uses_only_the_frozen_selection():
    current = [platform_selection()]
    store = SimpleNamespace(model_selection=lambda tenant: current[0])
    frozen = dict(current[0].identity)
    with tenant_model_scope(store, tenant_id="t", expected_identity=frozen):
        assert current_model_access("text").model == "platform-text"
        current[0] = SimpleNamespace(identity={**frozen, "revision": "changed"}, access=lambda purpose: pytest.fail("must not use a changed selection"))
        with pytest.raises(ModelUnavailable, match="model_configuration_changed"):
            current_model_access("text")
    legacy = {key: frozen[key] for key in ("provider", "model", "revision", "deployment")}
    with tenant_model_scope(store, tenant_id="t", expected_identity=legacy), pytest.raises(ModelUnavailable, match=RETIRED_IDENTITY):
        current_model_access("text")


def test_a_key_is_stored_only_after_text_structured_and_vision_succeed(monkeypatch):
    from documents.ai import AIError, AIResult
    from documents.model_access import ModelAccess
    from product import tenant_models

    candidate = SimpleNamespace(accesses={purpose: ModelAccess(api_key=SecretStr("tenant-key"), model=model, base_url=ENDPOINT, source="tenant") for purpose, model in MODELS.items()})
    seen = []

    def run(task, *, purpose="text", schema=None, **kwargs):
        selected = current_model_access(purpose)
        seen.append((purpose, selected.model, bool(schema)))
        if purpose == "vision":
            raise AIError("provider_status")
        return AIResult({"ok": True})

    monkeypatch.setattr(tenant_models, "run_json", run)
    assert tenant_models.check_capabilities(candidate) == ["vision"]
    assert seen == [("text", "tenant-text", False), ("text", "tenant-text", True), ("vision", "tenant-vision", True)]


def test_every_process_agrees_on_the_platform_identity_whatever_keys_it_holds(monkeypatch):
    """API, Worker and the Deep Controller hold different keys but freeze one identity."""
    from documents.model_access import model_access_scope

    controller = platform_selection().identity
    for name in ("PPAP_MODEL_TEXT_API_KEY", "PPAP_MODEL_VISION_API_KEY"):
        monkeypatch.delenv(name)
    api = platform_selection()
    assert api.identity == controller
    with model_access_scope(api.access), pytest.raises(ModelUnavailable, match="platform_model_unconfigured"):
        current_model_access("text")
