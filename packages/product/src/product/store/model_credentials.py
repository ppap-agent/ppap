"""Tenant-bound encrypted personal credentials, separate from shared Checkers."""

from __future__ import annotations

import json
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from documents.model_access import DEEPSEEK_MODEL, DEEPSEEK_PROVIDER, ModelAccess, ModelUnavailable
from pydantic import SecretStr


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


def encrypt_credential(settings, tenant_id: str, user_id: str, api_key: SecretStr) -> str:
    tenant_id, user_id = _identity(tenant_id, user_id)
    key = api_key.get_secret_value().strip()
    if not 1 <= len(key) <= 4096 or any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise ModelCredentialError("invalid_model_credential")
    # Authenticated envelope binds ciphertext to owner: copying rows cannot lend
    # another member's key. No key fingerprint is exposed or written to logs.
    body = json.dumps({"tenant_id": tenant_id, "user_id": user_id, "provider": DEEPSEEK_PROVIDER, "api_key": key})
    return _cipher(settings).encrypt(body.encode()).decode("ascii")


def decrypt_credential(settings, tenant_id: str, user_id: str, ciphertext: str) -> ModelAccess:
    tenant_id, user_id = _identity(tenant_id, user_id)
    try:
        body = json.loads(_cipher(settings).decrypt(ciphertext.encode("ascii")))
        if body["tenant_id"] != tenant_id or body["user_id"] != user_id or body["provider"] != DEEPSEEK_PROVIDER:
            raise ValueError()
        key = body["api_key"]
        if not isinstance(key, str) or not key:
            raise ValueError()
        return ModelAccess(api_key=SecretStr(key))
    except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError):
        raise ModelUnavailable() from None


def public_config(updated_at=None) -> dict:
    return {"provider": DEEPSEEK_PROVIDER, "model": DEEPSEEK_MODEL, "configured": updated_at is not None, "updated_at": updated_at.isoformat() if updated_at else None}


class PersonalModelStore:
    def __init__(self, product):
        self.product = product

    def config(self, principal) -> dict:
        with self.product._conn() as conn:
            row = conn.execute(
                """SELECT c.updated_at FROM product.personal_model_credentials c
                   JOIN product.memberships m USING (user_id, tenant_id)
                   WHERE c.tenant_id=%s AND c.user_id=%s""",
                (principal.tenant_id, principal.user_id),
            ).fetchone()
        return public_config(row[0] if row else None)

    def put(self, principal, api_key: SecretStr) -> dict:
        ciphertext = encrypt_credential(self.product.settings, principal.tenant_id, principal.user_id, api_key)
        with self.product._conn() as conn:
            row = conn.execute(
                """INSERT INTO product.personal_model_credentials (tenant_id,user_id,key_ciphertext)
                   SELECT tenant_id,user_id,%s FROM product.memberships WHERE tenant_id=%s AND user_id=%s
                   ON CONFLICT (tenant_id,user_id) DO UPDATE SET key_ciphertext=EXCLUDED.key_ciphertext,updated_at=clock_timestamp()
                   RETURNING updated_at""",
                (ciphertext, principal.tenant_id, principal.user_id),
            ).fetchone()
        if not row:
            raise ModelUnavailable()
        return public_config(row[0])

    def delete(self, principal) -> dict:
        with self.product._conn() as conn:
            conn.execute("DELETE FROM product.personal_model_credentials WHERE tenant_id=%s AND user_id=%s", (principal.tenant_id, principal.user_id))
        return public_config()

    def access(self, tenant_id: str, initiating_user_id: str) -> ModelAccess:
        tenant_id, initiating_user_id = _identity(tenant_id, initiating_user_id)
        with self.product._conn() as conn:
            row = conn.execute(
                """SELECT c.key_ciphertext FROM product.personal_model_credentials c
                   JOIN product.memberships m USING (user_id,tenant_id)
                   WHERE c.tenant_id=%s AND c.user_id=%s""",
                (tenant_id, initiating_user_id),
            ).fetchone()
        if not row:
            raise ModelUnavailable()
        return decrypt_credential(self.product.settings, tenant_id, initiating_user_id, row[0])
