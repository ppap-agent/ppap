from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from psycopg.types.json import Jsonb

from ppap_core.audits import (
    ClaimedJob,
    DRAFT_ACCEPTED,
    DRAFT_OPEN,
    EXEC_COMPLETED,
    EXEC_FAILED,
    EXEC_QUEUED,
    EXEC_RUNNING,
    RoundInput,
    RoundWork,
    normalize_idempotency_key,
    normalize_review,
    normalize_role,
    request_fingerprint,
    require_targets,
)
from ppap_core.db import connect
from ppap_core.files import (
    FAILED,
    MAX_UPLOAD_BYTES,
    READY,
    STAGING,
    FileVersion,
    ObjectExists,
    ObjectMissing,
    original_filename,
    safe_content_type,
    sha256_bytes,
    version_object_key,
)
from ppap_core.fixtures import (
    APP_ID,
    PRESENCE_RULE_BODY,
    RESOURCE_A,
    RESOURCE_B,
    RULE_A,
    RULE_B,
    SUBJECTS,
    TENANT_A,
    TENANT_B,
    USER_ALICE,
    USER_BOB,
    USER_CAROL,
)
from ppap_core.feishu import UserTokens
from ppap_core.identity import (
    PROVIDER_FEISHU,
    Principal,
    Resource,
    Session,
    can_read,
    identity_key,
    new_token,
    token_hash,
)
from ppap_core.settings import Settings
from ppap_core.storage import S3Objects


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


class AuthStore:
    def __init__(self, settings: Settings, objects=None, role: str = "api") -> None:
        self.settings = settings
        self.objects = objects or S3Objects(settings)
        self.role = role

    def _conn(self) -> psycopg.Connection:
        return connect(self.settings, self.role)

    def save_state(self, raw_state: str, return_to: str, ttl_seconds: int = 600) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO product.oauth_states (state_hash, return_to, expires_at)
                VALUES (%s, %s, %s)
                """,
                (token_hash(raw_state), return_to, _now() + timedelta(seconds=ttl_seconds)),
            )

    def consume_state(self, raw_state: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                UPDATE product.oauth_states
                SET consumed_at = now()
                WHERE state_hash = %s AND consumed_at IS NULL AND expires_at > now()
                RETURNING return_to
                """,
                (token_hash(raw_state),),
            ).fetchone()
        return row[0] if row else None

    def save_app_ticket(self, app_id: str, ticket: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO product.app_tickets (provider, app_id, ticket)
                VALUES (%s, %s, %s)
                ON CONFLICT (provider, app_id) DO UPDATE
                  SET ticket = EXCLUDED.ticket, received_at = now()
                """,
                (PROVIDER_FEISHU, app_id, ticket),
            )

    def has_app_ticket(self, app_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM product.app_tickets WHERE provider = %s AND app_id = %s",
                (PROVIDER_FEISHU, app_id),
            ).fetchone()
        return bool(row)

    def app_ticket(self, app_id: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT ticket FROM product.app_tickets WHERE provider = %s AND app_id = %s",
                (PROVIDER_FEISHU, app_id),
            ).fetchone()
        return str(row[0]) if row and row[0] else None

    def upsert_feishu_identity(self, tenant_key: str, subject: str, display_name: str = "") -> Principal:
        provider, app_id, tenant_key, subject = identity_key(
            PROVIDER_FEISHU, self.settings.feishu_app_id, tenant_key, subject
        )
        for _ in range(3):
            try:
                return self._upsert_feishu_identity_once(provider, app_id, tenant_key, subject, display_name)
            except UniqueViolation:
                continue
        raise RuntimeError("identity_upsert_conflict")

    def _upsert_feishu_identity_once(
        self, provider: str, app_id: str, tenant_key: str, subject: str, display_name: str
    ) -> Principal:
        with self._conn() as conn:
            with conn.transaction():
                existing = conn.execute(
                    """
                    SELECT i.user_id, m.tenant_id, m.role, i.display_name
                    FROM product.identities i
                    JOIN product.memberships m ON m.user_id = i.user_id
                    JOIN product.tenants t ON t.id = m.tenant_id
                        AND t.provider = i.provider AND t.external_key = i.tenant_key
                    WHERE i.provider = %s AND i.app_id = %s AND i.tenant_key = %s AND i.subject = %s
                    """,
                    (provider, app_id, tenant_key, subject),
                ).fetchone()
                if existing:
                    user_id, tenant_id, role, name = existing
                    return Principal(str(user_id), str(tenant_id), provider, app_id, tenant_key, subject, role, name)
                tenant_id = conn.execute(
                    """
                    INSERT INTO product.tenants (id, provider, external_key, display_name)
                    VALUES (gen_random_uuid(), %s, %s, %s)
                    ON CONFLICT (provider, external_key) DO UPDATE SET display_name = EXCLUDED.display_name
                    RETURNING id
                    """,
                    (provider, tenant_key, "feishu:" + tenant_key),
                ).fetchone()[0]
                user_id = conn.execute(
                    "INSERT INTO product.users (id) VALUES (gen_random_uuid()) RETURNING id"
                ).fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO product.identities (id, user_id, provider, app_id, tenant_key, subject, display_name)
                    VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, provider, app_id, tenant_key, subject, display_name),
                )
                conn.execute(
                    """
                    INSERT INTO product.memberships (user_id, tenant_id, role)
                    VALUES (%s, %s, 'member')
                    ON CONFLICT (user_id, tenant_id) DO NOTHING
                    """,
                    (user_id, tenant_id),
                )
                return Principal(
                    str(user_id), str(tenant_id), provider, app_id, tenant_key, subject, "member", display_name
                )

    def create_session(self, principal: Principal, tokens: UserTokens | None = None, ttl_seconds: int = 43200) -> str:
        raw = new_token()
        now = _now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        access_token = refresh_token = None
        access_expires_at = refresh_expires_at = None
        if tokens is not None:
            access_token = tokens.access_token
            refresh_token = tokens.refresh_token or None
            access_expires_at = tokens.access_expires_at
            refresh_expires_at = tokens.refresh_expires_at
            bound = tokens.refresh_expires_at if tokens.refresh_token else tokens.access_expires_at
            if bound < expires_at:
                expires_at = bound
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO product.sessions (
                    token_hash, user_id, tenant_id, expires_at,
                    access_token, refresh_token, access_expires_at, refresh_expires_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    token_hash(raw), principal.user_id, principal.tenant_id, expires_at,
                    access_token, refresh_token, access_expires_at, refresh_expires_at,
                ),
            )
        return raw

    def load_session(self, raw: str) -> Session | None:
        with self._conn() as conn:
            conn.row_factory = dict_row
            row = conn.execute(
                """
                SELECT s.user_id, s.tenant_id, s.expires_at, s.access_token, s.refresh_token,
                       s.access_expires_at, s.refresh_expires_at,
                       i.provider, i.app_id, i.tenant_key, i.subject, m.role, i.display_name
                FROM product.sessions s
                JOIN product.tenants t ON t.id = s.tenant_id
                JOIN product.identities i ON i.user_id = s.user_id
                    AND i.provider = t.provider AND i.tenant_key = t.external_key
                JOIN product.memberships m ON m.user_id = s.user_id AND m.tenant_id = s.tenant_id
                WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > now()
                LIMIT 1
                """,
                (token_hash(raw),),
            ).fetchone()
        if not row:
            return None
        principal = Principal(
            str(row["user_id"]),
            str(row["tenant_id"]),
            row["provider"],
            row["app_id"],
            row["tenant_key"],
            row["subject"],
            row["role"],
            row["display_name"],
        )
        return Session(
            principal,
            row["expires_at"],
            row["access_token"] or "",
            row["refresh_token"] or "",
            row["access_expires_at"],
            row["refresh_expires_at"],
        )

    def principal_from_token(self, raw: str) -> Principal | None:
        session = self.load_session(raw)
        return session.principal if session else None

    def update_provider_auth(self, raw: str, tokens: UserTokens) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE product.sessions
                SET access_token = %s, refresh_token = %s, access_expires_at = %s, refresh_expires_at = %s
                WHERE token_hash = %s AND revoked_at IS NULL
                """,
                (
                    tokens.access_token,
                    tokens.refresh_token or None,
                    tokens.access_expires_at,
                    tokens.refresh_expires_at,
                    token_hash(raw),
                ),
            )

    def revoke_session(self, raw: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE product.sessions SET revoked_at = now() WHERE token_hash = %s AND revoked_at IS NULL",
                (token_hash(raw),),
            )

    def resource(self, resource_id: str) -> Resource | None:
        parsed = _as_uuid(resource_id)
        if not parsed:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, tenant_id, kind FROM product.resources WHERE id = %s",
                (parsed,),
            ).fetchone()
        if not row:
            return None
        return Resource(str(row[0]), str(row[1]), row[2])

    def grants(self, resource_id: str, user_id: str) -> set[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT capability FROM product.resource_grants WHERE resource_id = %s AND user_id = %s",
                (resource_id, user_id),
            ).fetchall()
        return {row[0] for row in rows}

    def seed_isolated_dev(self) -> None:
        with self._conn() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO product.tenants (id, provider, external_key, display_name)
                    VALUES (%s, %s, %s, %s), (%s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (TENANT_A, PROVIDER_FEISHU, "isolated-dev-a", "isolated-dev-a",
                     TENANT_B, PROVIDER_FEISHU, "isolated-dev-b", "isolated-dev-b"),
                )
                for user_id in (USER_ALICE, USER_BOB, USER_CAROL):
                    conn.execute(
                        "INSERT INTO product.users (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
                        (user_id,),
                    )
                for name, spec in SUBJECTS.items():
                    conn.execute(
                        """
                        INSERT INTO product.identities (id, user_id, provider, app_id, tenant_key, subject, display_name)
                        VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (provider, app_id, tenant_key, subject)
                        DO UPDATE SET display_name = EXCLUDED.display_name
                        """,
                        (spec["user_id"], PROVIDER_FEISHU, APP_ID, spec["tenant_key"], spec["subject"], name),
                    )
                    conn.execute(
                        """
                        INSERT INTO product.memberships (user_id, tenant_id, role)
                        VALUES (%s, %s, 'member')
                        ON CONFLICT (user_id, tenant_id) DO NOTHING
                        """,
                        (spec["user_id"], spec["tenant_id"]),
                    )
                conn.execute(
                    """
                    INSERT INTO product.resources (id, tenant_id, kind)
                    VALUES (%s, %s, 'fixture'), (%s, %s, 'fixture')
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (RESOURCE_A, TENANT_A, RESOURCE_B, TENANT_B),
                )
                conn.execute(
                    """
                    INSERT INTO product.resource_grants (resource_id, user_id, capability) VALUES
                      (%s, %s, 'read'), (%s, %s, 'review'),
                      (%s, %s, 'read'),
                      (%s, %s, 'read'), (%s, %s, 'review')
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        RESOURCE_A, USER_ALICE, RESOURCE_A, USER_ALICE,
                        RESOURCE_A, USER_BOB,
                        RESOURCE_B, USER_CAROL, RESOURCE_B, USER_CAROL,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO product.rule_versions (id, tenant_id, title, body)
                    VALUES (%s, %s, %s, %s), (%s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        RULE_A, TENANT_A, "资料就绪", Jsonb(PRESENCE_RULE_BODY),
                        RULE_B, TENANT_B, "资料就绪", Jsonb(PRESENCE_RULE_BODY),
                    ),
                )

    def principal_for_fixture(self, name: str) -> Principal:
        spec = SUBJECTS[name]
        return Principal(
            spec["user_id"],
            spec["tenant_id"],
            PROVIDER_FEISHU,
            APP_ID,
            spec["tenant_key"],
            spec["subject"],
            "member",
            name,
        )

    def _version_from_row(self, row) -> FileVersion:
        return FileVersion(
            str(row[0]),
            str(row[1]),
            str(row[2]),
            row[3],
            row[4],
            int(row[5]),
            row[7],
            row[8],
            row[6],
        )

    def create_staging(
        self,
        principal: Principal,
        filename: str,
        content_type: str,
        declared_size: int,
        file_id: str | None = None,
    ) -> FileVersion:
        name = original_filename(filename)
        declared = int(declared_size)
        if declared < 0 or declared > MAX_UPLOAD_BYTES:
            raise ValueError("invalid_size")
        ctype = safe_content_type(content_type)
        if file_id:
            parsed = _as_uuid(file_id)
            if not parsed:
                raise KeyError("missing")
            resource = self.resource(parsed)
            if resource is None or resource.kind != "file":
                raise KeyError("missing")
            fid = parsed
            version_id = str(uuid.uuid4())
            key = version_object_key(principal.tenant_id, fid, version_id)
            with self._conn() as conn:
                with conn.transaction():
                    owner = conn.execute(
                        "SELECT tenant_id FROM product.files WHERE id = %s",
                        (fid,),
                    ).fetchone()
                    if not owner or str(owner[0]) != principal.tenant_id:
                        raise KeyError("missing")
                    conn.execute(
                        """
                        INSERT INTO product.file_versions
                            (id, file_id, tenant_id, original_filename, content_type, byte_size, object_key, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (version_id, fid, principal.tenant_id, name, ctype, declared, key, STAGING),
                    )
            return FileVersion(version_id, fid, principal.tenant_id, name, ctype, declared, key, STAGING)
        fid = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        key = version_object_key(principal.tenant_id, fid, version_id)
        with self._conn() as conn:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO product.resources (id, tenant_id, kind) VALUES (%s, %s, 'file')",
                    (fid, principal.tenant_id),
                )
                conn.execute(
                    """
                    INSERT INTO product.files (id, tenant_id, created_by, original_filename)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (fid, principal.tenant_id, principal.user_id, name),
                )
                conn.execute(
                    """
                    INSERT INTO product.file_versions
                        (id, file_id, tenant_id, original_filename, content_type, byte_size, object_key, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (version_id, fid, principal.tenant_id, name, ctype, declared, key, STAGING),
                )
                conn.execute(
                    """
                    INSERT INTO product.resource_grants (resource_id, user_id, capability)
                    VALUES (%s, %s, 'read')
                    ON CONFLICT DO NOTHING
                    """,
                    (fid, principal.user_id),
                )
        return FileVersion(version_id, fid, principal.tenant_id, name, ctype, declared, key, STAGING)

    def get_version(self, file_id: str, version_id: str) -> FileVersion | None:
        fid, vid = _as_uuid(file_id), _as_uuid(version_id)
        if not fid or not vid:
            return None
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id, file_id, tenant_id, original_filename, content_type, byte_size, sha256, object_key, status
                FROM product.file_versions
                WHERE file_id = %s AND id = %s
                """,
                (fid, vid),
            ).fetchone()
        return self._version_from_row(row) if row else None

    def put_blob(self, version: FileVersion, body: bytes) -> FileVersion:
        if version.status != STAGING:
            raise ObjectExists()
        if len(body) > MAX_UPLOAD_BYTES:
            raise ValueError("invalid_size")
        current = self.get_version(version.file_id, version.id)
        if current is None or current.status != STAGING:
            raise ObjectExists()
        self.objects.put_exclusive(current.object_key, body, current.content_type)
        with self._conn() as conn:
            row = conn.execute(
                """
                UPDATE product.file_versions
                SET byte_size = %s
                WHERE id = %s AND file_id = %s AND status = %s
                RETURNING id, file_id, tenant_id, original_filename, content_type, byte_size, sha256, object_key, status
                """,
                (len(body), current.id, current.file_id, STAGING),
            ).fetchone()
        if row is None:
            latest = self.get_version(version.file_id, version.id)
            if latest is None or latest.status != READY:
                try:
                    self.objects.delete(current.object_key)
                except Exception:
                    pass
            raise ObjectExists()
        return self._version_from_row(row)

    def finalize_version(self, version: FileVersion, expected_sha256: str | None = None) -> FileVersion:
        if version.status == READY:
            if expected_sha256 and version.sha256 and expected_sha256 != version.sha256:
                raise ValueError("sha256_conflict")
            return version
        if version.status != STAGING:
            raise ValueError("not_staging")
        try:
            body = self.objects.get(version.object_key)
        except ObjectMissing as exc:
            raise ValueError("missing_object") from exc
        digest = sha256_bytes(body)
        if expected_sha256 and expected_sha256 != digest:
            with self._conn() as conn:
                conn.execute(
                    """
                    UPDATE product.file_versions
                    SET status = %s, sha256 = %s, byte_size = %s
                    WHERE id = %s AND status = %s
                    """,
                    (FAILED, digest, len(body), version.id, STAGING),
                )
            raise ValueError("sha256_mismatch")
        with self._conn() as conn:
            row = conn.execute(
                """
                UPDATE product.file_versions
                SET status = %s, sha256 = %s, byte_size = %s, finalized_at = now()
                WHERE id = %s AND status = %s
                RETURNING id, file_id, tenant_id, original_filename, content_type, byte_size, sha256, object_key, status
                """,
                (READY, digest, len(body), version.id, STAGING),
            ).fetchone()
        if row:
            return self._version_from_row(row)
        current = self.get_version(version.file_id, version.id)
        if current and current.status == READY and current.sha256 == digest:
            return current
        raise ValueError("finalize_conflict")

    def version_bytes(self, version: FileVersion) -> bytes:
        if version.status != READY or not version.sha256:
            raise ValueError("not_ready")
        body = self.objects.get(version.object_key)
        if sha256_bytes(body) != version.sha256:
            raise ValueError("object_mutated")
        return body

    def reap_staging(self, max_age_seconds: int) -> int:
        if max_age_seconds < 0:
            raise ValueError("invalid_age")
        with self._conn() as conn:
            rows = conn.execute(
                """
                UPDATE product.file_versions
                SET status = %s
                WHERE status = %s AND created_at < now() - make_interval(secs => %s)
                RETURNING object_key
                """,
                (FAILED, STAGING, max_age_seconds),
            ).fetchall()
        for row in rows:
            try:
                self.objects.delete(row[0])
            except Exception:
                continue
        return len(rows)

    def list_rules(self, tenant_id: str) -> list[dict[str, str]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, title, body FROM product.rule_versions WHERE tenant_id = %s ORDER BY title, id",
                (tenant_id,),
            ).fetchall()
        out = []
        for row in rows:
            body = row[2] or {}
            out.append({"id": str(row[0]), "title": row[1], "kind": str(body.get("kind") or "engine")})
        return out

    def create_draft(self, principal: Principal) -> dict:
        draft_id = str(uuid.uuid4())
        with self._conn() as conn:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO product.resources (id, tenant_id, kind) VALUES (%s, %s, 'audit_draft')",
                    (draft_id, principal.tenant_id),
                )
                conn.execute(
                    """
                    INSERT INTO product.audit_drafts (id, tenant_id, created_by, status)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (draft_id, principal.tenant_id, principal.user_id, DRAFT_OPEN),
                )
                conn.execute(
                    """
                    INSERT INTO product.resource_grants (resource_id, user_id, capability)
                    VALUES (%s, %s, 'read'), (%s, %s, 'review')
                    ON CONFLICT DO NOTHING
                    """,
                    (draft_id, principal.user_id, draft_id, principal.user_id),
                )
        return self.get_draft(draft_id)

    def get_draft(self, draft_id: str) -> dict | None:
        parsed = _as_uuid(draft_id)
        if not parsed:
            return None
        with self._conn() as conn:
            return self._draft_public(conn, parsed)

    def save_draft(self, principal: Principal, draft_id: str, rule_version_id: str | None, items: list[dict] | None) -> dict:
        parsed = _as_uuid(draft_id)
        if not parsed:
            raise KeyError("missing")
        with self._conn() as conn:
            with conn.transaction():
                draft = conn.execute(
                    """
                    SELECT id, tenant_id, status FROM product.audit_drafts
                    WHERE id = %s FOR UPDATE
                    """,
                    (parsed,),
                ).fetchone()
                if not draft or str(draft[1]) != principal.tenant_id:
                    raise KeyError("missing")
                if draft[2] != DRAFT_OPEN:
                    raise ValueError("draft_already_accepted")
                if rule_version_id is not None:
                    rid = _as_uuid(rule_version_id)
                    if not rid or not self._rule_for_tenant(conn, rid, principal.tenant_id):
                        raise ValueError("unknown_rule")
                    conn.execute(
                        """
                        UPDATE product.audit_drafts
                        SET rule_version_id = %s, updated_at = now()
                        WHERE id = %s
                        """,
                        (rid, parsed),
                    )
                if items is not None:
                    prepared = self._prepared_items(conn, principal, items)
                    conn.execute("DELETE FROM product.draft_items WHERE draft_id = %s", (parsed,))
                    for row in prepared:
                        conn.execute(
                            """
                            INSERT INTO product.draft_items (draft_id, file_version_id, role)
                            VALUES (%s, %s, %s)
                            """,
                            (parsed, row["file_version_id"], row["role"]),
                        )
                    conn.execute("UPDATE product.audit_drafts SET updated_at = now() WHERE id = %s", (parsed,))
                return self._draft_public(conn, parsed)

    def accept_draft(self, principal: Principal, draft_id: str, idempotency_key: str) -> dict:
        key = normalize_idempotency_key(idempotency_key)
        parsed = _as_uuid(draft_id)
        if not parsed:
            raise KeyError("missing")
        with self._conn() as conn:
            with conn.transaction():
                return self._accept_locked(
                    conn, principal, key, source_draft_id=parsed, predecessor_id=None, rule_version_id=None, items=None
                )

    def round_by_idempotency(self, principal: Principal, idempotency_key: str) -> dict | None:
        key = normalize_idempotency_key(idempotency_key)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM product.audit_rounds WHERE tenant_id = %s AND idempotency_key = %s",
                (principal.tenant_id, key),
            ).fetchone()
            if not row:
                return None
            return self._round_public(conn, str(row[0]))

    def resubmit_round(
        self,
        principal: Principal,
        round_id: str,
        idempotency_key: str,
        rule_version_id: str | None,
        items: list[dict] | None,
    ) -> dict:
        key = normalize_idempotency_key(idempotency_key)
        parsed = _as_uuid(round_id)
        if not parsed:
            raise KeyError("missing")
        with self._conn() as conn:
            with conn.transaction():
                current = conn.execute(
                    """
                    SELECT id, tenant_id, rule_version_id FROM product.audit_rounds
                    WHERE id = %s FOR UPDATE
                    """,
                    (parsed,),
                ).fetchone()
                if not current or str(current[1]) != principal.tenant_id:
                    raise KeyError("missing")
                if items is None:
                    rows = conn.execute(
                        "SELECT file_version_id, role FROM product.round_inputs WHERE round_id = %s",
                        (parsed,),
                    ).fetchall()
                    items = [{"file_version_id": str(row[0]), "role": row[1]} for row in rows]
                rule = rule_version_id or str(current[2])
                return self._accept_locked(
                    conn,
                    principal,
                    key,
                    source_draft_id=None,
                    predecessor_id=parsed,
                    rule_version_id=rule,
                    items=items,
                )

    def get_round(self, round_id: str) -> dict | None:
        parsed = _as_uuid(round_id)
        if not parsed:
            return None
        with self._conn() as conn:
            return self._round_public(conn, parsed)

    def list_rounds(self, principal: Principal, limit: int = 50) -> list[dict]:
        try:
            cap = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            cap = 50
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT r.id
                FROM product.audit_rounds r
                JOIN product.resource_grants g
                    ON g.resource_id = r.id AND g.user_id = %s
                    AND g.capability IN ('read', 'review')
                WHERE r.tenant_id = %s
                GROUP BY r.id, r.created_at
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT %s
                """,
                (principal.user_id, principal.tenant_id, cap),
            ).fetchall()
            items = []
            for row in rows:
                item = self._round_history(conn, row[0])
                if item:
                    items.append(item)
            return items

    def round_input_version(self, round_id: str, file_id: str, version_id: str) -> FileVersion | None:
        rid, fid, vid = _as_uuid(round_id), _as_uuid(file_id), _as_uuid(version_id)
        if not rid or not fid or not vid:
            return None
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT fv.id, fv.file_id, fv.tenant_id, fv.original_filename, fv.content_type,
                       fv.byte_size, fv.sha256, fv.object_key, fv.status
                FROM product.round_inputs i
                JOIN product.file_versions fv ON fv.id = i.file_version_id
                WHERE i.round_id = %s AND fv.file_id = %s AND fv.id = %s
                """,
                (rid, fid, vid),
            ).fetchone()
        return self._version_from_row(row) if row else None

    def save_review(
        self, principal: Principal, round_id: str, item_key: str, judgment: str, rationale: str, next_step: str, version: int
    ) -> dict:
        text, why, nxt, seen = normalize_review(judgment, rationale, next_step, version)
        parsed = _as_uuid(round_id)
        key = str(item_key or "").strip()
        if not parsed or not key:
            raise KeyError("missing")
        with self._conn() as conn:
            with conn.transaction():
                finding = conn.execute(
                    "SELECT 1 FROM product.findings WHERE round_id = %s AND item_key = %s",
                    (parsed, key),
                ).fetchone()
                if not finding:
                    raise KeyError("missing")
                if seen == 0:
                    try:
                        conn.execute(
                            """
                            INSERT INTO product.reviews
                                (round_id, item_key, user_id, judgment, rationale, next_step, version)
                            VALUES (%s, %s, %s, %s, %s, %s, 1)
                            """,
                            (parsed, key, principal.user_id, text, why, nxt),
                        )
                    except UniqueViolation as exc:
                        raise ValueError("review_conflict") from exc
                else:
                    row = conn.execute(
                        """
                        UPDATE product.reviews
                        SET judgment = %s, rationale = %s, next_step = %s, user_id = %s,
                            version = version + 1, updated_at = now()
                        WHERE round_id = %s AND item_key = %s AND version = %s
                        RETURNING version
                        """,
                        (text, why, nxt, principal.user_id, parsed, key, seen),
                    ).fetchone()
                    if not row:
                        raise ValueError("review_conflict")
                return self._round_public(conn, parsed)

    def round_work(self, round_id: str) -> RoundWork | None:
        parsed = _as_uuid(round_id)
        if not parsed:
            return None
        with self._conn() as conn:
            head = conn.execute(
                """
                SELECT id, tenant_id, rule_title, rule_body, result_attempt
                FROM product.audit_rounds WHERE id = %s
                """,
                (parsed,),
            ).fetchone()
            if not head:
                return None
            rows = conn.execute(
                """
                SELECT i.role, fv.file_id, fv.id, fv.original_filename, fv.status, fv.object_key, fv.content_type
                FROM product.round_inputs i
                JOIN product.file_versions fv ON fv.id = i.file_version_id
                WHERE i.round_id = %s
                ORDER BY i.role, fv.original_filename
                """,
                (parsed,),
            ).fetchall()
        return RoundWork(
            str(head[0]),
            str(head[1]),
            head[2],
            head[3] or {},
            [
                RoundInput(row[0], str(row[1]), str(row[2]), row[3], row[4], row[5], row[6])
                for row in rows
            ],
            head[4],
        )

    def claim_job(self, worker_ident: str) -> ClaimedJob | None:
        with self._conn() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    SELECT id, kind, payload FROM engine.jobs
                    WHERE (
                        (status = 'queued' AND (lease_until IS NULL OR lease_until < now()))
                        OR (status = 'leased' AND lease_until IS NOT NULL AND lease_until < now())
                    )
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                updated = conn.execute(
                    """
                    UPDATE engine.jobs
                    SET status = 'leased',
                        attempt = attempt + 1,
                        lease_owner = %s,
                        lease_until = now() + interval '30 seconds',
                        updated_at = now()
                    WHERE id = %s
                    RETURNING attempt, payload
                    """,
                    (worker_ident, row[0]),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO engine.attempts (id, job_id, attempt, worker_id, diagnostic)
                    VALUES (gen_random_uuid(), %s, %s, %s, '{}'::jsonb)
                    """,
                    (row[0], updated[0], worker_ident),
                )
                payload = updated[1] or {}
                round_id = payload.get("round_id") if isinstance(payload, dict) else None
                if round_id:
                    conn.execute(
                        """
                        UPDATE product.audit_rounds
                        SET execution = %s
                        WHERE id = %s AND execution IN (%s, %s, %s)
                        """,
                        (EXEC_RUNNING, round_id, EXEC_QUEUED, EXEC_RUNNING, EXEC_FAILED),
                    )
                return ClaimedJob(str(row[0]), row[1], payload, int(updated[0]), str(round_id) if round_id else None)

    def complete_job(self, claimed: ClaimedJob, worker_ident: str, result: dict, outcome: str = "succeeded") -> bool:
        coverage = result.get("coverage") or {}
        findings = result.get("findings") or []
        execution = EXEC_COMPLETED if outcome == "succeeded" else EXEC_FAILED
        with self._conn() as conn:
            with conn.transaction():
                won = conn.execute(
                    """
                    UPDATE engine.jobs
                    SET status = %s, lease_until = NULL, updated_at = now()
                    WHERE id = %s AND attempt = %s AND lease_owner = %s AND status = 'leased'
                    RETURNING id
                    """,
                    ("succeeded" if outcome == "succeeded" else "failed", claimed.id, claimed.attempt, worker_ident),
                ).fetchone()
                conn.execute(
                    """
                    UPDATE engine.attempts
                    SET finished_at = now(), outcome = %s, diagnostic = %s
                    WHERE job_id = %s AND attempt = %s
                    """,
                    (
                        outcome,
                        Jsonb({"note": result.get("note") or "round_audit", "finding_count": len(findings)}),
                        claimed.id,
                        claimed.attempt,
                    ),
                )
                if not won or not claimed.round_id:
                    return False
                updated = conn.execute(
                    """
                    UPDATE product.audit_rounds
                    SET execution = %s, result_attempt = %s, coverage = %s
                    WHERE id = %s AND (result_attempt IS NULL OR result_attempt < %s)
                    RETURNING id
                    """,
                    (execution, claimed.attempt, Jsonb(coverage), claimed.round_id, claimed.attempt),
                ).fetchone()
                if not updated:
                    return False
                for row in findings:
                    conn.execute(
                        """
                        INSERT INTO product.findings
                            (id, round_id, item_key, classification, item, priority, status, suggestions, locator)
                        VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (round_id, item_key) DO UPDATE
                          SET classification = EXCLUDED.classification,
                              item = EXCLUDED.item,
                              priority = EXCLUDED.priority,
                              status = EXCLUDED.status,
                              suggestions = EXCLUDED.suggestions,
                              locator = EXCLUDED.locator,
                              updated_at = now()
                        """,
                        (
                            claimed.round_id,
                            row["item_key"],
                            row.get("classification") or "",
                            row.get("item") or "",
                            row.get("priority") or "",
                            row["status"],
                            row.get("suggestions") or "",
                            Jsonb(row.get("locator")) if row.get("locator") is not None else None,
                        ),
                    )
                return True

    def _rule_for_tenant(self, conn, rule_id: str, tenant_id: str):
        return conn.execute(
            "SELECT id, title, body FROM product.rule_versions WHERE id = %s AND tenant_id = %s",
            (rule_id, tenant_id),
        ).fetchone()

    def _prepared_items(self, conn, principal: Principal, items: list[dict]) -> list[dict[str, str]]:
        if not isinstance(items, list):
            raise ValueError("invalid_items")
        prepared: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                raise ValueError("invalid_items")
            vid = _as_uuid(str(raw.get("file_version_id") or ""))
            role = normalize_role(raw.get("role"))
            if not vid:
                raise ValueError("invalid_items")
            if vid in seen:
                raise ValueError("duplicate_item")
            seen.add(vid)
            row = conn.execute(
                """
                SELECT id, file_id, tenant_id, status, original_filename
                FROM product.file_versions WHERE id = %s
                """,
                (vid,),
            ).fetchone()
            if not row or str(row[2]) != principal.tenant_id:
                raise KeyError("missing")
            if row[3] != READY:
                raise ValueError("file_not_ready")
            resource = self.resource(str(row[1]))
            grants = self.grants(str(row[1]), principal.user_id)
            if resource is None or resource.kind != "file" or not can_read(principal, resource, grants):
                raise KeyError("missing")
            prepared.append({"file_version_id": str(row[0]), "role": role, "file_id": str(row[1])})
        return prepared

    def _accept_locked(
        self,
        conn,
        principal: Principal,
        key: str,
        source_draft_id: str | None,
        predecessor_id: str | None,
        rule_version_id: str | None,
        items: list[dict] | None,
    ) -> dict:
        existing = conn.execute(
            """
            SELECT id, request_fingerprint FROM product.audit_rounds
            WHERE tenant_id = %s AND idempotency_key = %s
            """,
            (principal.tenant_id, key),
        ).fetchone()
        draft_status = None
        draft_rule = None
        if source_draft_id:
            draft = conn.execute(
                """
                SELECT id, tenant_id, rule_version_id, status FROM product.audit_drafts
                WHERE id = %s FOR UPDATE
                """,
                (source_draft_id,),
            ).fetchone()
            if not draft or str(draft[1]) != principal.tenant_id:
                raise KeyError("missing")
            draft_rule = str(draft[2]) if draft[2] else None
            draft_status = draft[3]
            if items is None:
                rows = conn.execute(
                    "SELECT file_version_id, role FROM product.draft_items WHERE draft_id = %s",
                    (source_draft_id,),
                ).fetchall()
                items = [{"file_version_id": str(row[0]), "role": row[1]} for row in rows]
            if rule_version_id is None:
                rule_version_id = draft_rule
        if items is None:
            items = []
        if not rule_version_id:
            raise ValueError("rule_required")
        rid = _as_uuid(str(rule_version_id))
        rule = self._rule_for_tenant(conn, rid, principal.tenant_id) if rid else None
        if not rule:
            raise ValueError("unknown_rule")
        prepared = self._prepared_items(conn, principal, items)
        require_targets(prepared)
        fingerprint = request_fingerprint(str(rule[0]), prepared)
        if existing:
            if existing[1] != fingerprint:
                raise ValueError("idempotency_conflict")
            return self._round_public(conn, str(existing[0]))
        if source_draft_id and draft_status == DRAFT_ACCEPTED:
            raise ValueError("draft_already_accepted")
        round_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO product.resources (id, tenant_id, kind) VALUES (%s, %s, 'audit_round')",
            (round_id, principal.tenant_id),
        )
        conn.execute(
            """
            INSERT INTO product.audit_rounds (
                id, tenant_id, created_by, source_draft_id, predecessor_id,
                rule_version_id, rule_title, rule_body, idempotency_key,
                request_fingerprint, execution
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                round_id, principal.tenant_id, principal.user_id, source_draft_id, predecessor_id,
                rule[0], rule[1], Jsonb(rule[2] or {}), key, fingerprint, EXEC_QUEUED,
            ),
        )
        for row in prepared:
            conn.execute(
                "INSERT INTO product.round_inputs (round_id, file_version_id, role) VALUES (%s, %s, %s)",
                (round_id, row["file_version_id"], row["role"]),
            )
        conn.execute(
            """
            INSERT INTO product.resource_grants (resource_id, user_id, capability)
            VALUES (%s, %s, 'read'), (%s, %s, 'review')
            ON CONFLICT DO NOTHING
            """,
            (round_id, principal.user_id, round_id, principal.user_id),
        )
        conn.execute(
            """
            INSERT INTO engine.jobs (id, tenant_id, audit_round_id, kind, payload, status)
            VALUES (%s, %s, %s, 'audit', %s, 'queued')
            """,
            (job_id, principal.tenant_id, round_id, Jsonb({"round_id": round_id})),
        )
        if source_draft_id:
            conn.execute(
                """
                UPDATE product.audit_drafts
                SET status = %s, updated_at = now()
                WHERE id = %s
                """,
                (DRAFT_ACCEPTED, source_draft_id),
            )
        return self._round_public(conn, round_id)

    def _draft_public(self, conn, draft_id: str) -> dict | None:
        row = conn.execute(
            """
            SELECT d.id, d.tenant_id, d.status, d.rule_version_id, r.title
            FROM product.audit_drafts d
            LEFT JOIN product.rule_versions r ON r.id = d.rule_version_id
            WHERE d.id = %s
            """,
            (draft_id,),
        ).fetchone()
        if not row:
            return None
        items = conn.execute(
            """
            SELECT i.role, fv.id, fv.file_id, fv.original_filename, fv.status
            FROM product.draft_items i
            JOIN product.file_versions fv ON fv.id = i.file_version_id
            WHERE i.draft_id = %s
            ORDER BY i.role, fv.original_filename
            """,
            (draft_id,),
        ).fetchall()
        return {
            "id": str(row[0]),
            "status": row[2],
            "rule": {"id": str(row[3]), "title": row[4]} if row[3] else None,
            "items": [
                {
                    "role": item[0],
                    "file_version_id": str(item[1]),
                    "file_id": str(item[2]),
                    "original_filename": item[3],
                    "status": item[4],
                }
                for item in items
            ],
        }

    def _round_history(self, conn, round_id) -> dict | None:
        row = conn.execute(
            """
            SELECT id, execution, rule_version_id, rule_title, predecessor_id, created_at
            FROM product.audit_rounds WHERE id = %s
            """,
            (round_id,),
        ).fetchone()
        if not row:
            return None
        inputs = conn.execute(
            """
            SELECT i.role, fv.original_filename
            FROM product.round_inputs i
            JOIN product.file_versions fv ON fv.id = i.file_version_id
            WHERE i.round_id = %s
            ORDER BY i.role, fv.original_filename
            """,
            (round_id,),
        ).fetchall()
        return {
            "id": str(row[0]),
            "execution": row[1],
            "rule": {"id": str(row[2]), "title": row[3]},
            "predecessor_id": str(row[4]) if row[4] else None,
            "created_at": row[5].isoformat() if row[5] is not None else None,
            "inputs": [{"role": item[0], "original_filename": item[1]} for item in inputs],
        }

    def _round_public(self, conn, round_id: str) -> dict | None:
        row = conn.execute(
            """
            SELECT id, execution, rule_version_id, rule_title, coverage, predecessor_id,
                   source_draft_id, result_attempt, created_at
            FROM product.audit_rounds WHERE id = %s
            """,
            (round_id,),
        ).fetchone()
        if not row:
            return None
        inputs = conn.execute(
            """
            SELECT i.role, fv.id, fv.file_id, fv.original_filename, fv.status
            FROM product.round_inputs i
            JOIN product.file_versions fv ON fv.id = i.file_version_id
            WHERE i.round_id = %s
            ORDER BY i.role, fv.original_filename
            """,
            (round_id,),
        ).fetchall()
        findings = conn.execute(
            """
            SELECT f.item_key, f.classification, f.item, f.priority, f.status, f.suggestions, f.locator,
                   rv.judgment, rv.rationale, rv.next_step, rv.version
            FROM product.findings f
            LEFT JOIN product.reviews rv ON rv.round_id = f.round_id AND rv.item_key = f.item_key
            WHERE f.round_id = %s
            ORDER BY f.item_key
            """,
            (round_id,),
        ).fetchall()
        return {
            "id": str(row[0]),
            "execution": row[1],
            "rule": {"id": str(row[2]), "title": row[3]},
            "coverage": row[4] or {},
            "predecessor_id": str(row[5]) if row[5] else None,
            "source_draft_id": str(row[6]) if row[6] else None,
            "result_attempt": row[7],
            "created_at": row[8].isoformat() if row[8] is not None else None,
            "inputs": [
                {
                    "role": item[0],
                    "file_version_id": str(item[1]),
                    "file_id": str(item[2]),
                    "original_filename": item[3],
                    "status": item[4],
                }
                for item in inputs
            ],
            "findings": [
                {
                    "item_key": item[0],
                    "classification": item[1],
                    "item": item[2],
                    "priority": item[3],
                    "status": item[4],
                    "suggestions": item[5],
                    "locator": item[6],
                    "review": None
                    if item[7] is None
                    else {
                        "judgment": item[7],
                        "rationale": item[8],
                        "next_step": item[9],
                        "version": item[10],
                    },
                }
                for item in findings
            ],
        }

    def record_usage_event(
        self,
        principal: Principal,
        ai_item_id: str,
        consumed_points: int,
        is_free_trial: bool,
        *,
        trace_id: str | None = None,
    ) -> dict:
        item = str(ai_item_id or "").strip()
        if not item:
            raise ValueError("feishu_ai_item_missing")
        points = int(consumed_points)
        if points < 0:
            raise ValueError("invalid_usage_points")
        tid = str(trace_id or "").strip() or None
        event_id = str(uuid.uuid4())
        status = "none" if is_free_trial else "pending"
        with self._conn() as conn:
            if tid:
                existing = conn.execute(
                    """
                    SELECT id, tenant_id, user_id, ai_item_id, consumed_points, is_free_trial,
                           external_status, external_record_id, trace_id
                    FROM product.usage_events
                    WHERE tenant_id = %s AND trace_id = %s
                    """,
                    (principal.tenant_id, tid),
                ).fetchone()
                if existing:
                    return self._usage_from_row(existing)
            try:
                row = conn.execute(
                    """
                    INSERT INTO product.usage_events (
                        id, tenant_id, user_id, ai_item_id, consumed_points,
                        is_free_trial, external_status, trace_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, tenant_id, user_id, ai_item_id, consumed_points, is_free_trial,
                              external_status, external_record_id, trace_id
                    """,
                    (
                        event_id,
                        principal.tenant_id,
                        principal.user_id,
                        item,
                        points,
                        bool(is_free_trial),
                        status,
                        tid,
                    ),
                ).fetchone()
            except UniqueViolation:
                row = conn.execute(
                    """
                    SELECT id, tenant_id, user_id, ai_item_id, consumed_points, is_free_trial,
                           external_status, external_record_id, trace_id
                    FROM product.usage_events
                    WHERE tenant_id = %s AND trace_id = %s
                    """,
                    (principal.tenant_id, tid),
                ).fetchone()
        if not row:
            raise RuntimeError("usage_event_insert_failed")
        return self._usage_from_row(row)

    def trial_consumed_points(self, tenant_id: str, ai_item_id: str) -> int:
        item = str(ai_item_id or "").strip()
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(consumed_points), 0)
                FROM product.usage_events
                WHERE tenant_id = %s AND ai_item_id = %s
                  AND is_free_trial = true AND external_status = 'none'
                """,
                (tenant_id, item),
            ).fetchone()
        return int(row[0] if row else 0)

    def usage_event_public(self, row: dict) -> dict:
        return {
            "id": row["id"],
            "ai_item_id": row["ai_item_id"],
            "consumed_points": row["consumed_points"],
            "is_free_trial": row["is_free_trial"],
            "external_status": row["external_status"],
            "external_record_id": row.get("external_record_id"),
            "trace_id": row["trace_id"],
        }

    def usage_event(self, principal: Principal, event_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id, tenant_id, user_id, ai_item_id, consumed_points, is_free_trial,
                       external_status, external_record_id, trace_id
                FROM product.usage_events
                WHERE id = %s AND tenant_id = %s
                """,
                (event_id, principal.tenant_id),
            ).fetchone()
        return self._usage_from_row(row) if row else None

    def ensure_usage_trace_id(self, principal: Principal, event_id: str) -> dict:
        row = self.usage_event(principal, event_id)
        if row is None:
            raise KeyError("access_denied")
        if row.get("trace_id"):
            return row
        trace = uuid.uuid4().hex
        with self._conn() as conn:
            updated = conn.execute(
                """
                UPDATE product.usage_events
                SET trace_id = %s
                WHERE id = %s AND tenant_id = %s AND trace_id IS NULL
                RETURNING id, tenant_id, user_id, ai_item_id, consumed_points, is_free_trial,
                          external_status, external_record_id, trace_id
                """,
                (trace, event_id, principal.tenant_id),
            ).fetchone()
        if updated:
            return self._usage_from_row(updated)
        again = self.usage_event(principal, event_id)
        if again is None:
            raise KeyError("access_denied")
        return again

    def settle_usage_external(
        self, principal: Principal, event_id: str, status: str, record_id: str | None = None
    ) -> dict:
        if status not in {"pending", "confirmed", "failed"}:
            raise ValueError("invalid_external_status")
        with self._conn() as conn:
            row = conn.execute(
                """
                UPDATE product.usage_events
                SET external_status = %s, external_record_id = %s
                WHERE id = %s AND tenant_id = %s AND is_free_trial = false
                RETURNING id, tenant_id, user_id, ai_item_id, consumed_points, is_free_trial,
                          external_status, external_record_id, trace_id
                """,
                (status, record_id, event_id, principal.tenant_id),
            ).fetchone()
        if not row:
            raise KeyError("access_denied")
        return self._usage_from_row(row)

    def _usage_from_row(self, row) -> dict:
        return {
            "id": str(row[0]),
            "tenant_id": str(row[1]),
            "user_id": str(row[2]),
            "ai_item_id": row[3],
            "consumed_points": int(row[4]),
            "is_free_trial": bool(row[5]),
            "external_status": row[6],
            "external_record_id": row[7],
            "trace_id": row[8],
        }
