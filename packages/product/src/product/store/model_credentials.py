"""Tenant-bound encrypted personal credentials, separate from shared Checkers."""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from dataclasses import replace
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from documents.model_access import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_PROVIDER, ModelAccess, ModelUnavailable
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


def _identity(tenant_id: str, user_id: str) -> tuple[str, str]:
    try:
        return str(UUID(tenant_id)), str(UUID(user_id))
    except (ValueError, AttributeError, TypeError):
        raise ModelUnavailable() from None


def _configuration(settings, base_url: str, model: str) -> tuple[str, str, str]:
    try:
        endpoint = approved_base_url(settings, base_url)
        return DEEPSEEK_PROVIDER if endpoint == DEEPSEEK_BASE_URL else "responses-compatible", endpoint, valid_model(model)
    except ModelEndpointError as exc:
        raise ModelCredentialError(exc.code) from None


def encrypt_credential(settings, tenant_id: str, user_id: str, api_key: SecretStr, *, base_url: str = DEEPSEEK_BASE_URL, model: str = DEEPSEEK_MODEL, revision: str = "") -> str:
    tenant_id, user_id = _identity(tenant_id, user_id)
    provider, endpoint, model = _configuration(settings, base_url, model)
    key = api_key.get_secret_value().strip()
    if not 1 <= len(key) <= 4096 or any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise ModelCredentialError("invalid_model_credential")
    # Bind the owner AND destination to the secret. Editing metadata or copying
    # rows cannot redirect a stored key. A replacement requires a complete key.
    body = json.dumps({"tenant_id": tenant_id, "user_id": user_id, "provider": provider, "api_key": key, "base_url": endpoint, "model": model, "revision": revision})
    return _cipher(settings).encrypt(body.encode()).decode("ascii")


def decrypt_credential(settings, tenant_id: str, user_id: str, ciphertext: str, *, expected_identity: dict | None = None) -> ModelAccess:
    tenant_id, user_id = _identity(tenant_id, user_id)
    try:
        body = json.loads(_cipher(settings).decrypt(ciphertext.encode("ascii")))
        if body["tenant_id"] != tenant_id or body["user_id"] != user_id:
            raise ValueError()
        key = body["api_key"]
        if not isinstance(key, str) or not key:
            raise ValueError()
        # The original envelope had no endpoint fields. It remains valid only
        # for its original official destination; migration never moves its key.
        legacy = not any(name in body for name in ("base_url", "model", "revision"))
        if not legacy and not all(name in body for name in ("base_url", "model", "revision")):
            raise ValueError()
        provider, endpoint, model = _configuration(settings, body.get("base_url", DEEPSEEK_BASE_URL), body.get("model", DEEPSEEK_MODEL))
        if provider != body["provider"]:
            raise ValueError()
        access = ModelAccess(api_key=SecretStr(key), provider=provider, base_url=endpoint, model=model, revision=body.get("revision", ""))
        if expected_identity is not None:
            if legacy:
                access = replace(access, revision=expected_identity["revision"])
            if access.identity() != expected_identity:
                raise ValueError()
        return access
    except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError):
        raise ModelUnavailable() from None


def public_config(updated_at=None, *, provider=DEEPSEEK_PROVIDER, model=DEEPSEEK_MODEL, base_url=DEEPSEEK_BASE_URL, revision: str | None = None) -> dict:
    return {"provider": provider, "model": model, "base_url": base_url, "configured": updated_at is not None, "revision": revision, "updated_at": updated_at.isoformat() if updated_at else None}


def _metadata_identity(row) -> dict[str, str]:
    if row is None:
        return {"provider": DEEPSEEK_PROVIDER, "model": DEEPSEEK_MODEL, "revision": "unconfigured", "deployment": hashlib.sha256(DEEPSEEK_BASE_URL.encode()).hexdigest()}
    return {"provider": row[0], "model": row[1], "deployment": hashlib.sha256(row[2].encode()).hexdigest(), "revision": str(row[3])}


class PersonalModelStore:
    def __init__(self, product):
        self.product = product

    def config(self, principal) -> dict:
        with self.product._conn() as conn:
            row = conn.execute(
                """SELECT c.updated_at,c.provider,c.model,c.base_url,c.revision FROM product.personal_model_credentials c
                   JOIN product.memberships m USING (user_id, tenant_id)
                   WHERE c.tenant_id=%s AND c.user_id=%s""",
                (principal.tenant_id, principal.user_id),
            ).fetchone()
        return public_config(row[0], provider=row[1], model=row[2], base_url=row[3], revision=str(row[4])) if row else public_config()

    def identity(self, tenant_id: str, initiating_user_id: str, *, conn=None) -> dict[str, str]:
        tenant_id, initiating_user_id = _identity(tenant_id, initiating_user_id)
        with nullcontext(conn) if conn is not None else self.product._conn() as connection:
            row = connection.execute(
                """SELECT c.provider,c.model,c.base_url,c.revision FROM product.personal_model_credentials c
                   JOIN product.memberships m USING (user_id,tenant_id)
                   WHERE c.tenant_id=%s AND c.user_id=%s""",
                (tenant_id, initiating_user_id),
            ).fetchone()
        return _metadata_identity(row)

    def put(self, principal, api_key: SecretStr, *, base_url: str = DEEPSEEK_BASE_URL, model: str = DEEPSEEK_MODEL) -> dict:
        provider, endpoint, model = _configuration(self.product.settings, base_url, model)
        revision = str(uuid4())
        ciphertext = encrypt_credential(self.product.settings, principal.tenant_id, principal.user_id, api_key, base_url=endpoint, model=model, revision=revision)
        with self.product._conn() as conn:
            row = conn.execute(
                """INSERT INTO product.personal_model_credentials (tenant_id,user_id,key_ciphertext,provider,model,base_url,revision)
                   SELECT tenant_id,user_id,%s,%s,%s,%s,%s FROM product.memberships WHERE tenant_id=%s AND user_id=%s
                   ON CONFLICT (tenant_id,user_id) DO UPDATE SET
                     key_ciphertext=EXCLUDED.key_ciphertext,provider=EXCLUDED.provider,model=EXCLUDED.model,
                     base_url=EXCLUDED.base_url,revision=EXCLUDED.revision,updated_at=clock_timestamp()
                   RETURNING updated_at""",
                (ciphertext, provider, model, endpoint, revision, principal.tenant_id, principal.user_id),
            ).fetchone()
        if not row:
            raise ModelUnavailable()
        return public_config(row[0], provider=provider, model=model, base_url=endpoint, revision=revision)

    def delete(self, principal) -> dict:
        with self.product._conn() as conn:
            conn.execute("DELETE FROM product.personal_model_credentials WHERE tenant_id=%s AND user_id=%s", (principal.tenant_id, principal.user_id))
        return public_config()

    def access(self, tenant_id: str, initiating_user_id: str) -> ModelAccess:
        tenant_id, initiating_user_id = _identity(tenant_id, initiating_user_id)
        with self.product._conn() as conn:
            row = conn.execute(
                """SELECT c.provider,c.model,c.base_url,c.revision,c.key_ciphertext FROM product.personal_model_credentials c
                   JOIN product.memberships m USING (user_id,tenant_id)
                   WHERE c.tenant_id=%s AND c.user_id=%s""",
                (tenant_id, initiating_user_id),
            ).fetchone()
        if not row:
            raise ModelUnavailable()
        return decrypt_credential(self.product.settings, tenant_id, initiating_user_id, row[4], expected_identity=_metadata_identity(row))
