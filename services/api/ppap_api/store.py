from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ppap_core.db import connect
from ppap_core.display import round_display
from ppap_core.drafts import DRAFT_OPEN, IDENT_PENDING, ROLES
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
    CATALOG_PROJECT,
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
from ppap_core.rounds import ROUND_ACCEPTED, ROUND_FAILED, ROUND_RUNNING
from ppap_core.rules import catalog_snapshots, snapshot_hash
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
        self.seed_rule_catalog(TENANT_A, CATALOG_PROJECT)
        self.seed_rule_catalog(TENANT_B, CATALOG_PROJECT)

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

    def enqueue_audit(
        self,
        tenant_id: str,
        version: FileVersion,
        *,
        document_type: str,
        rule_profile: str,
        product_rule_version_id: str,
        engine_rule_id: str,
        rules: list | None = None,
        audit_round_id: str | None = None,
        conn=None,
    ) -> dict:
        if version.status != READY or not version.sha256:
            raise ValueError("not_ready")
        if version.tenant_id != tenant_id:
            raise ValueError("access_denied")
        job_id = str(uuid.uuid4())
        payload = {
            "file_id": version.file_id,
            "file_version_id": version.id,
            "sha256": version.sha256,
            "original_filename": version.original_filename,
            "document_type": document_type,
            "rule_profile": rule_profile,
            "product_rule_version_id": product_rule_version_id,
            "engine_rule_id": engine_rule_id,
            "rules": rules or [],
            "audit_round_id": audit_round_id,
        }
        owns = conn is None
        ctx = self._conn() if owns else None
        try:
            target = ctx if owns else conn
            target.execute(
                """
                INSERT INTO engine.jobs (id, tenant_id, audit_round_id, kind, payload, status)
                VALUES (%s, %s, %s, 'audit.execute', %s, 'queued')
                """,
                (job_id, tenant_id, audit_round_id, Jsonb(payload)),
            )
        finally:
            if ctx is not None:
                ctx.close()
        return {"id": job_id, "status": "queued", "payload": payload}

    def get_job(self, tenant_id: str, job_id: str) -> dict | None:
        jid = _as_uuid(job_id)
        if not jid:
            return None
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id, tenant_id, kind, payload, status, attempt, result, lease_owner
                FROM engine.jobs
                WHERE id = %s
                """,
                (jid,),
            ).fetchone()
        if row is None:
            return None
        if str(row[1]) != tenant_id:
            raise ValueError("access_denied")
        return {
            "id": str(row[0]),
            "kind": row[2],
            "payload": row[3],
            "status": row[4],
            "attempt": row[5],
            "result": row[6],
            "lease_owner": row[7],
        }

    def seed_rule_catalog(self, tenant_id: str, project_id: str) -> list[dict]:
        published = []
        for snapshot in catalog_snapshots(project_id):
            published.append(self.publish_rule_version(tenant_id, snapshot))
        return published

    def publish_rule_version(self, tenant_id: str, snapshot: dict) -> dict:
        content = snapshot["content"]
        digest = snapshot.get("content_sha256") or snapshot_hash(content)
        engine_rule_id = str(snapshot["engine_rule_id"])
        project_id = str(snapshot["project_id"])
        with self._conn() as conn:
            row = conn.execute(
                """
                INSERT INTO product.rule_versions
                    (id, tenant_id, project_id, engine_rule_id, document_type, label, content, content_sha256)
                VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, project_id, engine_rule_id, content_sha256)
                DO UPDATE SET label = EXCLUDED.label
                RETURNING id, tenant_id, project_id, engine_rule_id, document_type, label, content, content_sha256, created_at
                """,
                (
                    tenant_id,
                    project_id,
                    engine_rule_id,
                    str(snapshot["document_type"]),
                    str(snapshot.get("label") or engine_rule_id),
                    Jsonb(content),
                    digest,
                ),
            ).fetchone()
        return self._rule_public(row)

    def _rule_public(self, row) -> dict:
        content = row[6] if isinstance(row[6], dict) else {}
        checks = content.get("checks") if isinstance(content.get("checks"), list) else []
        return {
            "id": str(row[0]),
            "tenant_id": str(row[1]),
            "project_id": row[2],
            "engine_rule_id": row[3],
            "document_type": row[4],
            "label": row[5],
            "content": content,
            "content_sha256": row[7],
            "check_count": len(checks),
            "created_at": row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8]),
        }

    def list_rule_versions(self, tenant_id: str, project_id: str | None = None) -> list[dict]:
        with self._conn() as conn:
            if project_id:
                rows = conn.execute(
                    """
                    SELECT id, tenant_id, project_id, engine_rule_id, document_type, label, content, content_sha256, created_at
                    FROM product.rule_versions
                    WHERE tenant_id = %s AND project_id = %s
                    ORDER BY document_type, created_at
                    """,
                    (tenant_id, project_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, tenant_id, project_id, engine_rule_id, document_type, label, content, content_sha256, created_at
                    FROM product.rule_versions
                    WHERE tenant_id = %s
                    ORDER BY project_id, document_type, created_at
                    """,
                    (tenant_id,),
                ).fetchall()
        return [self._rule_public(row) for row in rows]

    def get_rule_version(self, tenant_id: str, version_id: str) -> dict:
        vid = _as_uuid(version_id)
        if not vid:
            raise KeyError("invalid_rule_version")
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id, tenant_id, project_id, engine_rule_id, document_type, label, content, content_sha256, created_at
                FROM product.rule_versions
                WHERE id = %s
                """,
                (vid,),
            ).fetchone()
        if row is None:
            raise KeyError("invalid_rule_version")
        if str(row[1]) != tenant_id:
            raise ValueError("access_denied")
        return self._rule_public(row)

    def _project_exists(self, tenant_id: str, project_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM product.rule_versions WHERE tenant_id = %s AND project_id = %s LIMIT 1",
                (tenant_id, project_id),
            ).fetchone()
        return bool(row)

    def _require_known_project(self, tenant_id: str, project_id: str | None) -> str | None:
        value = (project_id or "").strip() or None
        if value and not self._project_exists(tenant_id, value):
            raise ValueError("unknown_project")
        return value

    def create_draft(self, principal: Principal, *, label: str = "", project_id: str | None = None) -> dict:
        known_project = self._require_known_project(principal.tenant_id, project_id)
        draft_id = str(uuid.uuid4())
        with self._conn() as conn:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO product.resources (id, tenant_id, kind) VALUES (%s, %s, 'draft')",
                    (draft_id, principal.tenant_id),
                )
                conn.execute(
                    """
                    INSERT INTO product.audit_drafts (id, tenant_id, created_by, status, project_id, label)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (draft_id, principal.tenant_id, principal.user_id, DRAFT_OPEN, known_project, (label or "")[:255]),
                )
                conn.execute(
                    """
                    INSERT INTO product.resource_grants (resource_id, user_id, capability)
                    VALUES (%s, %s, 'read')
                    ON CONFLICT DO NOTHING
                    """,
                    (draft_id, principal.user_id),
                )
        return self.get_draft(principal, draft_id)

    def get_draft(self, principal: Principal, draft_id: str) -> dict:
        parsed = _as_uuid(draft_id)
        if not parsed:
            raise KeyError("missing")
        resource = self.resource(parsed)
        grants = self.grants(parsed, principal.user_id) if resource else set()
        if resource is None or resource.kind != "draft":
            raise KeyError("missing")
        if resource.tenant_id != principal.tenant_id or not (grants & {"read", "review"}):
            raise ValueError("access_denied")
        with self._conn() as conn:
            conn.row_factory = dict_row
            draft = conn.execute(
                """
                SELECT id, tenant_id, status, project_id, label, created_at, updated_at
                FROM product.audit_drafts
                WHERE id = %s
                """,
                (parsed,),
            ).fetchone()
            items = conn.execute(
                """
                SELECT i.id, i.file_id, i.file_version_id, v.sha256, v.original_filename, i.role, i.position,
                       i.identification_status, i.identification_job_id, i.matched_document_type,
                       i.product_rule_version_id, i.engine_rule_id, i.bound_content_sha256, i.bound_check_count,
                       i.match_evidence
                FROM product.draft_items i
                JOIN product.file_versions v ON v.id = i.file_version_id
                WHERE i.draft_id = %s
                ORDER BY i.position, i.created_at
                """,
                (parsed,),
            ).fetchall()
        if draft is None:
            raise KeyError("missing")
        return {
            "id": str(draft["id"]),
            "tenant_id": str(draft["tenant_id"]),
            "status": draft["status"],
            "project_id": draft["project_id"],
            "label": draft["label"],
            "accepted": False,
            "created_at": draft["created_at"].isoformat(),
            "updated_at": draft["updated_at"].isoformat(),
            "items": [
                {
                    "id": str(item["id"]),
                    "file_id": str(item["file_id"]),
                    "file_version_id": str(item["file_version_id"]),
                    "sha256": item["sha256"],
                    "original_filename": item["original_filename"],
                    "role": item["role"],
                    "identification_status": item["identification_status"],
                    "identification_job_id": str(item["identification_job_id"]) if item["identification_job_id"] else None,
                    "matched_document_type": item["matched_document_type"],
                    "product_rule_version_id": str(item["product_rule_version_id"]) if item["product_rule_version_id"] else None,
                    "engine_rule_id": item["engine_rule_id"],
                    "bound_content_sha256": item["bound_content_sha256"],
                    "bound_check_count": item["bound_check_count"],
                    "match_evidence": item["match_evidence"] or {},
                }
                for item in items
            ],
        }

    def replace_draft(self, principal: Principal, draft_id: str, patch: dict) -> dict:
        current = self.get_draft(principal, draft_id)
        label = current["label"] if "label" not in patch else str(patch.get("label") or "")[:255]
        if "project_id" in patch:
            project_id = self._require_known_project(principal.tenant_id, patch.get("project_id"))
        else:
            project_id = current["project_id"]
        incoming = patch["items"] if "items" in patch else None
        if incoming is not None:
            if not isinstance(incoming, list):
                raise ValueError("invalid_items")
            prepared = self._prepare_items(principal, current, project_id, incoming)
        else:
            prepared = None
            if project_id != current["project_id"]:
                for item in current["items"]:
                    if item["product_rule_version_id"]:
                        bound = self.get_rule_version(principal.tenant_id, item["product_rule_version_id"])
                        if bound["project_id"] != project_id:
                            raise ValueError("project_conflict")
        with self._conn() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE product.audit_drafts
                    SET label = %s, project_id = %s, updated_at = now()
                    WHERE id = %s AND tenant_id = %s AND status = %s
                    """,
                    (label, project_id, current["id"], principal.tenant_id, DRAFT_OPEN),
                )
                if prepared is not None:
                    conn.execute("DELETE FROM product.draft_items WHERE draft_id = %s", (current["id"],))
                    for position, item in enumerate(prepared):
                        conn.execute(
                            """
                            INSERT INTO product.draft_items (
                                id, draft_id, tenant_id, file_id, file_version_id, role, position,
                                identification_status, identification_job_id, matched_document_type,
                                product_rule_version_id, engine_rule_id, bound_content_sha256, bound_check_count,
                                match_evidence
                            ) VALUES (
                                %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s,
                                %s, %s, %s, %s,
                                %s
                            )
                            """,
                            (
                                item["id"], current["id"], principal.tenant_id, item["file_id"], item["file_version_id"],
                                item["role"], position,
                                item["identification_status"], item["identification_job_id"], item["matched_document_type"],
                                item["product_rule_version_id"], item["engine_rule_id"], item["bound_content_sha256"],
                                item["bound_check_count"], Jsonb(item["match_evidence"]),
                            ),
                        )
        return self.get_draft(principal, draft_id)

    def _prepare_items(self, principal: Principal, current: dict, project_id: str | None, incoming: list) -> list[dict]:
        previous = {(item["file_version_id"], item["role"]): item for item in current["items"]}
        seen: set[str] = set()
        prepared: list[dict] = []
        for raw in incoming:
            if not isinstance(raw, dict):
                raise ValueError("invalid_items")
            role = str(raw.get("role") or "")
            if role not in ROLES:
                raise ValueError("invalid_role")
            version = self.get_version(str(raw.get("file_id") or ""), str(raw.get("file_version_id") or ""))
            if version is None:
                raise KeyError("missing_file")
            if version.tenant_id != principal.tenant_id:
                raise ValueError("access_denied")
            resource = self.resource(version.file_id)
            grants = self.grants(version.file_id, principal.user_id) if resource else set()
            if resource is None or resource.kind != "file" or not (grants & {"read", "review"}):
                raise ValueError("access_denied")
            if version.status != READY or not version.sha256:
                raise ValueError("not_ready")
            if version.id in seen:
                raise ValueError("duplicate_version")
            seen.add(version.id)
            kept = previous.get((version.id, role))
            if "product_rule_version_id" in raw:
                bound_id = raw.get("product_rule_version_id") or None
            else:
                bound_id = kept["product_rule_version_id"] if kept else None
            engine_rule_id = None
            bound_sha = None
            bound_count = None
            if bound_id:
                rule = self.get_rule_version(principal.tenant_id, str(bound_id))
                if project_id and rule["project_id"] != project_id:
                    raise ValueError("project_conflict")
                engine_rule_id = rule["engine_rule_id"]
                bound_sha = rule["content_sha256"]
                bound_count = rule["check_count"]
                bound_id = rule["id"]
            if kept:
                ident_status = kept["identification_status"]
                ident_job = kept["identification_job_id"]
                matched = kept["matched_document_type"]
                evidence = kept["match_evidence"]
                item_id = kept["id"]
            else:
                ident_status = IDENT_PENDING
                ident_job = None
                matched = None
                evidence = {}
                item_id = str(uuid.uuid4())
            prepared.append(
                {
                    "id": item_id,
                    "file_id": version.file_id,
                    "file_version_id": version.id,
                    "role": role,
                    "identification_status": ident_status,
                    "identification_job_id": ident_job,
                    "matched_document_type": matched,
                    "product_rule_version_id": bound_id,
                    "engine_rule_id": engine_rule_id,
                    "bound_content_sha256": bound_sha,
                    "bound_check_count": bound_count,
                    "match_evidence": evidence or {},
                }
            )
        return prepared

    def enqueue_identify(self, principal: Principal, draft_id: str) -> dict:
        draft = self.get_draft(principal, draft_id)
        if draft["project_id"] and not self._project_exists(principal.tenant_id, draft["project_id"]):
            raise ValueError("unknown_project")
        jobs = []
        with self._conn() as conn:
            with conn.transaction():
                for item in draft["items"]:
                    job_id = str(uuid.uuid4())
                    payload = {
                        "draft_id": draft["id"],
                        "item_id": item["id"],
                        "file_id": item["file_id"],
                        "file_version_id": item["file_version_id"],
                        "sha256": item["sha256"],
                        "original_filename": item["original_filename"],
                        "role": item["role"],
                        "project_id": draft["project_id"],
                        "preserve_product_rule_version_id": item["product_rule_version_id"],
                    }
                    conn.execute(
                        """
                        INSERT INTO engine.jobs (id, tenant_id, kind, payload, status)
                        VALUES (%s, %s, 'draft.identify', %s, 'queued')
                        """,
                        (job_id, principal.tenant_id, Jsonb(payload)),
                    )
                    conn.execute(
                        """
                        UPDATE product.draft_items
                        SET identification_job_id = %s, identification_status = %s, updated_at = now()
                        WHERE id = %s AND tenant_id = %s
                        """,
                        (job_id, IDENT_PENDING, item["id"], principal.tenant_id),
                    )
                    jobs.append({"item_id": item["id"], "id": job_id, "status": "queued", "kind": "draft.identify"})
        return {"draft_id": draft["id"], "jobs": jobs, "accepted": False}

    def apply_identification(
        self,
        tenant_id: str,
        item_id: str,
        job_id: str,
        *,
        status: str,
        matched_document_type: str | None,
        evidence: dict,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE product.draft_items
                SET identification_status = %s,
                    matched_document_type = %s,
                    match_evidence = %s,
                    identification_job_id = %s,
                    updated_at = now()
                WHERE id = %s AND tenant_id = %s
                """,
                (status, matched_document_type, Jsonb(evidence), job_id, item_id, tenant_id),
            )

    def _round_request_hash(self, draft: dict) -> str:
        return snapshot_hash(
            {
                "draft_id": draft["id"],
                "items": [
                    {
                        "file_version_id": item["file_version_id"],
                        "role": item["role"],
                        "sha256": item["sha256"],
                        "product_rule_version_id": item["product_rule_version_id"],
                        "engine_rule_id": item["engine_rule_id"],
                        "bound_content_sha256": item["bound_content_sha256"],
                    }
                    for item in draft["items"]
                ],
            }
        )

    def _existing_round_by_key(self, tenant_id: str, idempotency_key: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM product.audit_rounds WHERE tenant_id = %s AND idempotency_key = %s",
                (tenant_id, idempotency_key),
            ).fetchone()
        if not row:
            return None
        return {"id": str(row[0])}

    def accept_draft(self, principal: Principal, draft_id: str, idempotency_key: str) -> tuple[dict, bool]:
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 128:
            raise ValueError("invalid_idempotency_key")
        draft = self.get_draft(principal, draft_id)
        targets = [item for item in draft["items"] if item["role"] == "target"]
        if not targets:
            raise ValueError("target_required")
        for item in targets:
            if not item.get("product_rule_version_id") or not item.get("engine_rule_id"):
                raise ValueError("rule_not_bound")
            if not item.get("sha256"):
                raise ValueError("not_ready")
        for item in draft["items"]:
            if not item.get("sha256"):
                raise ValueError("not_ready")
        request_hash = self._round_request_hash(draft)
        existing = self._existing_round_by_key(principal.tenant_id, key)
        if existing:
            found = self.get_round(principal, existing["id"])
            if found["request_hash"] != request_hash:
                raise ValueError("idempotency_conflict")
            return found, False
        round_id = str(uuid.uuid4())
        try:
            with self._conn() as conn:
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO product.resources (id, tenant_id, kind) VALUES (%s, %s, 'round')",
                        (round_id, principal.tenant_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO product.audit_rounds
                            (id, tenant_id, created_by, draft_id, idempotency_key, request_hash, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (round_id, principal.tenant_id, principal.user_id, draft["id"], key, request_hash, ROUND_ACCEPTED),
                    )
                    conn.execute(
                        """
                        INSERT INTO product.resource_grants (resource_id, user_id, capability)
                        VALUES (%s, %s, 'read')
                        ON CONFLICT DO NOTHING
                        """,
                        (round_id, principal.user_id),
                    )
                    for position, item in enumerate(draft["items"]):
                        conn.execute(
                            """
                            INSERT INTO product.round_inputs (
                                id, round_id, tenant_id, file_id, file_version_id, role, sha256, original_filename,
                                product_rule_version_id, engine_rule_id, bound_content_sha256, bound_check_count, position
                            ) VALUES (
                                gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s
                            )
                            """,
                            (
                                round_id, principal.tenant_id, item["file_id"], item["file_version_id"], item["role"],
                                item["sha256"], item["original_filename"], item["product_rule_version_id"],
                                item["engine_rule_id"], item["bound_content_sha256"], item["bound_check_count"], position,
                            ),
                        )
                    for item in targets:
                        version = self.get_version(item["file_id"], item["file_version_id"])
                        if version is None:
                            raise ValueError("not_ready")
                        self.enqueue_audit(
                            principal.tenant_id,
                            version,
                            document_type=item.get("matched_document_type") or item["engine_rule_id"],
                            rule_profile="v2",
                            product_rule_version_id=item["product_rule_version_id"],
                            engine_rule_id=item["engine_rule_id"],
                            audit_round_id=round_id,
                            conn=conn,
                        )
        except UniqueViolation:
            found = self._existing_round_by_key(principal.tenant_id, key)
            if not found:
                raise
            body = self.get_round(principal, found["id"])
            if body["request_hash"] != request_hash:
                raise ValueError("idempotency_conflict")
            return body, False
        return self.get_round(principal, round_id), True

    def get_round(self, principal: Principal, round_id: str) -> dict:
        parsed = _as_uuid(round_id)
        if not parsed:
            raise KeyError("missing")
        resource = self.resource(parsed)
        grants = self.grants(parsed, principal.user_id) if resource else set()
        if resource is None or resource.kind != "round":
            raise KeyError("missing")
        if resource.tenant_id != principal.tenant_id or not (grants & {"read", "review"}):
            raise ValueError("access_denied")
        with self._conn() as conn:
            conn.row_factory = dict_row
            round_row = conn.execute(
                """
                SELECT id, tenant_id, draft_id, idempotency_key, request_hash, status, current_result_id, created_at, updated_at
                FROM product.audit_rounds
                WHERE id = %s
                """,
                (parsed,),
            ).fetchone()
            inputs = conn.execute(
                """
                SELECT file_id, file_version_id, role, sha256, original_filename,
                       product_rule_version_id, engine_rule_id, bound_content_sha256, bound_check_count
                FROM product.round_inputs
                WHERE round_id = %s
                ORDER BY position
                """,
                (parsed,),
            ).fetchall()
            jobs = conn.execute(
                """
                SELECT id, kind, status, attempt, result
                FROM engine.jobs
                WHERE audit_round_id = %s
                ORDER BY created_at
                """,
                (parsed,),
            ).fetchall()
            results = conn.execute(
                """
                SELECT id, job_id, attempt, file_version_id, sha256, product_rule_version_id, engine_rule_id, findings, created_at
                FROM product.round_results
                WHERE round_id = %s
                ORDER BY created_at
                """,
                (parsed,),
            ).fetchall()
        if round_row is None:
            raise KeyError("missing")
        inputs = [
            {
                "file_id": str(item["file_id"]),
                "file_version_id": str(item["file_version_id"]),
                "role": item["role"],
                "sha256": item["sha256"],
                "original_filename": item["original_filename"],
                "product_rule_version_id": str(item["product_rule_version_id"]) if item["product_rule_version_id"] else None,
                "engine_rule_id": item["engine_rule_id"],
                "bound_content_sha256": item["bound_content_sha256"],
                "bound_check_count": item["bound_check_count"],
            }
            for item in inputs
        ]
        jobs = [
            {
                "id": str(job["id"]),
                "kind": job["kind"],
                "status": job["status"],
                "attempt": job["attempt"],
                "result": job["result"] or {},
            }
            for job in jobs
        ]
        results = [
            {
                "id": str(row["id"]),
                "job_id": str(row["job_id"]),
                "attempt": row["attempt"],
                "file_version_id": str(row["file_version_id"]),
                "sha256": row["sha256"],
                "product_rule_version_id": row["product_rule_version_id"],
                "engine_rule_id": row["engine_rule_id"],
                "findings": row["findings"] or [],
                "created_at": row["created_at"].isoformat(),
            }
            for row in results
        ]
        display = round_display(round_row["status"], inputs, results)
        return {
            "id": str(round_row["id"]),
            "tenant_id": str(round_row["tenant_id"]),
            "draft_id": str(round_row["draft_id"]),
            "idempotency_key": round_row["idempotency_key"],
            "request_hash": round_row["request_hash"],
            "status": round_row["status"],
            "execution_state": display["execution_state"],
            "current_result_id": str(round_row["current_result_id"]) if round_row["current_result_id"] else None,
            "created_at": round_row["created_at"].isoformat(),
            "updated_at": round_row["updated_at"].isoformat(),
            "inputs": inputs,
            "jobs": jobs,
            "results": results,
            "visible_findings": display["visible_findings"],
            "coverage": display["coverage"],
        }

    def retry_round(self, principal: Principal, round_id: str) -> dict:
        current = self.get_round(principal, round_id)
        with self._conn() as conn:
            with conn.transaction():
                updated = conn.execute(
                    """
                    UPDATE engine.jobs
                    SET status = 'queued', lease_owner = NULL, lease_until = NULL, updated_at = now()
                    WHERE audit_round_id = %s AND tenant_id = %s AND status = 'failed'
                    RETURNING id
                    """,
                    (current["id"], principal.tenant_id),
                ).fetchall()
                if updated:
                    conn.execute(
                        """
                        UPDATE product.audit_rounds
                        SET status = %s, updated_at = now()
                        WHERE id = %s AND tenant_id = %s
                        """,
                        (ROUND_ACCEPTED, current["id"], principal.tenant_id),
                    )
        return self.get_round(principal, round_id)

    def expire_job_lease(self, tenant_id: str, job_id: str) -> bool:
        jid = _as_uuid(job_id)
        if not jid:
            raise ValueError("invalid_job")
        with self._conn() as conn:
            row = conn.execute(
                """
                UPDATE engine.jobs
                SET lease_until = now() - interval '1 second', updated_at = now()
                WHERE id = %s AND tenant_id = %s AND status = 'leased'
                RETURNING id
                """,
                (jid, tenant_id),
            ).fetchone()
        return bool(row)

    def forge_job_result(self, tenant_id: str, job_id: str, worker: str, attempt: int, result: dict) -> bool:
        jid = _as_uuid(job_id)
        if not jid:
            raise ValueError("invalid_job")
        with self._conn() as conn:
            updated = conn.execute(
                """
                UPDATE engine.jobs
                SET status = 'succeeded', result = %s, lease_until = NULL, updated_at = now()
                WHERE id = %s AND tenant_id = %s AND lease_owner = %s AND attempt = %s AND status = 'leased'
                """,
                (Jsonb(result), jid, tenant_id, worker, attempt),
            )
        return updated.rowcount == 1

    def claim_job(self, tenant_id: str, job_id: str, worker: str) -> dict | None:
        jid = _as_uuid(job_id)
        owner = str(worker or "").strip()
        if not jid or not owner or len(owner) > 128:
            raise ValueError("invalid_job")
        with self._conn() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE engine.jobs
                    SET status = 'leased',
                        attempt = attempt + 1,
                        lease_owner = %s,
                        lease_until = now() + interval '15 minutes',
                        updated_at = now()
                    WHERE id = %s AND tenant_id = %s
                      AND (
                        status = 'queued'
                        OR (status = 'leased' AND lease_until IS NOT NULL AND lease_until < now())
                      )
                    RETURNING id, attempt, lease_owner, status, audit_round_id, kind
                    """,
                    (owner, jid, tenant_id),
                ).fetchone()
                if not row:
                    return None
                if row[5] == "audit.execute" and row[4] is not None:
                    conn.execute(
                        """
                        UPDATE product.audit_rounds
                        SET status = %s, updated_at = now()
                        WHERE id = %s AND tenant_id = %s AND status IN (%s, %s, %s)
                        """,
                        (ROUND_RUNNING, row[4], tenant_id, ROUND_ACCEPTED, ROUND_FAILED, ROUND_RUNNING),
                    )
        return {
            "id": str(row[0]),
            "attempt": row[1],
            "lease_owner": row[2],
            "status": row[3],
        }
