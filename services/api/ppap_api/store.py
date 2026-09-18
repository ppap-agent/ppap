from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

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
    sha256_bytes,
    version_object_key,
)
from ppap_core.fixtures import (
    APP_ID,
    RESOURCE_A,
    RESOURCE_B,
    SUBJECTS,
    TENANT_A,
    TENANT_B,
    USER_ALICE,
    USER_BOB,
    USER_CAROL,
)
from ppap_core.identity import PROVIDER_FEISHU, Principal, Resource, identity_key, new_token, token_hash
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
    def __init__(self, settings: Settings, objects=None) -> None:
        self.settings = settings
        self.objects = objects or S3Objects(settings)

    def _conn(self) -> psycopg.Connection:
        return connect(self.settings, "api")

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

    def create_session(self, principal: Principal, ttl_seconds: int = 43200) -> str:
        raw = new_token()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO product.sessions (token_hash, user_id, tenant_id, expires_at)
                VALUES (%s, %s, %s, %s)
                """,
                (token_hash(raw), principal.user_id, principal.tenant_id, _now() + timedelta(seconds=ttl_seconds)),
            )
        return raw

    def principal_from_token(self, raw: str) -> Principal | None:
        with self._conn() as conn:
            conn.row_factory = dict_row
            row = conn.execute(
                """
                SELECT s.user_id, s.tenant_id, i.provider, i.app_id, i.tenant_key, i.subject, m.role, i.display_name
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
        return Principal(
            str(row["user_id"]),
            str(row["tenant_id"]),
            row["provider"],
            row["app_id"],
            row["tenant_key"],
            row["subject"],
            row["role"],
            row["display_name"],
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
        ctype = (content_type or "application/octet-stream").split(";")[0].strip() or "application/octet-stream"
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
        self.objects.put_exclusive(version.object_key, body, version.content_type)
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE product.file_versions
                SET byte_size = %s
                WHERE id = %s AND file_id = %s AND status = %s
                """,
                (len(body), version.id, version.file_id, STAGING),
            )
        return FileVersion(
            version.id,
            version.file_id,
            version.tenant_id,
            version.original_filename,
            version.content_type,
            len(body),
            version.object_key,
            STAGING,
            version.sha256,
        )

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
