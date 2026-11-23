"""Identity keys, session tokens, and object-authorization decisions. No email merge."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from product.settings import Settings

SESSION_COOKIE = "ppap_session"
EDITION_COOKIE = "ppap_edition"
# Docs add-on host sessions are Bearer tokens that carry their channel in this prefix.
DOCS_TOKEN_PREFIX = "docs_"
DOCS_SESSION_SECONDS = 7200
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
    default = settings.canonical_web_url() + "/"
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
class Session:
    principal: Principal
    expires_at: datetime
    access_token: str = field(default="", repr=False)
    refresh_token: str = field(default="", repr=False)
    access_expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    provider_authorization_id: str = ""
    # When the person last signed in on this session; signing moments proposed from outside need it recent (#355).
    signed_in_at: datetime | None = None

    def needs_refresh(self, at: datetime) -> bool:
        if self.access_expires_at is None:
            return False
        if self.access_expires_at > at:
            return False
        if not self.refresh_token:
            return False
        return self.refresh_expires_at is None or self.refresh_expires_at > at

    def provider_auth_dead(self, at: datetime) -> bool:
        if self.access_expires_at is None:
            return False
        if self.access_expires_at > at:
            return False
        return not self.needs_refresh(at)


class ProviderAuthError(ValueError):
    def __init__(self, code: str, status: int = 401):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Resource:
    id: str
    tenant_id: str
    kind: str
    # Who created the draft, round or file; the one person who may change it.
    created_by: str | None = None


# Audit data has two rules. Every member of an enterprise sees its drafts, rounds and files; only
# the person who created one changes it, or anything that hangs on it (reviews, confirmed facts,
# writeback, release, reports). Templates are read through their own surface, never as a resource.
def can_read(principal: Principal, resource: Resource) -> bool:
    return principal.tenant_id == resource.tenant_id and resource.kind != "template"


def can_modify(principal: Principal, resource: Resource) -> bool:
    return can_read(principal, resource) and resource.created_by == principal.user_id


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
