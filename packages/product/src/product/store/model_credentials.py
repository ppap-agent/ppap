"""A tenant's own model key (tenant BYOK). Without one, the tenant uses the platform models.

The key is encrypted together with its tenant, destination and models, so editing a row
or copying it to another tenant cannot redirect a stored key. Only operators set it; no
customer API or page reads or writes it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from documents.model_access import MODEL_PROVIDER, PURPOSES, ModelAccess, ModelPurpose, ModelUnavailable, platform_model_access
from pydantic import SecretStr

from product.model_endpoint import ModelEndpointError, approved_base_url, valid_model


class ModelCredentialError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _cipher(settings) -> Fernet:
    try:
        raw = settings.model_credential_encryption_key.get_secret_value()
        return Fernet(raw.encode("ascii"))
    except (AttributeError, ValueError, UnicodeError):
        raise ModelCredentialError("model_credential_encryption_unavailable") from None


def _tenant(tenant_id: str) -> str:
    try:
        return str(UUID(tenant_id))
    except (ValueError, AttributeError, TypeError):
        raise ModelUnavailable() from None


def _endpoint(settings, base_url: str) -> str:
    try:
        return approved_base_url(settings, base_url)
    except ModelEndpointError as exc:
        raise ModelCredentialError(exc.code) from None


def _models(text_model: str, vision_model: str | None, coding_model: str | None) -> dict[ModelPurpose, str]:
    try:
        text = valid_model(text_model)
        return {"text": text, "vision": valid_model(vision_model or text), "coding": valid_model(coding_model or text)}
    except ModelEndpointError as exc:
        raise ModelCredentialError(exc.code) from None


def encrypt_credential(settings, tenant_id: str, api_key: SecretStr, *, base_url: str, models: dict[ModelPurpose, str], revision: str) -> str:
    key = api_key.get_secret_value().strip()
    if not 1 <= len(key) <= 4096 or any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise ModelCredentialError("invalid_model_credential")
    body = json.dumps({"tenant_id": _tenant(tenant_id), "provider": MODEL_PROVIDER, "api_key": key, "base_url": _endpoint(settings, base_url), "models": dict(models), "revision": revision})
    return _cipher(settings).encrypt(body.encode()).decode("ascii")


def decrypt_credential(settings, tenant_id: str, ciphertext: str, *, base_url: str, models: Mapping[ModelPurpose, str], revision: str) -> dict[ModelPurpose, ModelAccess]:
    """The stored key for each use, only if the encrypted binding matches the row."""
    try:
        body = json.loads(_cipher(settings).decrypt(ciphertext.encode("ascii")))
        if body["tenant_id"] != _tenant(tenant_id) or body["base_url"] != base_url or body["models"] != dict(models) or body["revision"] != revision:
            raise ValueError()
        key = body["api_key"]
        if not isinstance(key, str) or not key:
            raise ValueError()
        endpoint = _endpoint(settings, body["base_url"])
        return {purpose: ModelAccess(api_key=SecretStr(key), model=models[purpose], base_url=endpoint, source="tenant", revision=revision) for purpose in PURPOSES}
    except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError, ModelCredentialError):
        raise ModelUnavailable("tenant_model_unavailable") from None


@dataclass(frozen=True)
class ModelSelection:
    """The models one tenant uses right now, and the identity an operation freezes."""

    identity: dict[str, str]
    accesses: dict[ModelPurpose, ModelAccess | ModelUnavailable]

    def access(self, purpose: ModelPurpose) -> ModelAccess:
        value = self.accesses[purpose]
        if isinstance(value, ModelUnavailable):
            raise value
        return value


def selection_identity(source: str, accesses: dict[ModelPurpose, ModelAccess | ModelUnavailable]) -> dict[str, str]:
    """One flat, non-secret identity covering every use; any change gives a new revision."""
    parts = {purpose: value.identity() if isinstance(value, ModelAccess) else {"unavailable": value.code} for purpose, value in accesses.items()}
    text = accesses["text"]
    return {
        "source": source,
        "provider": MODEL_PROVIDER,
        "model": text.model if isinstance(text, ModelAccess) else "unavailable",
        "vision_model": value.model if isinstance(value := accesses["vision"], ModelAccess) else "unavailable",
        "coding_model": value.model if isinstance(value := accesses["coding"], ModelAccess) else "unavailable",
        "revision": hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "deployment": hashlib.sha256((text.base_url if isinstance(text, ModelAccess) else "").encode()).hexdigest(),
    }


def platform_selection() -> ModelSelection:
    accesses: dict[ModelPurpose, ModelAccess | ModelUnavailable] = {}
    for purpose in PURPOSES:
        try:
            accesses[purpose] = platform_model_access(purpose)
        except ModelUnavailable as exc:
            accesses[purpose] = exc
    return ModelSelection(selection_identity("platform", accesses), accesses)


@dataclass(frozen=True)
class KeyCandidate:
    """A validated, encrypted key that is not stored until its capabilities pass."""

    base_url: str
    models: dict[ModelPurpose, str]
    revision: str
    ciphertext: str = field(repr=False)
    accesses: dict[ModelPurpose, ModelAccess] = field(repr=False)


class TenantModelStore:
    def __init__(self, product):
        self.product = product

    def _row(self, conn, tenant_id: str):
        return conn.execute(
            "SELECT base_url,text_model,vision_model,coding_model,revision,key_ciphertext,updated_at FROM product.tenant_model_credentials WHERE tenant_id=%s",
            (_tenant(tenant_id),),
        ).fetchone()

    def selection(self, tenant_id: str, *, conn=None) -> ModelSelection:
        """Tenant key when the tenant has one, otherwise the platform models."""
        with nullcontext(conn) if conn is not None else self.product._conn() as connection:
            row = self._row(connection, tenant_id)
        if row is None:
            return platform_selection()
        models: dict[ModelPurpose, str] = {"text": row[1], "vision": row[2], "coding": row[3]}
        try:
            accesses: dict[ModelPurpose, ModelAccess | ModelUnavailable] = dict(decrypt_credential(self.product.settings, tenant_id, row[5], base_url=row[0], models=models, revision=str(row[4])))
        except ModelUnavailable as exc:
            # A broken tenant key is reported, never silently replaced by the platform model.
            accesses = dict.fromkeys(PURPOSES, exc)
        return ModelSelection(selection_identity("tenant", accesses), accesses)

    def public(self, tenant_id: str) -> dict:
        """What operators may see: never the key."""
        with self.product._conn() as conn:
            row = self._row(conn, tenant_id)
        if row is None:
            return {"source": "platform", "configured": False}
        return {"source": "tenant", "configured": True, "base_url": row[0], "models": {"text": row[1], "vision": row[2], "coding": row[3]}, "revision": str(row[4]), "updated_at": row[6].isoformat()}

    def candidate(self, tenant_id: str, api_key: SecretStr, *, base_url: str, text_model: str, vision_model: str | None = None, coding_model: str | None = None) -> KeyCandidate:
        tenant_id, endpoint, models, revision = _tenant(tenant_id), _endpoint(self.product.settings, base_url), _models(text_model, vision_model, coding_model), str(uuid4())
        ciphertext = encrypt_credential(self.product.settings, tenant_id, api_key, base_url=endpoint, models=models, revision=revision)
        accesses = decrypt_credential(self.product.settings, tenant_id, ciphertext, base_url=endpoint, models=models, revision=revision)
        return KeyCandidate(endpoint, models, revision, ciphertext, accesses)

    def put(self, tenant_id: str, candidate: KeyCandidate, *, operator: str) -> dict:
        base_url, models, revision, ciphertext = candidate.base_url, candidate.models, candidate.revision, candidate.ciphertext
        with self.product._conn() as conn:
            row = conn.execute(
                """INSERT INTO product.tenant_model_credentials (tenant_id,key_ciphertext,provider,base_url,text_model,vision_model,coding_model,revision,updated_by)
                   SELECT id,%s,%s,%s,%s,%s,%s,%s,%s FROM product.tenants WHERE id=%s
                   ON CONFLICT (tenant_id) DO UPDATE SET
                     key_ciphertext=EXCLUDED.key_ciphertext,provider=EXCLUDED.provider,base_url=EXCLUDED.base_url,
                     text_model=EXCLUDED.text_model,vision_model=EXCLUDED.vision_model,coding_model=EXCLUDED.coding_model,
                     revision=EXCLUDED.revision,updated_by=EXCLUDED.updated_by,updated_at=clock_timestamp()
                   RETURNING tenant_id""",
                (ciphertext, MODEL_PROVIDER, base_url, models["text"], models["vision"], models["coding"], revision, operator, _tenant(tenant_id)),
            ).fetchone()
        if not row:
            raise ModelCredentialError("tenant_not_found")
        return self.public(tenant_id)

    def delete(self, tenant_id: str) -> dict:
        with self.product._conn() as conn:
            conn.execute("DELETE FROM product.tenant_model_credentials WHERE tenant_id=%s", (_tenant(tenant_id),))
        return self.public(tenant_id)
