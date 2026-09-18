"""Identity keys, session tokens, and object-authorization decisions. No email merge."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

from ppap_core.settings import Settings

SESSION_COOKIE = "ppap_session"
PROVIDER_FEISHU = "feishu"


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def identity_key(provider: str, app_id: str, tenant_key: str, subject: str) -> tuple[str, str, str, str]:
    parts = (provider.strip(), app_id.strip(), tenant_key.strip(), subject.strip())
    if not all(parts):
        raise ValueError("identity_requires_provider_app_tenant_subject")
    return parts


def same_origin(url: str, allowed: list[str]) -> bool:
    try:
        got = urlsplit(url)
    except ValueError:
        return False
    if got.username or got.password or got.scheme not in ("http", "https") or not got.hostname:
        return False
    for base in allowed:
        allowed_parts = urlsplit(base)
        if (got.scheme, got.hostname, got.port or (443 if got.scheme == "https" else 80)) == (
            allowed_parts.scheme,
            allowed_parts.hostname,
            allowed_parts.port or (443 if allowed_parts.scheme == "https" else 80),
        ):
            return True
    return False


def safe_return_to(candidate: str, settings: Settings) -> str:
    default = settings.web_origins()[0] + "/"
    if not candidate.strip():
        return default
    if not same_origin(candidate, settings.web_origins()):
        return default
    return candidate


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    provider: str
    app_id: str
    tenant_key: str
    subject: str
    membership: str
    display_name: str = ""

    def public(self) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "membership": self.membership,
            "display_name": self.display_name,
            "identity": {
                "provider": self.provider,
                "app_id": self.app_id,
                "tenant_key": self.tenant_key,
                "subject": self.subject,
            },
        }


@dataclass(frozen=True)
class Resource:
    id: str
    tenant_id: str
    kind: str


def can_read(principal: Principal, resource: Resource, grants: set[str]) -> bool:
    if principal.tenant_id != resource.tenant_id:
        return False
    return bool(grants & {"read", "review"})


def can_review(principal: Principal, resource: Resource, grants: set[str]) -> bool:
    if not can_read(principal, resource, grants):
        return False
    return "review" in grants


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
