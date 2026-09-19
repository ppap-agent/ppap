from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ppap_core.feishu import UserTokens
from ppap_core.fixtures import SUBJECTS
from ppap_core.identity import Principal, Resource, Session, identity_key, new_token, token_hash
from ppap_core.settings import Settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryAuthStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
        self.states: dict[str, tuple[str, datetime]] = {}
        self.tickets: dict[str, str] = {}
        self.sessions: dict[str, Session] = {}
        self.revoked: set[str] = set()
        self.identities: dict[tuple[str, str, str, str], Principal] = {}
        self.resources: dict[str, Resource] = {}
        self.grant_table: dict[tuple[str, str], set[str]] = {}

    def save_state(self, raw_state: str, return_to: str, ttl_seconds: int = 600) -> None:
        self.states[token_hash(raw_state)] = (return_to, _now() + timedelta(seconds=ttl_seconds))

    def consume_state(self, raw_state: str) -> str | None:
        key = token_hash(raw_state)
        row = self.states.pop(key, None)
        if not row or row[1] <= _now():
            return None
        return row[0]

    def save_app_ticket(self, app_id: str, ticket: str) -> None:
        self.tickets[app_id] = ticket

    def has_app_ticket(self, app_id: str) -> bool:
        return app_id in self.tickets

    def upsert_feishu_identity(self, tenant_key: str, subject: str, display_name: str = "") -> Principal:
        key = identity_key("feishu", self.settings.feishu_app_id, tenant_key, subject)
        existing = self.identities.get(key)
        if existing:
            return existing
        principal = Principal(new_token(), new_token(), *key, "member", display_name)
        self.identities[key] = principal
        return principal

    def create_session(self, principal: Principal, tokens: UserTokens | None = None, ttl_seconds: int = 43200) -> str:
        raw = new_token()
        now = _now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        access_token = refresh_token = ""
        access_expires_at = refresh_expires_at = None
        if tokens is not None:
            access_token = tokens.access_token
            refresh_token = tokens.refresh_token
            access_expires_at = tokens.access_expires_at
            refresh_expires_at = tokens.refresh_expires_at
            bound = tokens.refresh_expires_at if tokens.refresh_token else tokens.access_expires_at
            if bound < expires_at:
                expires_at = bound
        self.sessions[token_hash(raw)] = Session(
            principal, expires_at, access_token, refresh_token, access_expires_at, refresh_expires_at
        )
        return raw

    def load_session(self, raw: str) -> Session | None:
        digest = token_hash(raw)
        if digest in self.revoked:
            return None
        session = self.sessions.get(digest)
        if not session or session.expires_at <= _now():
            return None
        return session

    def principal_from_token(self, raw: str) -> Principal | None:
        session = self.load_session(raw)
        return session.principal if session else None

    def update_provider_auth(self, raw: str, tokens: UserTokens) -> None:
        digest = token_hash(raw)
        session = self.sessions.get(digest)
        if not session or digest in self.revoked:
            return
        self.sessions[digest] = Session(
            session.principal,
            session.expires_at,
            tokens.access_token,
            tokens.refresh_token,
            tokens.access_expires_at,
            tokens.refresh_expires_at,
        )

    def revoke_session(self, raw: str) -> None:
        self.revoked.add(token_hash(raw))

    def resource(self, resource_id: str) -> Resource | None:
        return self.resources.get(resource_id)

    def grants(self, resource_id: str, user_id: str) -> set[str]:
        return set(self.grant_table.get((resource_id, user_id), set()))

    def seed_isolated_dev(self) -> None:
        from ppap_core.fixtures import RESOURCE_A, RESOURCE_B, TENANT_A, TENANT_B, USER_ALICE, USER_BOB, USER_CAROL

        self.resources[RESOURCE_A] = Resource(RESOURCE_A, TENANT_A, "fixture")
        self.resources[RESOURCE_B] = Resource(RESOURCE_B, TENANT_B, "fixture")
        self.grant_table[(RESOURCE_A, USER_ALICE)] = {"read", "review"}
        self.grant_table[(RESOURCE_A, USER_BOB)] = {"read"}
        self.grant_table[(RESOURCE_B, USER_CAROL)] = {"read", "review"}

    def principal_for_fixture(self, name: str) -> Principal:
        spec = SUBJECTS[name]
        return Principal(
            spec["user_id"], spec["tenant_id"], "feishu", "isolated-dev-feishu-app",
            spec["tenant_key"], spec["subject"], "member", name,
        )
