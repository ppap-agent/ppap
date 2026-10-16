from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import psycopg
from ppap_core.audits import (
    ANALYSIS_DEEP,
    ANALYSIS_STANDARD,
    DRAFT_ACCEPTED,
    DRAFT_OPEN,
    EXEC_COMPLETED,
    EXEC_FAILED,
    EXEC_QUEUED,
    EXEC_RUNNING,
    ClaimedJob,
    RoundInput,
    RoundWork,
    job_payload,
    normalize_analysis_mode,
    normalize_idempotency_key,
    normalize_review,
    normalize_role,
    request_fingerprint,
    require_targets,
)
from ppap_core.db import connect
from ppap_core.feishu import UserTokens
from ppap_core.feishu_usage import audit_usage_points, remaining_trial_points, split_trial_and_partner
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
from ppap_core.rules import FILE_PREPARATION_KINDS, latest_rule_versions, normalize_rule_body, rule_parse_request, rule_parse_view, rule_title
from ppap_core.settings import Settings
from ppap_core.storage import S3Objects
from ppap_core.writeback_targets import normalize_external_ref, public_target
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _executor_public(payload) -> dict:
    if not isinstance(payload, dict):
        return {}
    extra = {}
    if payload.get("executor"):
        extra["executor"] = payload["executor"]
    if payload.get("executor_revision"):
        extra["executor_revision"] = payload["executor_revision"]
    return extra


def _attempt_diagnostic(claimed: ClaimedJob, result: dict, findings: list) -> dict:
    diagnostic = {
        "note": result.get("note") or "round_audit",
        "finding_count": len(findings),
    }
    payload = claimed.payload if isinstance(claimed.payload, dict) else {}
    if payload.get("executor"):
        diagnostic["executor"] = payload["executor"]
        diagnostic["executor_revision"] = payload.get("executor_revision")
    for key in ("failure", "usage"):
        if key in result:
            diagnostic[key] = result[key]
    return diagnostic


class AuthStore:
    def __init__(
        self,
        settings: Settings,
        objects=None,
        role: str = "api",
        rule_execution_factory=None,
    ) -> None:
        self.settings = settings
        self.objects = objects or S3Objects(settings)
        self.role = role
        self.rule_execution_factory = rule_execution_factory

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
        provider, app_id, tenant_key, subject = identity_key(PROVIDER_FEISHU, self.settings.feishu_app_id, tenant_key, subject)
        for _ in range(3):
            try:
                return self._upsert_feishu_identity_once(provider, app_id, tenant_key, subject, display_name)
            except UniqueViolation:
                continue
        raise RuntimeError("identity_upsert_conflict")

    def _upsert_feishu_identity_once(self, provider: str, app_id: str, tenant_key: str, subject: str, display_name: str) -> Principal:
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
                tenant_id = cast(
                    tuple[Any, ...],
                    conn.execute(
                        """
                    INSERT INTO product.tenants (id, provider, external_key, display_name)
                    VALUES (gen_random_uuid(), %s, %s, %s)
                    ON CONFLICT (provider, external_key) DO UPDATE SET display_name = EXCLUDED.display_name
                    RETURNING id
                    """,
                        (provider, tenant_key, "feishu:" + tenant_key),
                    ).fetchone(),
                )[0]
                user_id = cast(tuple[Any, ...], conn.execute("INSERT INTO product.users (id) VALUES (gen_random_uuid()) RETURNING id").fetchone())[0]
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
                return Principal(str(user_id), str(tenant_id), provider, app_id, tenant_key, subject, "member", display_name)

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
                    token_hash(raw),
                    principal.user_id,
                    principal.tenant_id,
                    expires_at,
                    access_token,
                    refresh_token,
                    access_expires_at,
                    refresh_expires_at,
                ),
            )
        return raw

    def load_session(self, raw: str) -> Session | None:
        with self._conn() as conn:
            conn.row_factory = cast(Any, dict_row)
            session_row = cast(
                dict[str, Any] | None,
                conn.execute(
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
                ).fetchone(),
            )
        if not session_row:
            return None
        principal = Principal(
            str(session_row["user_id"]),
            str(session_row["tenant_id"]),
            session_row["provider"],
            session_row["app_id"],
            session_row["tenant_key"],
            session_row["subject"],
            session_row["role"],
            session_row["display_name"],
        )
        return Session(
            principal,
            session_row["expires_at"],
            session_row["access_token"] or "",
            session_row["refresh_token"] or "",
            session_row["access_expires_at"],
            session_row["refresh_expires_at"],
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
                    (TENANT_A, PROVIDER_FEISHU, "isolated-dev-a", "isolated-dev-a", TENANT_B, PROVIDER_FEISHU, "isolated-dev-b", "isolated-dev-b"),
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
                        RESOURCE_A,
                        USER_ALICE,
                        RESOURCE_A,
                        USER_ALICE,
                        RESOURCE_A,
                        USER_BOB,
                        RESOURCE_B,
                        USER_CAROL,
                        RESOURCE_B,
                        USER_CAROL,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO product.rule_versions (id, tenant_id, title, body)
                    VALUES (%s, %s, %s, %s), (%s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        RULE_A,
                        TENANT_A,
                        "资料就绪",
                        Jsonb(PRESENCE_RULE_BODY),
                        RULE_B,
                        TENANT_B,
                        "资料就绪",
                        Jsonb(PRESENCE_RULE_BODY),
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

    def accept_rule_parse(self, principal, request_id, source_file_id, source_file_version_id, document_type="") -> dict:
        return self._accept_file_job(principal, request_id, source_file_id, source_file_version_id, "rule_parse", document_type)

    def accept_file_analysis(self, principal, request_id, source_file_id, source_file_version_id) -> dict:
        return self._accept_file_job(principal, request_id, source_file_id, source_file_version_id, "file_analysis")

    def accept_file_validation(self, principal, request_id, source_file_id, source_file_version_id) -> dict:
        return self._accept_file_job(principal, request_id, source_file_id, source_file_version_id, "file_validation")

    def _accept_file_job(self, principal, request_id, source_file_id, source_file_version_id, kind, document_type="") -> dict:
        job_id, payload = rule_parse_request(request_id, source_file_id, source_file_version_id, document_type)
        with self._conn() as conn:
            with conn.transaction():
                prepared = self._prepared_items(conn, principal, [{"file_version_id": payload["source_file_version_id"], "role": "reference"}])
                if prepared[0]["file_id"] != payload["source_file_id"]:
                    raise KeyError("missing")
                conn.execute(
                    """INSERT INTO engine.jobs (id, tenant_id, kind, payload, status)
                    VALUES (%s, %s, %s, %s, 'queued') ON CONFLICT (id) DO NOTHING""",
                    (job_id, principal.tenant_id, kind, Jsonb(payload)),
                )
                row = cast(
                    tuple[Any, ...],
                    conn.execute(
                        "SELECT tenant_id, kind, payload, status, result, lease_until < now() FROM engine.jobs WHERE id = %s",
                        (job_id,),
                    ).fetchone(),
                )
                if str(row[0]) != principal.tenant_id or row[1] != kind:
                    raise KeyError("missing")
                if row[2] != payload:
                    raise ValueError("idempotency_conflict" if kind == "file_validation" else "rule_parse_conflict")
                return rule_parse_view(job_id, row[2], row[3], row[4], bool(row[5]))

    def get_rule_parse(self, principal, job_id: str) -> dict:
        return self._get_file_job(principal, job_id, "rule_parse")

    def get_file_analysis(self, principal, job_id: str) -> dict:
        return self._get_file_job(principal, job_id, "file_analysis")

    def get_file_validation(self, principal, job_id: str) -> dict:
        return self._get_file_job(principal, job_id, "file_validation")

    def latest_file_analysis(self, principal, file_id: str, version_id: str) -> dict | None:
        return self._latest_file_job(principal, file_id, version_id, "file_analysis")

    def latest_file_validation(self, principal, file_id: str, version_id: str) -> dict | None:
        return self._latest_file_job(principal, file_id, version_id, "file_validation")

    def _latest_file_job(self, principal, file_id: str, version_id: str, kind: str) -> dict | None:
        with self._conn() as conn:
            prepared = self._prepared_items(conn, principal, [{"file_version_id": version_id, "role": "reference"}])
            if prepared[0]["file_id"] != file_id:
                raise KeyError("missing")
            # Task recognition has fixed references; this legacy read is standalone.
            row = conn.execute(
                """SELECT id FROM engine.jobs WHERE tenant_id = %s AND kind = %s
                AND payload->>'source_file_version_id' = %s
                AND NOT (payload ? 'task_id')
                ORDER BY created_at DESC, id DESC LIMIT 1""",
                (principal.tenant_id, kind, version_id),
            ).fetchone()
        return self._get_file_job(principal, str(row[0]), kind) if row else None

    def _get_file_job(self, principal, job_id: str, kind: str) -> dict:
        parsed = _as_uuid(job_id)
        if not parsed:
            raise KeyError("missing")
        with self._conn() as conn:
            row = conn.execute(
                """SELECT payload, status, result, lease_until < now() FROM engine.jobs
                WHERE id = %s AND tenant_id = %s AND kind = %s""",
                (parsed, principal.tenant_id, kind),
            ).fetchone()
            if row is None:
                raise KeyError("missing")
            if row[0].get("task_id"):
                self._recognition_owner(conn, principal, row[0]["task_id"])
            self._prepared_items(conn, principal, [{"file_version_id": row[0]["source_file_version_id"], "role": "reference"}])
            return rule_parse_view(parsed, row[0], row[1], row[2], bool(row[3]))

    def create_audit_task(self, principal: Principal, body: dict) -> dict:
        request_id = _as_uuid(str(body.get("request_id") or ""))
        revising = "task_id" in body or "expected_revision" in body
        task_id = _as_uuid(str(body.get("task_id") or "")) if revising else request_id
        expected = body.get("expected_revision")
        files = body.get("files")
        customer = body.get("customer_preference")
        if not request_id or not task_id or not isinstance(files, list) or not files or (customer is not None and (not isinstance(customer, str) or not customer.strip() or len(customer) > 500)):
            raise ValueError("invalid_task_request")
        if revising and (type(expected) is not int or expected < 0):
            raise ValueError("invalid_task_revision")
        mode = self._offered_analysis_mode(body.get("analysis_mode"))
        normalized: list[dict[str, Any]] = []
        for raw in files:
            if not isinstance(raw, dict):
                raise ValueError("invalid_task_request")
            vid, validation_id = _as_uuid(str(raw.get("file_version_id") or "")), _as_uuid(str(raw.get("validation_id") or ""))
            selection, reason = raw.get("selection"), raw.get("removal_reason")
            if not vid or not validation_id or not isinstance(selection, str) or selection not in {"selected", "unselected", "removed"}:
                raise ValueError("invalid_task_request")
            if selection == "removed":
                if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
                    raise ValueError("invalid_removal_reason")
                reason = reason.strip()
            elif reason is not None:
                raise ValueError("invalid_removal_reason")
            normalized.append({"file_version_id": vid, "validation_id": validation_id, "selection": selection, "removal_reason": reason})
        if not any(row["selection"] == "selected" for row in normalized):
            raise ValueError("selected_file_required")
        inputs = {"files": normalized, "analysis_mode": mode, "customer_preference": customer}
        if revising:
            inputs.update(task_id=task_id, expected_revision=expected)
        fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        with self._conn() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("recognition-request:" + request_id,))
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("recognition:" + task_id,))
            if revising:
                draft = self._recognition_owner(conn, principal, task_id, lock=True)
                receipt = conn.execute("SELECT tenant_id, kind, payload FROM engine.jobs WHERE id = %s", (request_id,)).fetchone()
                if receipt:
                    if str(receipt[0]) != principal.tenant_id:
                        raise KeyError("missing")
                    accepted = receipt[2].get("recognition_request") or {}
                    if receipt[1] != "file_analysis" or receipt[2].get("task_id") != task_id or accepted.get("fingerprint") != fingerprint:
                        raise ValueError("idempotency_conflict")
                    return self._recognition_public(conn, principal, task_id)
                if conn.execute("SELECT 1 FROM product.resources WHERE id = %s", (request_id,)).fetchone():
                    raise ValueError("idempotency_conflict")
                if draft["input_revision"] != expected:
                    raise ValueError("task_revision_conflict")
                if draft["status"] != "open" or draft["task_completion_reason"]:
                    raise ValueError("task_input_locked")
                previous = {str(row[0]) for row in conn.execute("SELECT file_version_id FROM product.draft_items WHERE draft_id = %s", (task_id,)).fetchall()}
                if not previous.issubset({row["file_version_id"] for row in normalized}):
                    raise ValueError("task_file_provenance_required")
                revision = expected + 1
                self._recognition_inputs(conn, principal, task_id, normalized, customer, revision, request_id, fingerprint)
                conn.execute(
                    """UPDATE product.audit_drafts SET input_revision = %s, recognition_request_id = %s,
                    analysis_mode = %s, customer_preference = %s, updated_at = now() WHERE id = %s""",
                    (revision, request_id, mode, customer, task_id),
                )
                return self._recognition_public(conn, principal, task_id)
            existing = conn.execute("SELECT task_fingerprint FROM product.audit_drafts WHERE id = %s", (task_id,)).fetchone()
            if existing:
                self._recognition_owner(conn, principal, task_id)
                if existing[0] != fingerprint:
                    raise ValueError("idempotency_conflict")
                return self._recognition_public(conn, principal, task_id)
            if conn.execute("SELECT 1 FROM product.resources WHERE id = %s", (task_id,)).fetchone():
                raise KeyError("missing")
            # Initial creation and rematching share the endpoint's request identity.
            prior_job = conn.execute("SELECT tenant_id FROM engine.jobs WHERE id = %s", (request_id,)).fetchone()
            if prior_job:
                if str(prior_job[0]) != principal.tenant_id:
                    raise KeyError("missing")
                raise ValueError("idempotency_conflict")
            day = cast(tuple, conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone())[0]
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"task-number:{principal.tenant_id}:{day}",))
            sequence = cast(tuple, conn.execute("SELECT COALESCE(MAX(task_sequence), 0) + 1 FROM product.audit_drafts WHERE tenant_id = %s AND task_day = %s", (principal.tenant_id, day)).fetchone())[
                0
            ]
            conn.execute("INSERT INTO product.resources (id, tenant_id, kind) VALUES (%s, %s, 'audit_draft')", (task_id, principal.tenant_id))
            conn.execute(
                """INSERT INTO product.audit_drafts (id, tenant_id, created_by, status, analysis_mode, task_fingerprint, task_day, task_sequence, customer_preference)
                VALUES (%s, %s, %s, 'open', %s, %s, %s, %s, %s)""",
                (task_id, principal.tenant_id, principal.user_id, mode, fingerprint, day, sequence, customer),
            )
            conn.execute("INSERT INTO product.resource_grants (resource_id, user_id, capability) VALUES (%s, %s, 'read'), (%s, %s, 'review')", (task_id, principal.user_id, task_id, principal.user_id))
            self._recognition_inputs(conn, principal, task_id, normalized, customer, 0)
            return self._recognition_public(conn, principal, task_id)

    def _recognition_inputs(self, conn, principal, task_id, files, customer, revision, request_id=None, fingerprint=None) -> None:
        prepared = self._prepared_items(conn, principal, [{"file_version_id": row["file_version_id"], "role": "reference"} for row in files])
        for row, source in zip(files, prepared):
            validation = self.get_file_validation(principal, row["validation_id"])
            if validation["source_file_version_id"] != row["file_version_id"] or validation["source_file_id"] != source["file_id"]:
                raise ValueError("precheck_required")
            if row["selection"] != "removed" and (validation["status"] != "succeeded" or (validation.get("result") or {}).get("state") != "valid"):
                raise ValueError("precheck_required")
        candidates = latest_rule_versions(self.list_rules(principal.tenant_id))
        conn.execute("DELETE FROM product.draft_items WHERE draft_id = %s", (task_id,))
        receipt_id = request_id
        for position, (row, source) in enumerate(zip(files, prepared)):
            analysis_id = (receipt_id or str(uuid.uuid4())) if row["selection"] == "selected" else None
            if analysis_id:
                payload = {
                    "task_id": task_id,
                    "task_revision": revision,
                    "source_file_id": source["file_id"],
                    "source_file_version_id": row["file_version_id"],
                    "document_type": "",
                    "candidate_rules": candidates,
                    "customer_preference": customer,
                    "matching_scope": "customer_document_type",
                }
                if receipt_id:
                    payload["recognition_request"] = {"request_id": receipt_id, "input_revision": revision, "fingerprint": fingerprint}
                    receipt_id = None
                conn.execute("INSERT INTO engine.jobs (id, tenant_id, kind, payload, status) VALUES (%s, %s, 'file_analysis', %s, 'queued')", (analysis_id, principal.tenant_id, Jsonb(payload)))
            conn.execute(
                """INSERT INTO product.draft_items (draft_id, file_version_id, role, selection, removal_reason, validation_job_id, analysis_job_id, position)
                VALUES (%s, %s, 'reference', %s, %s, %s, %s, %s)""",
                (task_id, row["file_version_id"], row["selection"], row["removal_reason"], row["validation_id"], analysis_id, position),
            )

    def _recognition_owner(self, conn, principal: Principal, task_id: str, *, lock: bool = False) -> dict:
        parsed = _as_uuid(task_id)
        if not parsed:
            raise KeyError("missing")
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT d.* FROM product.audit_drafts d JOIN product.resources r ON r.id = d.id
                WHERE d.id = %s AND d.tenant_id = %s AND d.created_by = %s AND d.task_fingerprint IS NOT NULL
                AND r.tenant_id = d.tenant_id AND r.kind = 'audit_draft'
                AND EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id = d.id AND g.user_id = %s AND g.capability IN ('read', 'review'))"""
                + (" FOR UPDATE OF d" if lock else ""),
                (parsed, principal.tenant_id, principal.user_id, principal.user_id),
            )
            draft = cur.fetchone()
        if not draft:
            raise KeyError("missing")
        return draft

    def get_audit_task(self, principal: Principal, task_id: str) -> dict:
        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return self._recognition_public(conn, principal, task_id)

    def recognition_job_current(self, claimed: ClaimedJob) -> bool:
        """Skip obsolete queued work; running calls still cannot change Task refs."""
        with self._conn() as conn:
            return (
                conn.execute(
                    """SELECT 1 FROM product.audit_drafts d JOIN product.draft_items i ON i.draft_id = d.id
                WHERE d.id = %s AND d.tenant_id = %s AND d.status = 'open'
                AND d.task_completion_reason IS NULL AND d.input_revision = %s
                AND i.analysis_job_id = %s AND i.selection = 'selected'""",
                    (claimed.payload["task_id"], claimed.tenant_id, claimed.payload.get("task_revision", 0), claimed.id),
                ).fetchone()
                is not None
            )

    def get_audit_task_recognition(self, principal: Principal, task_id: str, request_id: str) -> dict:
        parsed_task, parsed_request = _as_uuid(task_id), _as_uuid(request_id)
        if not parsed_task or not parsed_request:
            raise ValueError("invalid_task_request")
        task_id, request_id = parsed_task, parsed_request
        read: dict[str, Any] = {"request_id": request_id, "task_id": task_id, "state": "unknown", "input_revision": None, "task": None}
        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            if task_id == request_id and not conn.execute("SELECT 1 FROM product.resources WHERE id = %s", (task_id,)).fetchone():
                return read
            self._recognition_owner(conn, principal, task_id)
            revision = 0 if task_id == request_id else None
            if revision is None:
                job = conn.execute("SELECT payload FROM engine.jobs WHERE id = %s AND tenant_id = %s AND kind = 'file_analysis'", (request_id, principal.tenant_id)).fetchone()
                if job and job[0].get("task_id") == task_id:
                    receipt = job[0].get("recognition_request") or {}
                    if receipt.get("request_id") == request_id:
                        revision = receipt.get("input_revision")
            if revision is not None:
                read.update(state="accepted", input_revision=revision, task=self._recognition_public(conn, principal, task_id))
            return read

    def _recognition_public(self, conn, principal: Principal, task_id: str) -> dict:
        draft = self._recognition_owner(conn, principal, task_id)
        task_id = str(draft["id"])
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT i.*, j.status AS analysis_status, j.result AS analysis_result, j.payload AS analysis_payload,
                j.lease_until < now() AS analysis_expired, j.updated_at AS analysis_updated_at
                FROM product.draft_items i LEFT JOIN engine.jobs j ON j.id = i.analysis_job_id
                WHERE i.draft_id = %s ORDER BY i.position""",
                (task_id,),
            )
            rows = cur.fetchall()
        self._prepared_items(conn, principal, [{"file_version_id": str(row["file_version_id"]), "role": "reference"} for row in rows])
        files = []
        updated = draft["updated_at"]
        for row in rows:
            vid = str(row["file_version_id"])
            source = conn.execute("SELECT id, file_id, tenant_id, original_filename, content_type, byte_size, sha256, object_key, status FROM product.file_versions WHERE id = %s", (vid,)).fetchone()
            version = self._version_from_row(source)
            validation = self.get_file_validation(principal, str(row["validation_job_id"]))
            checked = validation.get("result")
            if not isinstance(checked, dict) or checked.get("state") not in {"valid", "invalid", "error"}:
                checked = {"state": "checking", "reasons": []}
            match: dict[str, Any] | None = None
            if row["selection"] == "selected":
                payload = row["analysis_payload"] or {}
                result = row["analysis_result"] or {}
                status = row["analysis_status"]
                match = {
                    "state": "pending" if status == "queued" else "matching",
                    "metadata": {"customer": None, "model": None, "part": None, "part_number": None},
                    "document_type": None,
                    "rule": None,
                    "gaps": [],
                }
                if status in {"succeeded", "failed", "cancelled"}:
                    bound = (
                        payload.get("task_id") == task_id
                        and payload.get("task_revision", 0) == draft["input_revision"]
                        and payload.get("source_file_version_id") == vid
                        and result.get("source_file_version_id") == vid
                    )
                    state = result.get("matching_status") if status == "succeeded" and bound else "failed"
                    if not bound:
                        result = {"notices": ["matching_snapshot_invalid"]}
                    match.update(
                        state=state if state in {"matched", "unmatched", "failed"} else "failed",
                        metadata=result.get("metadata") or match["metadata"],
                        document_type=result.get("document_type"),
                        gaps=result.get("notices") or (["rule_matching_failed"] if state == "failed" else []),
                    )
                    rid = result.get("matched_rule_id")
                    if match["state"] == "matched":
                        allowed = any(candidate["id"] == rid for candidate in payload.get("candidate_rules", []))
                        rule = self._rule_for_tenant(conn, cast(str, rid), principal.tenant_id) if allowed else None
                        if rule:
                            match["rule"] = {"id": str(rule[0]), "title": rule[1], "body": rule[2]}
                        else:
                            match.update(state="failed", gaps=["matching_snapshot_invalid"])
                elif row["analysis_expired"]:
                    match["gaps"] = ["matching_result_unknown"]
                if row["analysis_updated_at"]:
                    updated = max(updated, row["analysis_updated_at"])
            files.append(
                {
                    "file": version.public(),
                    "selection": row["selection"],
                    "removal_reason": row["removal_reason"],
                    "validation_id": str(row["validation_job_id"]),
                    "validation": checked,
                    "match": match,
                }
            )
        selected = [row for row in files if row["selection"] == "selected"]
        matching = any(row["match"]["state"] in {"pending", "matching"} for row in selected)
        return {
            "id": task_id,
            "number": f"PPAP-{draft['task_day']:%Y%m%d}-{draft['task_sequence']:03d}",
            "state": "completed" if draft["task_completion_reason"] else "matching" if matching else "pending_confirm",
            "analysis_mode": draft["analysis_mode"],
            "created_at": draft["created_at"].isoformat(),
            "updated_at": updated.isoformat(),
            "source_draft_id": task_id,
            "input_revision": draft["input_revision"],
            "recognition_request_id": str(draft["recognition_request_id"] or draft["id"]),
            "round_id": None,
            "customer_preference": draft["customer_preference"],
            "files": files,
            "completion_reason": draft["task_completion_reason"],
            "estimated_points": None if matching or any(row["match"]["state"] == "failed" for row in selected) else sum(row["match"]["state"] == "matched" for row in selected) * 500,
        }

    def list_audit_tasks(self, principal: Principal, limit: int = 20, offset: int = 0, q: str = "", state: str = "") -> list[dict]:
        if state not in {"", "matching", "pending_confirm", "reviewing", "completed"}:
            raise ValueError("invalid_task_state")
        needle = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            rows = conn.execute(
                """SELECT d.id FROM product.audit_drafts d WHERE d.tenant_id = %s AND d.created_by = %s AND d.task_fingerprint IS NOT NULL
                AND EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id = d.id AND g.user_id = %s AND g.capability IN ('read', 'review'))
                AND (%s = '' OR EXISTS (SELECT 1 FROM product.draft_items i JOIN product.file_versions f ON f.id = i.file_version_id WHERE i.draft_id = d.id AND f.original_filename ILIKE %s))
                AND (%s = '' OR %s = CASE WHEN d.task_completion_reason IS NOT NULL THEN 'completed'
                    WHEN EXISTS (SELECT 1 FROM product.draft_items i JOIN engine.jobs j ON j.id = i.analysis_job_id WHERE i.draft_id = d.id AND j.status IN ('queued', 'leased')) THEN 'matching'
                    ELSE 'pending_confirm' END)
                ORDER BY d.created_at DESC, d.id DESC LIMIT %s OFFSET %s""",
                (principal.tenant_id, principal.user_id, principal.user_id, q, needle, state, state, limit, offset),
            ).fetchall()
            return [self._recognition_public(conn, principal, str(row[0])) for row in rows]

    def end_unmatched_task(self, principal: Principal, task_id: str, expected_revision: int, removals: list) -> dict:
        with self._conn() as conn, conn.transaction():
            draft = self._recognition_owner(conn, principal, task_id, lock=True)
            if type(expected_revision) is not int or expected_revision < 0:
                raise ValueError("invalid_task_revision")
            if draft["input_revision"] != expected_revision:
                raise ValueError("task_revision_conflict")
            task = self._recognition_public(conn, principal, task_id)
            if not isinstance(removals, list):
                raise ValueError("invalid_removal_reason")
            removed: dict[str, str] = {}
            for raw in removals:
                if not isinstance(raw, dict):
                    raise ValueError("invalid_removal_reason")
                vid, reason = _as_uuid(str(raw.get("file_version_id") or "")), raw.get("removal_reason")
                if not vid or vid in removed or not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
                    raise ValueError("invalid_removal_reason")
                row = next((row for row in task["files"] if row["file"]["id"] == vid), None)
                if not row or not (row["selection"] == "selected" and (row["match"] or {}).get("state") == "unmatched" or row["selection"] == "removed" and row["removal_reason"] == reason.strip()):
                    raise ValueError("task_not_zero_match")
                removed[vid] = reason.strip()
            if task["completion_reason"] == "no_matching_rules":
                return task
            selected = [row for row in task["files"] if row["selection"] == "selected"]
            if task["state"] != "pending_confirm" or not selected or any(row["match"]["state"] != "unmatched" for row in selected):
                raise ValueError("task_not_zero_match")
            for vid, reason in removed.items():
                conn.execute("UPDATE product.draft_items SET selection = 'removed', removal_reason = %s WHERE draft_id = %s AND file_version_id = %s", (reason, task_id, vid))
            conn.execute("UPDATE product.audit_drafts SET task_completion_reason = 'no_matching_rules', updated_at = now() WHERE id = %s", (task_id,))
            return self._recognition_public(conn, principal, task_id)

    def list_rules(self, tenant_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, title, body, created_at, predecessor_id FROM product.rule_versions WHERE tenant_id = %s ORDER BY created_at DESC, id DESC",
                (tenant_id,),
            ).fetchall()
        out = []
        for row in rows:
            body = row[2] or {}
            out.append(
                {
                    "id": str(row[0]),
                    "title": row[1],
                    "kind": str(body.get("kind") or "engine"),
                    "created_at": row[3].isoformat(),
                    "predecessor_id": str(row[4]) if row[4] else None,
                    **{field: body.get(field) for field in ("type", "rule_profile", "customers", "models", "parts")},
                }
            )
        return out

    def get_rule(self, tenant_id: str, rule_version_id: str) -> dict | None:
        parsed = _as_uuid(rule_version_id)
        if not parsed:
            return None
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT r.id, r.title, r.body, r.created_at, r.source_file_version_id,
                       fv.file_id, r.predecessor_id, r.created_by, r.case_context
                FROM product.rule_versions r
                LEFT JOIN product.file_versions fv ON fv.id = r.source_file_version_id
                WHERE r.tenant_id = %s AND r.id = %s
                """,
                (tenant_id, parsed),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": str(row[0]),
            "title": row[1],
            "body": row[2],
            "created_at": row[3].isoformat(),
            "source_file_version_id": str(row[4]) if row[4] else None,
            "source_file_id": str(row[5]) if row[5] else None,
            "predecessor_id": str(row[6]) if row[6] else None,
            "created_by": str(row[7]) if row[7] else None,
            "case_context": row[8] or {},
        }

    def publish_rule(self, principal: Principal, title, body, source_file_version_id, predecessor_id=None, request_id=None, case_context=None) -> dict:
        title, body = rule_title(title), normalize_rule_body(body)
        rule_id = _as_uuid(request_id) if request_id else str(uuid.uuid4())
        if not rule_id:
            raise ValueError("invalid_rule_request")
        context = case_context or {}
        if not isinstance(context, dict) or len(json.dumps(context, ensure_ascii=False).encode()) > 262144:
            raise ValueError("invalid_case_context")
        with self._conn() as conn:
            with conn.transaction():
                self._prepared_items(conn, principal, [{"file_version_id": source_file_version_id, "role": "reference"}])
                predecessor = _as_uuid(predecessor_id) if predecessor_id else None
                if predecessor_id and (not predecessor or not self._rule_for_tenant(conn, predecessor, principal.tenant_id)):
                    raise KeyError("missing")
                conn.execute(
                    """
                    INSERT INTO product.rule_versions
                        (id, tenant_id, title, body, source_file_version_id, predecessor_id, created_by, case_context)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING
                    """,
                    (rule_id, principal.tenant_id, title, Jsonb(body), source_file_version_id, predecessor, principal.user_id, Jsonb(context)),
                )
                existing = conn.execute("SELECT tenant_id, title, body, source_file_version_id, predecessor_id, case_context FROM product.rule_versions WHERE id = %s", (rule_id,)).fetchone()
                if not existing or str(existing[0]) != principal.tenant_id:
                    raise KeyError("missing")
                if (
                    existing[1] != title
                    or existing[2] != body
                    or str(existing[3]) != str(source_file_version_id)
                    or (str(existing[4]) if existing[4] else None) != (str(predecessor) if predecessor else None)
                    or existing[5] != context
                ):
                    raise ValueError("idempotency_conflict")
        return cast(dict, self.get_rule(principal.tenant_id, rule_id))

    def create_draft(self, principal: Principal, predecessor_id: str | None = None) -> dict:
        if predecessor_id:
            resource = self.resource(predecessor_id)
            if resource is None or resource.kind != "audit_round" or not can_read(principal, resource, self.grants(predecessor_id, principal.user_id)):
                raise KeyError("missing")
        draft_id = str(uuid.uuid4())
        with self._conn() as conn:
            with conn.transaction():
                mode = ANALYSIS_STANDARD
                if predecessor_id:
                    inherited = conn.execute(
                        "SELECT analysis_mode FROM product.audit_rounds WHERE id = %s",
                        (predecessor_id,),
                    ).fetchone()
                    if inherited:
                        mode = self._offered_analysis_mode(inherited[0], allow_reject=False)
                conn.execute(
                    "INSERT INTO product.resources (id, tenant_id, kind) VALUES (%s, %s, 'audit_draft')",
                    (draft_id, principal.tenant_id),
                )
                conn.execute(
                    """
                    INSERT INTO product.audit_drafts (id, tenant_id, created_by, status, predecessor_id, analysis_mode)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (draft_id, principal.tenant_id, principal.user_id, DRAFT_OPEN, predecessor_id, mode),
                )
                conn.execute(
                    """
                    INSERT INTO product.resource_grants (resource_id, user_id, capability)
                    VALUES (%s, %s, 'read'), (%s, %s, 'review')
                    ON CONFLICT DO NOTHING
                    """,
                    (draft_id, principal.user_id, draft_id, principal.user_id),
                )
                if predecessor_id:
                    writeback = self._target_row(conn, "round_writeback_targets", "round_id", predecessor_id)
                    if writeback:
                        self.save_draft_writeback(conn, draft_id, writeback)
        return cast(dict, self.get_draft(draft_id))

    def list_drafts(self, principal: Principal, limit: int = 20, offset: int = 0, q: str = "") -> list[dict]:
        needle = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT d.id FROM product.audit_drafts d
                   WHERE d.tenant_id = %s AND d.status = %s
                     AND d.task_fingerprint IS NULL
                     AND EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id = d.id
                                 AND g.user_id = %s AND g.capability IN ('read', 'review'))
                     AND EXISTS (SELECT 1 FROM product.draft_items i WHERE i.draft_id = d.id)
                     AND (%s = '' OR d.case_context::text ILIKE %s OR EXISTS (
                         SELECT 1 FROM product.draft_items i JOIN product.file_versions f ON f.id = i.file_version_id
                         WHERE i.draft_id = d.id AND f.original_filename ILIKE %s))
                   ORDER BY d.updated_at DESC, d.id DESC LIMIT %s OFFSET %s""",
                (principal.tenant_id, DRAFT_OPEN, principal.user_id, q, needle, needle, limit, offset),
            ).fetchall()
            return [cast(dict, self._draft_public(conn, row[0])) for row in rows]

    def get_draft(self, draft_id: str) -> dict | None:
        parsed = _as_uuid(draft_id)
        if not parsed:
            return None
        with self._conn() as conn:
            return self._draft_public(conn, parsed)

    def draft_round_id(self, draft_id: str) -> str | None:
        parsed = _as_uuid(draft_id)
        if not parsed:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM product.audit_rounds WHERE source_draft_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
                (parsed,),
            ).fetchone()
        return str(row[0]) if row else None

    def _offered_analysis_mode(self, value, allow_reject: bool = True) -> str:
        mode = normalize_analysis_mode(value)
        if mode != ANALYSIS_DEEP:
            return mode
        if self.settings and self.settings.deep_offered():
            return mode
        if allow_reject:
            raise ValueError("deep_not_available")
        return ANALYSIS_STANDARD

    def save_draft(
        self,
        principal: Principal,
        draft_id: str,
        items: list[dict] | None,
        analysis_mode: str | None = None,
        writeback_target=None,
        case_context: dict | None = None,
    ) -> dict:
        parsed = _as_uuid(draft_id)
        if not parsed:
            raise KeyError("missing")
        with self._conn() as conn:
            with conn.transaction():
                draft = conn.execute(
                    """
                    SELECT id, tenant_id, status, task_fingerprint FROM product.audit_drafts
                    WHERE id = %s FOR UPDATE
                    """,
                    (parsed,),
                ).fetchone()
                if not draft or str(draft[1]) != principal.tenant_id:
                    raise KeyError("missing")
                if draft[3] is not None:
                    raise ValueError("task_input_locked")
                if draft[2] != DRAFT_OPEN:
                    raise ValueError("draft_already_accepted")
                if items is not None:
                    prepared = self._bound_audit_items(conn, principal, items, require_rules=False)
                    conn.execute("DELETE FROM product.draft_items WHERE draft_id = %s", (parsed,))
                    for row in prepared:
                        conn.execute(
                            """
                            INSERT INTO product.draft_items (draft_id, file_version_id, role, rule_version_id)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (parsed, row["file_version_id"], row["role"], row["rule_version_id"]),
                        )
                if analysis_mode is not None:
                    mode = self._offered_analysis_mode(analysis_mode)
                    conn.execute(
                        "UPDATE product.audit_drafts SET analysis_mode = %s, updated_at = now() WHERE id = %s",
                        (mode, parsed),
                    )
                elif items is not None:
                    conn.execute("UPDATE product.audit_drafts SET updated_at = now() WHERE id = %s", (parsed,))
                if writeback_target is not None:
                    self.save_draft_writeback(conn, parsed, normalize_external_ref(writeback_target) if writeback_target else None)
                    conn.execute("UPDATE product.audit_drafts SET updated_at = now() WHERE id = %s", (parsed,))
                if case_context is not None:
                    if not isinstance(case_context, dict) or len(json.dumps(case_context, ensure_ascii=False).encode()) > 262144:
                        raise ValueError("invalid_case_context")
                    conn.execute("UPDATE product.audit_drafts SET case_context = %s, updated_at = now() WHERE id = %s", (Jsonb(case_context), parsed))
                return cast(dict, self._draft_public(conn, parsed))

    def accept_draft(self, principal: Principal, draft_id: str, idempotency_key: str, predecessor_id: str | None = None) -> dict:
        draft = self.get_draft(draft_id)
        if draft and draft.get("predecessor_id"):
            if predecessor_id and predecessor_id != draft["predecessor_id"]:
                raise ValueError("idempotency_conflict")
            predecessor_id = draft["predecessor_id"]
        key = normalize_idempotency_key(idempotency_key)
        parsed = _as_uuid(draft_id)
        if not parsed:
            raise KeyError("missing")
        if predecessor_id:
            resource = self.resource(predecessor_id)
            if resource is None or resource.kind != "audit_round" or not can_read(principal, resource, self.grants(predecessor_id, principal.user_id)):
                raise KeyError("missing")
        with self._conn() as conn:
            with conn.transaction():
                return self._accept_locked(conn, principal, key, source_draft_id=parsed, predecessor_id=predecessor_id, items=None)

    def resubmit_round(
        self,
        principal: Principal,
        round_id: str,
        idempotency_key: str,
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
                    SELECT id, tenant_id FROM product.audit_rounds
                    WHERE id = %s
                    """,
                    (parsed,),
                ).fetchone()
                if not current or str(current[1]) != principal.tenant_id:
                    raise KeyError("missing")
                if items is None:
                    rows = conn.execute(
                        "SELECT file_version_id, role, rule_version_id FROM product.round_inputs WHERE round_id = %s",
                        (parsed,),
                    ).fetchall()
                    items = [{"file_version_id": str(row[0]), "role": row[1], "rule_version_id": str(row[2]) if row[2] else None} for row in rows]
                return self._accept_locked(
                    conn,
                    principal,
                    key,
                    source_draft_id=None,
                    predecessor_id=parsed,
                    items=items,
                )

    def get_report(self, principal: Principal, round_id: str, fingerprint: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, status, document_id, error_code FROM product.report_exports WHERE round_id=%s AND tenant_id=%s AND created_by=%s AND fingerprint=%s",
                (round_id, principal.tenant_id, principal.user_id, fingerprint),
            ).fetchone()
        return {"id": str(row[0]), "status": row[1], "document_id": row[2], "error_code": row[3]} if row else None

    def claim_report(self, principal: Principal, round_id: str, fingerprint: str) -> str | None:
        ident = str(uuid.uuid4())
        with self._conn() as conn:
            row = conn.execute(
                """INSERT INTO product.report_exports (id,round_id,tenant_id,created_by,fingerprint,status)
                   VALUES (%s,%s,%s,%s,%s,'creating')
                   ON CONFLICT (round_id, created_by, fingerprint) DO UPDATE
                   SET id=EXCLUDED.id, status='creating', error_code=NULL
                   WHERE report_exports.status='failed' AND report_exports.document_id IS NULL
                   RETURNING id""",
                (ident, round_id, principal.tenant_id, principal.user_id, fingerprint),
            ).fetchone()
        return ident if row else None

    def settle_report(self, principal: Principal, ident: str, status: str, document_id: str | None = None, error_code: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE product.report_exports SET status=%s, document_id=COALESCE(%s,document_id), error_code=%s
                   WHERE id=%s AND tenant_id=%s AND created_by=%s AND status IN ('creating','writing')""",
                (status, document_id, error_code, ident, principal.tenant_id, principal.user_id),
            )

    def get_round(self, round_id: str) -> dict | None:
        parsed = _as_uuid(round_id)
        if not parsed:
            return None
        with self._conn() as conn:
            return self._round_public(conn, parsed)

    def list_rounds(self, principal: Principal, limit: int = 50, offset: int = 0, q: str = "", execution: str = "", unreviewed: bool = False) -> list[dict]:
        needle = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT r.id FROM product.audit_rounds r
                   WHERE r.tenant_id = %s
                     AND (%s = '' OR r.execution = %s)
                     AND (NOT %s OR EXISTS (
                         SELECT 1 FROM product.findings f WHERE f.round_id = r.id
                         AND NOT EXISTS (SELECT 1 FROM product.reviews v WHERE v.round_id = f.round_id AND v.item_key = f.item_key)))
                     AND (%s = '' OR r.case_context::text ILIKE %s OR EXISTS (
                         SELECT 1 FROM product.round_inputs i JOIN product.file_versions f ON f.id = i.file_version_id
                         WHERE i.round_id = r.id AND (f.original_filename ILIKE %s OR i.rule_title ILIKE %s)))
                   ORDER BY r.created_at DESC, r.id DESC LIMIT %s OFFSET %s""",
                (principal.tenant_id, execution, execution, unreviewed, q, needle, needle, needle, limit, offset),
            ).fetchall()
            return [cast(dict, self._round_history(conn, row[0])) for row in rows]

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

    def round_bundle(self, round_id: str):
        import tempfile
        from zipfile import ZIP_DEFLATED, ZipFile

        from ppap_core.bundles import archive_member_names

        work = self.round_work(round_id)
        if work is None:
            return None
        names = archive_member_names([(item.file_version_id, item.filename) for item in work.inputs])
        versions = []
        total = 0
        for item in work.inputs:
            version = self.get_version(item.file_id, item.file_version_id)
            if version is None:
                raise ValueError("bundle_source_missing")
            total += int(version.byte_size or 0)
            versions.append(version)
        if total > MAX_UPLOAD_BYTES:
            raise ValueError("bundle_too_large")
        tmp = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        with ZipFile(tmp, "w", compression=ZIP_DEFLATED) as archive:
            for version, name in zip(versions, names):
                archive.writestr(name, self.version_bytes(version))
        tmp.seek(0)

        def chunks():
            try:
                while True:
                    block = tmp.read(65536)
                    if not block:
                        break
                    yield block
            finally:
                tmp.close()

        return f"ppap-round-{work.round_id[:8]}.zip", chunks()

    def save_review(self, principal: Principal, round_id: str, item_key: str, judgment: str, rationale: str, next_step: str, version: int, citations: list[dict] | None = None) -> dict:
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
                    coverage_row = conn.execute("SELECT coverage FROM product.audit_rounds WHERE id = %s", (parsed,)).fetchone()
                    coverage = (coverage_row[0] or {}).get("rule_items", []) if coverage_row else []
                    if not any(key == f"coverage:{row.get('file_version_id')}:{row.get('id')}" and row.get("coverage") != "executed" for row in coverage):
                        raise KeyError("missing")
                cites = citations or []
                if not isinstance(cites, list) or len(cites) > 20:
                    raise ValueError("invalid_review_citations")
                versions = {str(row[0]) for row in conn.execute("SELECT file_version_id FROM product.round_inputs WHERE round_id = %s", (parsed,)).fetchall()}
                for cite in cites:
                    if (
                        not isinstance(cite, dict)
                        or cite.get("file_version_id") not in versions
                        or any(not isinstance(cite.get(field), str) or not cite[field].strip() or len(cite[field]) > 4000 for field in ("location", "excerpt"))
                    ):
                        raise ValueError("invalid_review_citations")
                if text.startswith("manual-") and (not why or not cites):
                    raise ValueError("manual_review_evidence_required")
                if seen == 0:
                    try:
                        conn.execute(
                            """
                            INSERT INTO product.reviews
                                (round_id, item_key, user_id, judgment, rationale, next_step, version, citations)
                            VALUES (%s, %s, %s, %s, %s, %s, 1, %s)
                            """,
                            (parsed, key, principal.user_id, text, why, nxt, Jsonb(cites)),
                        )
                    except UniqueViolation as exc:
                        raise ValueError("review_conflict") from exc
                else:
                    row = conn.execute(
                        """
                        UPDATE product.reviews
                        SET judgment = %s, rationale = %s, next_step = %s, user_id = %s,
                            version = version + 1, updated_at = now(), citations = %s
                        WHERE round_id = %s AND item_key = %s AND version = %s
                        RETURNING version
                        """,
                        (text, why, nxt, principal.user_id, Jsonb(cites), parsed, key, seen),
                    ).fetchone()
                    if not row:
                        raise ValueError("review_conflict")
                return cast(dict, self._round_public(conn, parsed))

    def round_work(self, round_id: str) -> RoundWork | None:
        parsed = _as_uuid(round_id)
        if not parsed:
            return None
        with self._conn() as conn:
            head = conn.execute(
                """
                SELECT id, tenant_id, result_attempt, analysis_mode
                FROM product.audit_rounds WHERE id = %s
                """,
                (parsed,),
            ).fetchone()
            if not head:
                return None
            rows = conn.execute(
                """
                SELECT i.role, fv.file_id, fv.id, fv.original_filename, fv.status, fv.object_key, fv.content_type,
                       i.rule_version_id, i.rule_title, i.rule_body, fv.sha256, i.rule_execution,
                       rv.source_file_version_id
                FROM product.round_inputs i
                JOIN product.file_versions fv ON fv.id = i.file_version_id
                LEFT JOIN product.rule_versions rv ON rv.id = i.rule_version_id AND rv.tenant_id = fv.tenant_id
                WHERE i.round_id = %s
                ORDER BY i.role, fv.original_filename
                """,
                (parsed,),
            ).fetchall()
        return RoundWork(
            str(head[0]),
            str(head[1]),
            [
                RoundInput(
                    row[0],
                    str(row[1]),
                    str(row[2]),
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    str(row[7]) if row[7] else None,
                    row[8],
                    row[9],
                    row[10],
                    row[11],
                    str(row[12]) if row[12] else None,
                )
                for row in rows
            ],
            head[2],
            normalize_analysis_mode(head[3]),
        )

    def claim_job(self, worker_ident: str) -> ClaimedJob | None:
        with self._conn() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    SELECT id, kind, payload, tenant_id FROM engine.jobs
                    WHERE (
                        (status = 'queued' AND (lease_until IS NULL OR lease_until < now()))
                        OR (status = 'leased' AND kind <> ALL(%s) AND lease_until IS NOT NULL AND lease_until < now())
                    )
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    (list(FILE_PREPARATION_KINDS),),
                ).fetchone()
                if not row:
                    return None
                updated = cast(
                    tuple[Any, ...],
                    conn.execute(
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
                    ).fetchone(),
                )
                conn.execute(
                    """
                    INSERT INTO engine.attempts (id, job_id, attempt, worker_id, diagnostic)
                    VALUES (gen_random_uuid(), %s, %s, %s, '{}'::jsonb)
                    """,
                    (row[0], updated[0], worker_ident),
                )
                payload = updated[1] or {}
                round_id = payload.get("round_id") if isinstance(payload, dict) else None
                tenant_id = row[3]
                if round_id:
                    conn.execute(
                        """
                        UPDATE product.audit_rounds
                        SET execution = %s
                        WHERE id = %s AND execution IN (%s, %s, %s)
                        """,
                        (EXEC_RUNNING, round_id, EXEC_QUEUED, EXEC_RUNNING, EXEC_FAILED),
                    )
                return ClaimedJob(
                    str(row[0]),
                    row[1],
                    payload,
                    int(updated[0]),
                    str(round_id) if round_id else None,
                    str(tenant_id) if tenant_id else None,
                )

    def renew_job_lease(self, claimed: ClaimedJob, worker_ident: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                """UPDATE engine.jobs SET lease_until = now() + interval '30 seconds', updated_at = now()
                WHERE id = %s AND attempt = %s AND lease_owner = %s AND status = 'leased'
                  AND (kind <> 'audit' OR COALESCE(payload->>'analysis_mode', 'standard') <> 'deep' OR lease_until > now()) RETURNING id""",
                (claimed.id, claimed.attempt, worker_ident),
            ).fetchone()
            return row is not None

    def audit_progress(self, claimed: ClaimedJob, worker_ident: str, progress: dict) -> bool:
        safe = dict(progress)
        safe["updated_at"] = _now().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """UPDATE engine.attempts a
                   SET diagnostic = a.diagnostic || %s
                   FROM engine.jobs j
                   WHERE j.id = %s AND j.attempt = %s AND j.lease_owner = %s
                     AND j.status = 'leased' AND j.lease_until > now()
                     AND a.job_id = j.id AND a.attempt = j.attempt RETURNING a.id""",
                (Jsonb({"progress": safe}), claimed.id, claimed.attempt, worker_ident),
            ).fetchone()
            return row is not None

    def complete_job(self, claimed: ClaimedJob, worker_ident: str, result: dict, outcome: str = "succeeded") -> bool:
        coverage = result.get("coverage") or {}
        findings = result.get("findings") or []
        execution = EXEC_COMPLETED if outcome == "succeeded" else EXEC_FAILED
        with self._conn() as conn:
            with conn.transaction():
                won = conn.execute(
                    """
                    UPDATE engine.jobs
                    SET status = %s, lease_until = NULL, updated_at = now(),
                        result = CASE WHEN kind = ANY(%s) THEN %s ELSE result END
                    WHERE id = %s AND attempt = %s AND lease_owner = %s AND status = 'leased'
                      AND (kind <> 'audit' OR COALESCE(payload->>'analysis_mode', 'standard') <> 'deep' OR lease_until > now())
                    RETURNING id
                    """,
                    (
                        "succeeded" if outcome == "succeeded" else "failed",
                        list(FILE_PREPARATION_KINDS),
                        Jsonb(result) if claimed.kind in FILE_PREPARATION_KINDS else None,
                        claimed.id,
                        claimed.attempt,
                        worker_ident,
                    ),
                ).fetchone()
                if not won:
                    return False
                conn.execute(
                    """
                    UPDATE engine.attempts
                    SET finished_at = now(), outcome = %s, diagnostic = diagnostic || %s
                    WHERE job_id = %s AND attempt = %s
                    """,
                    (
                        outcome,
                        Jsonb(_attempt_diagnostic(claimed, result, findings)),
                        claimed.id,
                        claimed.attempt,
                    ),
                )
                if not claimed.round_id:
                    return True
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
            prepared.append({"file_version_id": str(row[0]), "role": role, "file_id": str(row[1]), "original_filename": row[4]})
        return prepared

    def _bound_audit_items(self, conn, principal, items, *, require_rules=True) -> list[dict]:
        prepared = self._prepared_items(conn, principal, items)
        for row, raw in zip(prepared, items):
            rule_id = raw.get("rule_version_id")
            rule = None
            if row["role"] == "reference" and rule_id:
                raise ValueError("reference_rule_forbidden")
            if row["role"] == "target":
                if not rule_id and require_rules:
                    raise ValueError("rule_required")
                if rule_id:
                    rid = _as_uuid(str(rule_id))
                    rule = self._rule_for_tenant(conn, rid, principal.tenant_id) if rid else None
                    if not rule:
                        raise ValueError("unknown_rule")
            execution = None
            if rule and self.rule_execution_factory is not None:
                execution = self.rule_execution_factory(rule[2], row.get("original_filename") or "")
            cast(dict[str, Any], row).update(
                rule_version_id=str(rule[0]) if rule else None,
                rule_title=rule[1] if rule else None,
                rule_body=rule[2] if rule else None,
                rule_execution=execution,
            )
        return prepared

    def _accept_locked(
        self,
        conn,
        principal: Principal,
        key: str,
        source_draft_id: str | None,
        predecessor_id: str | None,
        items: list[dict] | None,
    ) -> dict:
        # Serialize one tenant/key before reading existing acceptance, including
        # competing requests from different drafts. Hash collisions only serialize.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (principal.tenant_id + ":" + key,))
        existing = conn.execute(
            """
            SELECT id, request_fingerprint, predecessor_id, analysis_mode FROM product.audit_rounds
            WHERE tenant_id = %s AND idempotency_key = %s
            """,
            (principal.tenant_id, key),
        ).fetchone()
        draft_status = None
        analysis_mode = ANALYSIS_STANDARD
        if source_draft_id:
            draft = conn.execute(
                """
                SELECT id, tenant_id, status, task_fingerprint FROM product.audit_drafts
                WHERE id = %s FOR UPDATE
                """,
                (source_draft_id,),
            ).fetchone()
            if not draft or str(draft[1]) != principal.tenant_id:
                raise KeyError("missing")
            if draft[3] is not None:
                raise ValueError("task_input_locked")
            draft_status = draft[2]
            if items is None:
                rows = conn.execute(
                    "SELECT file_version_id, role, rule_version_id FROM product.draft_items WHERE draft_id = %s",
                    (source_draft_id,),
                ).fetchall()
                items = [{"file_version_id": str(row[0]), "role": row[1], "rule_version_id": str(row[2]) if row[2] else None} for row in rows]
            mode_row = conn.execute(
                "SELECT analysis_mode FROM product.audit_drafts WHERE id = %s",
                (source_draft_id,),
            ).fetchone()
            analysis_mode = normalize_analysis_mode(mode_row[0] if mode_row else ANALYSIS_STANDARD)
        elif predecessor_id:
            mode_row = conn.execute(
                "SELECT analysis_mode FROM product.audit_rounds WHERE id = %s",
                (predecessor_id,),
            ).fetchone()
            analysis_mode = normalize_analysis_mode(mode_row[0] if mode_row else ANALYSIS_STANDARD)
        if items is None:
            items = []
        prepared = self._bound_audit_items(conn, principal, items)
        require_targets(prepared)
        fingerprint = request_fingerprint(prepared)
        if existing:
            resource = self.resource(str(existing[0]))
            if resource is None or not can_read(principal, resource, self.grants(str(existing[0]), principal.user_id)):
                raise KeyError("missing")
            if existing[1] != fingerprint or (str(existing[2]) if existing[2] else None) != predecessor_id or existing[3] != analysis_mode:
                raise ValueError("idempotency_conflict")
            return cast(dict, self._round_public(conn, str(existing[0])))
        analysis_mode = self._offered_analysis_mode(analysis_mode)
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
                idempotency_key,
                request_fingerprint, execution, analysis_mode
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                round_id,
                principal.tenant_id,
                principal.user_id,
                source_draft_id,
                predecessor_id,
                key,
                fingerprint,
                EXEC_QUEUED,
                analysis_mode,
            ),
        )
        for row in prepared:
            conn.execute(
                """INSERT INTO product.round_inputs (round_id, file_version_id, role, rule_version_id, rule_title, rule_body, rule_execution)
                VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    round_id,
                    row["file_version_id"],
                    row["role"],
                    row["rule_version_id"],
                    row["rule_title"],
                    Jsonb(row["rule_body"]) if row["rule_body"] is not None else None,
                    Jsonb(row["rule_execution"]) if row.get("rule_execution") is not None else None,
                ),
            )
        if source_draft_id:
            conn.execute("UPDATE product.audit_rounds SET case_context = (SELECT case_context FROM product.audit_drafts WHERE id = %s) WHERE id = %s", (source_draft_id, round_id))
        elif predecessor_id:
            conn.execute("UPDATE product.audit_rounds SET case_context = (SELECT case_context FROM product.audit_rounds WHERE id = %s) WHERE id = %s", (predecessor_id, round_id))
        target = self.draft_writeback(conn, source_draft_id) if source_draft_id else None
        if target is None and predecessor_id:
            target = self._target_row(conn, "round_writeback_targets", "round_id", predecessor_id)
        self.copy_writeback_to_round(conn, round_id, target)
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
            (
                job_id,
                principal.tenant_id,
                round_id,
                Jsonb(job_payload(round_id, analysis_mode, self.settings.deep_executor_revision)),
            ),
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
        self._record_round_usage(conn, principal, round_id, prepared)
        return cast(dict, self._round_public(conn, round_id))

    def _record_round_usage(self, conn, principal, round_id, prepared) -> None:
        if not self.settings.usage_charge_enabled:
            return
        item_id = self.settings.feishu_ai_item_id.strip()
        if not item_id or principal.provider != PROVIDER_FEISHU:
            return
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("usage:" + principal.tenant_id + ":" + item_id,))
        consumed = self._trial_ledger_consumed(conn, principal.tenant_id, item_id)
        remaining = remaining_trial_points(self.settings.feishu_free_trial_quota_points, consumed)
        points = audit_usage_points(sum(row["role"] == "target" for row in prepared))
        for amount, trial in split_trial_and_partner(remaining, points):
            conn.execute(
                """INSERT INTO product.usage_events
                   (id, tenant_id, user_id, ai_item_id, consumed_points, is_free_trial,
                    external_status, trace_id, round_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (str(uuid.uuid4()), principal.tenant_id, principal.user_id, item_id, amount, trial, "none" if trial else "pending", round_id + (":trial" if trial else ":partner"), round_id),
            )

    def _draft_public(self, conn, draft_id: str) -> dict | None:
        row = conn.execute(
            """
            SELECT d.id, d.tenant_id, d.status, d.updated_at, d.predecessor_id, d.analysis_mode, d.case_context
            FROM product.audit_drafts d
            WHERE d.id = %s
            """,
            (draft_id,),
        ).fetchone()
        if not row:
            return None
        items = conn.execute(
            """
            SELECT i.role, fv.id, fv.file_id, fv.original_filename, fv.status, i.rule_version_id, r.title, r.body
            FROM product.draft_items i
            JOIN product.file_versions fv ON fv.id = i.file_version_id
            LEFT JOIN product.rule_versions r ON r.id = i.rule_version_id
            WHERE i.draft_id = %s
            ORDER BY i.role, fv.original_filename
            """,
            (draft_id,),
        ).fetchall()
        return {
            "id": str(row[0]),
            "status": row[2],
            "updated_at": row[3].isoformat(),
            "predecessor_id": str(row[4]) if row[4] else None,
            "analysis_mode": normalize_analysis_mode(row[5]),
            "case_context": row[6] or {},
            "writeback": public_target(self.draft_writeback(conn, str(row[0]))),
            "items": [
                {
                    "role": item[0],
                    "file_version_id": str(item[1]),
                    "file_id": str(item[2]),
                    "original_filename": item[3],
                    "status": item[4],
                    "rule_version_id": str(item[5]) if item[5] else None,
                    "rule": {"id": str(item[5]), "title": item[6], "body": item[7]} if item[5] else None,
                }
                for item in items
            ],
        }

    def _round_history(self, conn, round_id) -> dict | None:
        row = conn.execute(
            """
            SELECT id, execution, predecessor_id, created_at, analysis_mode, case_context, coverage
            FROM product.audit_rounds WHERE id = %s
            """,
            (round_id,),
        ).fetchone()
        if not row:
            return None
        inputs = conn.execute(
            """
            SELECT i.role, fv.original_filename, fv.id, i.rule_version_id, i.rule_title
            FROM product.round_inputs i
            JOIN product.file_versions fv ON fv.id = i.file_version_id
            WHERE i.round_id = %s
            ORDER BY i.role, fv.original_filename
            """,
            (round_id,),
        ).fetchall()
        counts = conn.execute(
            "SELECT status, COUNT(*), array_agg(item_key) FROM product.findings WHERE round_id = %s GROUP BY status",
            (round_id,),
        ).fetchall()
        result_counts = {"PASS": 0, "NG": 0, "ERR": 0, "UNABLE": 0, "NA": 0}
        finding_keys = set()
        reviewable_keys = set()
        for status, n, keys in counts:
            finding_keys.update(keys)
            if status in result_counts:
                result_counts[status] = int(n)
                if status != "NA":
                    reviewable_keys.update(keys)
        coverage_items = (row[6] or {}).get("rule_items", [])
        uncovered = [item for item in coverage_items if isinstance(item, dict) and item.get("coverage") != "executed" and item.get("item_key") not in finding_keys]
        uncovered_count = len(uncovered)
        reviewable_keys.update(f"coverage:{item.get('file_version_id')}:{item.get('id')}" for item in uncovered)
        versions = {str(item[2]) for item in inputs}
        # Match the review workspace: a stored row alone does not complete a judgment.
        reviewed_count = sum(
            key in reviewable_keys
            and judgment in {"confirm", "disagree", "unknown", "manual-satisfied", "manual-unsatisfied"}
            and bool(rationale.strip())
            and (
                not judgment.startswith("manual-")
                or isinstance(citations, list)
                and bool(citations)
                and all(
                    isinstance(cite, dict) and cite.get("file_version_id") in versions and all(isinstance(cite.get(field), str) and cite[field].strip() for field in ("location", "excerpt"))
                    for cite in citations
                )
            )
            for key, judgment, rationale, citations in conn.execute("SELECT item_key, judgment, rationale, citations FROM product.reviews WHERE round_id = %s", (round_id,)).fetchall()
        )
        try:
            registration = json.loads((row[5] or {}).get("registration", "{}"))
            scope = registration.get("scope", {}) if isinstance(registration, dict) else {}
            scope = scope if isinstance(scope, dict) else {}
        except (ValueError, TypeError):
            scope = {}
        labels = {key: value.strip()[:200] for key, value in scope.items() if key in {"customer", "supplier", "part", "revision", "plant"} and isinstance(value, str)}
        return {
            "id": str(row[0]),
            "execution": row[1],
            "predecessor_id": str(row[2]) if row[2] else None,
            "created_at": row[3].isoformat() if row[3] is not None else None,
            "analysis_mode": normalize_analysis_mode(row[4]),
            "inputs": [
                {
                    "role": item[0],
                    "original_filename": item[1],
                    "file_version_id": str(item[2]),
                    "rule": {"id": str(item[3]), "title": item[4]} if item[3] else None,
                }
                for item in inputs
            ],
            "result_counts": result_counts,
            "reviewed_count": reviewed_count,
            "uncovered_count": uncovered_count,
            "case_title": labels.get("part", ""),
            "case_scope": " · ".join(filter(None, (labels.get("customer"), labels.get("supplier"), f"图纸 Rev {labels['revision']}" if labels.get("revision") else "", labels.get("plant")))),
        }

    def _round_public(self, conn, round_id: str) -> dict | None:
        row = conn.execute(
            """
            SELECT id, execution, coverage, predecessor_id,
                   source_draft_id, result_attempt, created_at, analysis_mode, case_context
            FROM product.audit_rounds WHERE id = %s
            """,
            (round_id,),
        ).fetchone()
        if not row:
            return None
        inputs = conn.execute(
            """
            SELECT i.role, fv.id, fv.file_id, fv.original_filename, fv.status, i.rule_version_id, i.rule_title, i.rule_body
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
                   rv.judgment, rv.rationale, rv.next_step, rv.version, rv.citations
            FROM product.findings f
            LEFT JOIN product.reviews rv ON rv.round_id = f.round_id AND rv.item_key = f.item_key
            WHERE f.round_id = %s
            ORDER BY f.item_key
            """,
            (round_id,),
        ).fetchall()
        # A clause ID is an identity, not a lexical or numeric display order.
        clause_order = {identity: position for position, identity in enumerate((str(item[1]), str(clause["id"])) for item in inputs for clause in (item[7] or {}).get("rules", []))}
        findings.sort(
            key=lambda finding: (
                clause_order.get(
                    cast(
                        tuple[str, str],
                        ((finding[6] or {}).get("file_version_id"), str((finding[6] or {}).get("rule_item_id", ""))),
                    ),
                    len(clause_order),
                ),
                finding[0],
            )
        )
        job = conn.execute(
            """
            SELECT j.payload, a.diagnostic, j.updated_at FROM engine.jobs j
            LEFT JOIN engine.attempts a ON a.job_id = j.id AND a.attempt = j.attempt
            WHERE j.audit_round_id = %s AND j.kind = 'audit'
            ORDER BY j.created_at
            LIMIT 1
            """,
            (round_id,),
        ).fetchone()
        payload = job[0] if job else {}
        diagnostic = job[1] if job and job[1] else {}
        progress = diagnostic.get("progress")
        if progress and row[1] == EXEC_RUNNING:
            progress = {**progress, "updated_at": job[2].isoformat()}
        coverage = dict(row[2] or {})
        reviews = {
            review[0]: {"judgment": review[1], "rationale": review[2], "next_step": review[3], "version": review[4], "citations": review[5] or []}
            for review in conn.execute(
                "SELECT item_key, judgment, rationale, next_step, version, citations FROM product.reviews WHERE round_id = %s AND item_key LIKE 'coverage:%%'", (round_id,)
            ).fetchall()
        }
        if coverage.get("rule_items"):
            coverage["rule_items"] = [{**item, "review": reviews.get(f"coverage:{item.get('file_version_id')}:{item.get('id')}")} for item in coverage["rule_items"]]
        return {
            "id": str(row[0]),
            "execution": row[1],
            "coverage": coverage,
            "predecessor_id": str(row[3]) if row[3] else None,
            "source_draft_id": str(row[4]) if row[4] else None,
            "result_attempt": row[5],
            "created_at": row[6].isoformat() if row[6] is not None else None,
            "analysis_mode": normalize_analysis_mode(row[7]),
            "case_context": row[8] or {},
            "writeback": public_target(self._target_row(conn, "round_writeback_targets", "round_id", str(row[0]))),
            **_executor_public(payload),
            "progress": progress,
            "failure": diagnostic.get("failure"),
            "inputs": [
                {
                    "role": item[0],
                    "file_version_id": str(item[1]),
                    "file_id": str(item[2]),
                    "original_filename": item[3],
                    "status": item[4],
                    "rule_version_id": str(item[5]) if item[5] else None,
                    "rule": {"id": str(item[5]), "title": item[6], "body": item[7]} if item[5] else None,
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
                        "citations": item[11] or [],
                    },
                }
                for item in findings
            ],
        }

    def trial_consumed_points(self, tenant_id: str, ai_item_id: str) -> int:
        with self._conn() as conn:
            return self._trial_ledger_consumed(conn, tenant_id, ai_item_id)

    def trial_baseline_points(self, tenant_id: str, ai_item_id: str) -> int:
        with self._conn() as conn:
            return self._trial_baseline_consumed(conn, tenant_id, ai_item_id)

    def _trial_baseline_consumed(self, conn, tenant_id: str, ai_item_id: str) -> int:
        row = conn.execute(
            """SELECT b.consumed_points
               FROM product.tenants t
               JOIN product.trial_cutover_baselines b
                 ON b.provider = t.provider AND b.tenant_key = t.external_key AND b.ai_item_id = %s
               WHERE t.id = %s""",
            (ai_item_id, tenant_id),
        ).fetchone()
        return int(row[0]) if row else 0

    def _trial_ledger_consumed(self, conn, tenant_id: str, ai_item_id: str) -> int:
        current = int(
            conn.execute(
                """SELECT COALESCE(SUM(consumed_points), 0) FROM product.usage_events
               WHERE tenant_id = %s AND ai_item_id = %s AND is_free_trial""",
                (tenant_id, ai_item_id),
            ).fetchone()[0]
        )
        return current + self._trial_baseline_consumed(conn, tenant_id, ai_item_id)

    def file_source(self, file_version_id: str) -> dict | None:
        vid = _as_uuid(file_version_id)
        if not vid:
            return None
        with self._conn() as conn:
            return self._target_row(conn, "file_version_sources", "file_version_id", vid)

    def save_file_source(self, file_version_id: str, object_kind: str, object_ref: str) -> None:
        vid = _as_uuid(file_version_id)
        if not vid:
            raise ValueError("invalid_items")
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO product.file_version_sources (file_version_id, provider, object_kind, object_ref)
                   VALUES (%s, 'feishu', %s, %s)
                   ON CONFLICT (file_version_id) DO UPDATE SET object_kind = EXCLUDED.object_kind, object_ref = EXCLUDED.object_ref""",
                (vid, object_kind, object_ref),
            )

    def save_draft_writeback(self, conn, draft_id: str, target: dict | None) -> None:
        conn.execute("DELETE FROM product.draft_writeback_targets WHERE draft_id = %s", (draft_id,))
        if not target:
            return
        conn.execute(
            """INSERT INTO product.draft_writeback_targets (draft_id, provider, object_kind, object_ref)
               VALUES (%s, %s, %s, %s)""",
            (draft_id, target["provider"], target["object_kind"], target["object_ref"]),
        )

    def _target_row(self, conn, table: str, key: str, ident: str) -> dict | None:
        row = conn.execute(
            f"SELECT provider, object_kind, object_ref FROM product.{table} WHERE {key} = %s",
            (ident,),
        ).fetchone()
        if not row:
            return None
        return {"provider": row[0], "object_kind": row[1], "object_ref": row[2]}

    def draft_writeback(self, conn, draft_id: str) -> dict | None:
        return self._target_row(conn, "draft_writeback_targets", "draft_id", draft_id)

    def round_writeback(self, round_id: str) -> dict | None:
        parsed = _as_uuid(round_id)
        if not parsed:
            return None
        with self._conn() as conn:
            return self._target_row(conn, "round_writeback_targets", "round_id", parsed)

    def copy_writeback_to_round(self, conn, round_id: str, target: dict | None) -> None:
        if not target:
            return
        conn.execute(
            """INSERT INTO product.round_writeback_targets (round_id, provider, object_kind, object_ref)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (round_id) DO UPDATE SET object_kind = EXCLUDED.object_kind, object_ref = EXCLUDED.object_ref""",
            (round_id, target["provider"], target["object_kind"], target["object_ref"]),
        )

    def usage_event_public(self, row: dict) -> dict:
        result = {
            key: row.get(key)
            for key in (
                "id",
                "user_id",
                "ai_item_id",
                "consumed_points",
                "is_free_trial",
                "external_status",
                "external_record_id",
                "trace_id",
                "created_at",
                "round_id",
            )
        }
        for key in ("id", "user_id", "round_id"):
            if result[key] is not None:
                result[key] = str(result[key])
        created = result["created_at"]
        if created is not None and hasattr(created, "isoformat"):
            result["created_at"] = created.isoformat()
        started = row.get("reporting_at")
        if isinstance(started, str):
            started = datetime.fromisoformat(started)
        if result["external_status"] == "reporting" and started and started < _now() - timedelta(seconds=120):
            result["external_status"] = "unknown"
        return result

    def list_usage_events(self, principal: Principal, limit: int = 50, offset: int = 0, since=None, until=None) -> list[dict]:
        with self._conn() as conn:
            rows = self._usage_rows(conn, principal, limit, offset, since, until)
        return [self.usage_event_public(row) for row in rows]

    def _usage_rows(self, conn, principal, limit, offset, since, until):
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT * FROM product.usage_events
                   WHERE tenant_id = %s
                     AND (%s::date IS NULL OR created_at >= %s::date)
                     AND (%s::date IS NULL OR created_at < %s::date + INTERVAL '1 day')
                   ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s""",
                (principal.tenant_id, since, since, until, until, limit, offset),
            )
            return cur.fetchall()

    def usage_event(self, principal: Principal, event_id: str) -> dict | None:
        if not _as_uuid(event_id):
            return None
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT * FROM product.usage_events
                   WHERE id = %s AND tenant_id = %s""",
                (event_id, principal.tenant_id),
            )
            return cur.fetchone()

    def claim_usage_report(self, principal, event_id) -> str | None:
        claim = str(uuid.uuid4())
        with self._conn() as conn:
            row = conn.execute(
                """UPDATE product.usage_events SET external_status = 'reporting',
                          report_claim = %s, reporting_at = now()
                   WHERE id = %s AND tenant_id = %s AND user_id = %s
                     AND external_status IN ('pending', 'unknown') AND NOT is_free_trial
                   RETURNING id""",
                (claim, event_id, principal.tenant_id, principal.user_id),
            ).fetchone()
        return claim if row else None

    def settle_usage_external(self, principal, event_id, claim, status, record_id=None) -> dict:
        if status not in {"confirmed", "unknown"} or (status == "confirmed" and not record_id):
            raise ValueError("invalid_external_status")
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """UPDATE product.usage_events SET external_status = %s, external_record_id = %s
                   WHERE id = %s AND tenant_id = %s AND user_id = %s
                     AND report_claim = %s AND external_status = 'reporting'
                   RETURNING *""",
                (status, record_id, event_id, principal.tenant_id, principal.user_id, claim),
            )
            row = cur.fetchone()
        if row is None:
            raise KeyError("usage_claim_lost")
        return row
