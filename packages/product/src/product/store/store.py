from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterator
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast

import psycopg
from audit.audits import (
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
    normalize_analysis_mode,
    normalize_idempotency_key,
    normalize_review,
    normalize_role,
    request_fingerprint,
    require_targets,
)
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from product.db import connect
from product.feishu import CardAction, UserTokens
from product.feishu_notifications import optimization_notification_card, task_notification_card
from product.feishu_personal_upload import PersonalDriveClient, UploadRejected, UploadUnknown
from product.feishu_usage import FeishuUsageError, PartnerAiItem, audit_usage_points, remaining_trial_points, split_trial_and_partner
from product.files import (
    FAILED,
    MAX_UPLOAD_BYTES,
    READY,
    STAGING,
    FileVersion,
    ImportedFile,
    ObjectExists,
    ObjectMissing,
    UnsafeSource,
    original_filename,
    safe_content_type,
    sha256_bytes,
    version_object_key,
)
from product.fixtures import (
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
from product.identity import (
    PROVIDER_FEISHU,
    Principal,
    Resource,
    Session,
    can_read,
    identity_key,
    new_token,
    token_hash,
)
from product.logs import emit
from product.rules import FILE_PREPARATION_KINDS, customer_scope, latest_rule_versions, normalize_rule_body, rule_parse_request, rule_parse_view, rule_title
from product.settings import Settings
from product.storage import S3Objects
from product.store.provider_auth import ProviderAuthStore, create_authorization
from product.store.task_view import file_round_audit, has_execution_error, report_eligible, round_read_view, task_round_view
from product.store.upload_preparation import PREPARATION_KEY, normalize_preparation, preparation_inputs, validate_preparation_update
from product.writeback_targets import normalize_external_ref, public_target


def _preset_customers(body: dict) -> set[str]:
    """Use adopted Suite identity; legacy recognition retains preset provenance."""
    from audit.presets import customer_presets

    customers = {preset.customer for preset in customer_presets() if body.get("suite_key") == f"{preset.customer}:{preset.entry_id}"}
    if body.get("rule_profile") == "v4":
        customers.update(body.get("customers") or [])
    return customers


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
    if payload.get("execution_policy"):
        extra["execution_policy"] = payload["execution_policy"]
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
        if payload.get("execution_policy"):
            diagnostic["execution_policy"] = payload["execution_policy"]
    for key in ("failure", "usage"):
        if key in result:
            diagnostic[key] = result[key]
    if result.get("usage_source") in {"provider", "controlled", "unknown"}:
        diagnostic["usage_source"] = result["usage_source"]
    return diagnostic


class AuthStore:
    def __init__(
        self,
        settings: Settings,
        objects=None,
        role: str = "api",
    ) -> None:
        self.settings = settings
        self.objects = objects or S3Objects(settings)
        self.role = role

    def _conn(self) -> psycopg.Connection:
        return connect(self.settings, self.role)

    def model_user_for_job(self, claimed) -> str:
        """Read the accepted initiator, never trust a caller-supplied user/key."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COALESCE(r.created_by::text, j.payload->>'initiating_user_id', '')
                   FROM engine.jobs j LEFT JOIN product.audit_rounds r
                     ON r.id=j.audit_round_id AND r.tenant_id=j.tenant_id
                   WHERE j.id=%s AND j.tenant_id=%s AND j.attempt=%s
                     AND j.status='leased' AND j.lease_until > now()""",
                (claimed.id, claimed.tenant_id, claimed.attempt),
            ).fetchone()
        return str(row[0]) if row else ""

    def personal_model_config(self, principal: Principal) -> dict:
        from product.store.model_credentials import PersonalModelStore

        return PersonalModelStore(self).config(principal)

    def put_personal_model_config(self, principal: Principal, api_key) -> dict:
        from product.store.model_credentials import PersonalModelStore

        return PersonalModelStore(self).put(principal, api_key)

    def delete_personal_model_config(self, principal: Principal) -> dict:
        from product.store.model_credentials import PersonalModelStore

        return PersonalModelStore(self).delete(principal)

    def personal_model_access(self, tenant_id: str, initiating_user_id: str):
        from product.store.model_credentials import PersonalModelStore

        return PersonalModelStore(self).access(tenant_id, initiating_user_id)

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

    def save_app_ticket(self, app_id: str, ticket: str, *, issued_at: Decimal | None = None) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                """
                INSERT INTO product.app_tickets (provider, app_id, ticket, provider_event_ts)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (provider, app_id) DO UPDATE
                  SET ticket = EXCLUDED.ticket, received_at = clock_timestamp(),
                      provider_event_ts = EXCLUDED.provider_event_ts
                WHERE app_tickets.provider_event_ts IS NULL
                   OR EXCLUDED.provider_event_ts > app_tickets.provider_event_ts
                RETURNING 1
                """,
                (PROVIDER_FEISHU, app_id, ticket, issued_at),
            ).fetchone()
        return row is not None

    def app_ticket_status(self, app_id: str) -> dict[str, str]:
        with self._conn() as conn:
            row = conn.execute("SELECT received_at, provider_event_ts FROM product.app_tickets WHERE provider = %s AND app_id = %s", (PROVIDER_FEISHU, app_id)).fetchone()
        return {
            "app_ticket": "received" if row else "missing",
            "app_ticket_received_at": row[0].isoformat() if row else "",
            "app_ticket_event_timestamp": str(row[1]) if row and row[1] is not None else "",
        }

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
                    from product.rules import install_customer_presets

                    install_customer_presets(conn, str(tenant_id))
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
                from product.rules import install_customer_presets

                install_customer_presets(conn, str(tenant_id))
                return Principal(str(user_id), str(tenant_id), provider, app_id, tenant_key, subject, "member", display_name)

    def create_session(self, principal: Principal, tokens: UserTokens | None = None, ttl_seconds: int = 43200) -> str:
        raw = new_token()
        now = _now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        if tokens is not None:
            bound = tokens.refresh_expires_at if tokens.refresh_token else tokens.access_expires_at
            if bound < expires_at:
                expires_at = bound
        with self._conn() as conn, conn.transaction():
            authorization_id = create_authorization(conn, principal, tokens)
            conn.execute(
                """
                INSERT INTO product.sessions (
                    token_hash, user_id, tenant_id, expires_at, provider_authorization_id
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    token_hash(raw),
                    principal.user_id,
                    principal.tenant_id,
                    expires_at,
                    authorization_id,
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
                SELECT s.user_id, s.tenant_id, s.expires_at, a.access_token, a.refresh_token,
                       a.access_expires_at, a.refresh_expires_at, s.provider_authorization_id,
                       i.provider, i.app_id, i.tenant_key, i.subject, m.role, i.display_name
                FROM product.sessions s
                JOIN product.tenants t ON t.id = s.tenant_id
                JOIN product.provider_authorizations a ON a.id=s.provider_authorization_id
                    AND a.user_id=s.user_id AND a.tenant_id=s.tenant_id
                JOIN product.identities i ON i.user_id = s.user_id
                    AND i.provider = t.provider AND i.tenant_key = t.external_key
                    AND i.provider=a.provider AND i.app_id=a.app_id AND i.subject=a.subject
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
            str(session_row["provider_authorization_id"]),
        )

    def principal_from_token(self, raw: str) -> Principal | None:
        session = self.load_session(raw)
        return session.principal if session else None

    def fresh_provider_session(self, session: Session, refresh) -> Session:
        return ProviderAuthStore(self).fresh(session, refresh)

    def revoke_session(self, raw: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE product.sessions SET revoked_at = now() WHERE token_hash = %s AND revoked_at IS NULL",
                (token_hash(raw),),
            )

    def resource(self, resource_id: str) -> Resource | None:
        if not _as_uuid(resource_id):
            return None
        with self._conn() as conn:
            return self._resource(conn, resource_id)

    @staticmethod
    def _resource(conn, resource_id: str) -> Resource | None:
        parsed = _as_uuid(resource_id)
        if not parsed:
            return None
        row = conn.execute(
            """SELECT r.id, r.tenant_id, r.kind, EXISTS (
                    SELECT 1 FROM product.audit_rounds a JOIN product.audit_drafts d ON d.id = a.source_draft_id
                    WHERE a.id = r.id AND d.task_fingerprint IS NOT NULL)
                    OR EXISTS (SELECT 1 FROM product.deep_rule_changes c WHERE c.analysis_round_id = r.id)
                FROM product.resources r WHERE r.id = %s""",
            (parsed,),
        ).fetchone()
        if not row:
            return None
        return Resource(str(row[0]), str(row[1]), row[2], row[3])

    def grants(self, resource_id: str, user_id: str) -> set[str]:
        with self._conn() as conn:
            return self._grants(conn, resource_id, user_id)

    @staticmethod
    def _grants(conn, resource_id: str, user_id: str) -> set[str]:
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
                schema_row = conn.execute("SELECT to_regclass('product.suite_revisions')").fetchone()
                core_schema = schema_row is not None and schema_row[0] is not None
                for rule_id, tenant_id, name in ((RULE_A, TENANT_A, "alice"), (RULE_B, TENANT_B, "carol")):
                    if conn.execute("SELECT 1 FROM product.rule_versions WHERE id=%s", (rule_id,)).fetchone():
                        continue
                    body = dict(PRESENCE_RULE_BODY)
                    if core_schema:
                        from product.store.custom_suites import publish_custom_rule_suite
                        from product.store.suite_releases import _lock, _member

                        principal = self.principal_for_fixture(name)
                        _member(conn, principal)
                        _lock(conn, tenant_id)
                        body = publish_custom_rule_suite(
                            self,
                            conn,
                            principal,
                            rule_id=rule_id,
                            title="资料就绪",
                            body={
                                "kind": "engine",
                                "type": "资料就绪",
                                "customers": [],
                                "models": [],
                                "parts": [],
                                "rules": [{"id": "presence", "category": "资料", "priority": "P0", "rule_to_check": "原件资料已经就绪", "type_of_rule": "text"}],
                            },
                            previous=None,
                        )
                    conn.execute(
                        "INSERT INTO product.rule_versions (id,tenant_id,title,body) VALUES (%s,%s,%s,%s)",
                        (rule_id, tenant_id, "资料就绪", Jsonb(body)),
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
        request_id: str | None = None,
        expected_sha256: str | None = None,
        feishu_folder_token: str | None = None,
    ) -> FileVersion:
        name = original_filename(filename)
        declared = int(declared_size)
        if declared < 0 or declared > MAX_UPLOAD_BYTES:
            raise ValueError("invalid_size")
        ctype = safe_content_type(content_type)
        if feishu_folder_token is not None and (not request_id or not isinstance(feishu_folder_token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", feishu_folder_token) or declared == 0):
            raise ValueError("invalid_drive_upload")
        if request_id is not None:
            return self._create_requested_upload(principal, name, ctype, declared, file_id, request_id, expected_sha256, feishu_folder_token=feishu_folder_token)
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

    def _create_requested_upload(self, principal, name, ctype, declared, file_id, request_id, expected_sha256, source=None, feishu_folder_token=None) -> FileVersion:
        version_id = _as_uuid(request_id)
        fid = _as_uuid(file_id) if file_id else None
        valid_digest = isinstance(expected_sha256, str) and len(expected_sha256) == 64 and all(char in "0123456789abcdef" for char in expected_sha256)
        if not version_id or file_id and not fid or not source and not valid_digest:
            raise ValueError("invalid_upload_request")
        inputs = {"user_id": principal.user_id, "file_id": fid, "original_filename": name, "content_type": ctype, "byte_size": declared, "sha256": expected_sha256}
        if source:
            inputs["source"] = source
        if feishu_folder_token:
            inputs["feishu_folder_token"] = feishu_folder_token
        with self._conn() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("upload:" + version_id,))
            row = conn.execute(
                """SELECT id, file_id, tenant_id, original_filename, content_type, byte_size, sha256, object_key, status, upload_request
                FROM product.file_versions WHERE id = %s""",
                (version_id,),
            ).fetchone()
            if row:
                if str(row[2]) != principal.tenant_id or not row[9] or row[9].get("user_id") != principal.user_id:
                    raise KeyError("missing")
                resource = self.resource(str(row[1]))
                if resource is None or not can_read(principal, resource, self.grants(str(row[1]), principal.user_id)):
                    raise KeyError("missing")
                fixed_inputs = {key: value for key, value in row[9].items() if key != "cloud_upload"}
                if source and row[9].get("source") != source or not source and fixed_inputs != inputs:
                    raise ValueError("upload_request_conflict")
                return self._version_from_row(row)
            if fid:
                resource = self.resource(fid)
                if resource is None or resource.kind != "file" or not can_read(principal, resource, self.grants(fid, principal.user_id)):
                    raise KeyError("missing")
            else:
                fid = str(uuid.uuid4())
                conn.execute("INSERT INTO product.resources (id, tenant_id, kind) VALUES (%s, %s, 'file')", (fid, principal.tenant_id))
                conn.execute("INSERT INTO product.files (id, tenant_id, created_by, original_filename) VALUES (%s, %s, %s, %s)", (fid, principal.tenant_id, principal.user_id, name))
                conn.execute("INSERT INTO product.resource_grants (resource_id, user_id, capability) VALUES (%s, %s, 'read')", (fid, principal.user_id))
            key = version_object_key(principal.tenant_id, fid, version_id)
            conn.execute(
                """INSERT INTO product.file_versions (id, file_id, tenant_id, original_filename, content_type, byte_size, object_key, status, upload_request)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (version_id, fid, principal.tenant_id, name, ctype, declared, key, STAGING, Jsonb(inputs)),
            )
            return FileVersion(version_id, fid, principal.tenant_id, name, ctype, declared, key, STAGING)

    def receive_drive_upload(self, principal, request_id, file_token, doc_type, load) -> FileVersion:
        def captured():
            imported = load()
            if imported.file_token != file_token or doc_type and imported.doc_type != doc_type:
                raise ValueError("upload_request_conflict")
            origin = {"provider": "feishu", "object_kind": imported.doc_type, "object_ref": file_token}
            return ImportedFile(imported.data, imported.filename, imported.content_type, origin)

        return self._receive_import_upload(principal, request_id, {"provider": "feishu", "file_token": file_token, "doc_type": doc_type}, captured)

    def receive_url_upload(self, principal, request_id, url, load) -> FileVersion:
        def captured():
            data, content_type, name = load()
            return ImportedFile(data, name, content_type)

        return self._receive_import_upload(principal, request_id, {"provider": "url", "url": url}, captured)

    def resume_url_upload(self, principal, request_id, load) -> FileVersion:
        # Receipt ownership is stricter than a grant to read the imported file.
        receipt = self.get_upload_request(principal, request_id)
        with self._conn() as conn:
            row = conn.execute("SELECT upload_request FROM product.file_versions WHERE id = %s", (request_id,)).fetchone()
        source = (row[0] or {}).get("source", {}) if row else {}
        if source.get("provider") != "url":
            raise ValueError("url_import_not_found")
        if receipt["state"] == "failed":
            raise ValueError("upload_not_resumable")
        url = source["url"]
        return self.receive_url_upload(principal, request_id, url, lambda: load(url))

    def _receive_import_upload(self, principal, request_id, source, load) -> FileVersion:
        version_id = _as_uuid(request_id) if isinstance(request_id, str) else None
        if not version_id:
            raise ValueError("invalid_upload_request")
        version = self._create_requested_upload(principal, "文件导入中", "application/octet-stream", 0, None, version_id, None, source)
        # This lock spans provider I/O, but no database transaction does. A second
        # handler observes the original request instead of exporting again.
        with self._conn() as guard:
            key = ("drive-import:" if source["provider"] == "feishu" else "url-import:") + version_id
            lock_row = guard.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()
            locked = bool(lock_row and lock_row[0])
            if not locked:
                return self.get_version(version.file_id, version_id) or version
            try:
                version = self.get_version(version.file_id, version_id) or version
                if version.status != STAGING:
                    return version
                inputs = self._upload_request(version)
                if inputs and inputs.get("sha256"):
                    try:
                        return self.finalize_version(version)
                    except ValueError as exc:
                        if str(exc) != "missing_object":
                            raise
                try:
                    imported = load()
                except UnsafeSource:
                    # A rejected URL/redirect/body is a definite source failure,
                    # not an unfinished transfer. The original lock session must
                    # still own this transition; a late handler cannot reconnect.
                    guard.execute("UPDATE product.file_versions SET status = %s WHERE id = %s AND status = %s", (FAILED, version_id, STAGING))
                    raise
                if len(imported.data) > MAX_UPLOAD_BYTES:
                    raise ValueError("too_large")
                captured = {
                    "original_filename": original_filename(imported.filename),
                    "content_type": safe_content_type(imported.content_type),
                    "byte_size": len(imported.data),
                    "sha256": sha256_bytes(imported.data),
                }
                conflict = False
                # A dead lock session cannot continue this transaction on a new
                # connection. Re-read under a row lock; never trust pre-I/O input.
                with guard.transaction():
                    row = guard.execute("SELECT upload_request, status FROM product.file_versions WHERE id = %s FOR UPDATE", (version_id,)).fetchone()
                    if not row or row[0].get("source") != source:
                        raise ValueError("upload_request_conflict")
                    if row[1] != STAGING:
                        return self.get_version(version.file_id, version_id) or version
                    inputs = row[0]
                    conflict = bool(inputs.get("sha256") and any(inputs.get(key) != value for key, value in captured.items()))
                    if conflict:
                        guard.execute("UPDATE product.file_versions SET status = %s WHERE id = %s AND status = %s", (FAILED, version_id, STAGING))
                    else:
                        updated = guard.execute(
                            """UPDATE product.file_versions SET original_filename = %s, content_type = %s, byte_size = %s, upload_request = %s
                           WHERE id = %s AND status = %s AND upload_request = %s""",
                            (captured["original_filename"], captured["content_type"], captured["byte_size"], Jsonb({**inputs, **captured}), version_id, STAGING, Jsonb(inputs)),
                        )
                        if updated.rowcount != 1:
                            raise ValueError("upload_request_conflict")
                        guard.execute("UPDATE product.files SET original_filename = %s WHERE id = %s", (captured["original_filename"], version.file_id))
                        if imported.origin:
                            guard.execute(
                                """INSERT INTO product.file_version_sources (file_version_id, provider, object_kind, object_ref)
                               VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                                (version_id, imported.origin["provider"], imported.origin["object_kind"], imported.origin["object_ref"]),
                            )
                if conflict:
                    raise ValueError("upload_content_conflict")
                current = self.get_version(version.file_id, version_id)
                if current is None:
                    raise KeyError("missing")
                return self.put_blob(current, imported.data)
            finally:
                if not guard.closed:
                    guard.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    def _upload_request(self, version: FileVersion) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT upload_request FROM product.file_versions WHERE id = %s AND file_id = %s AND tenant_id = %s", (version.id, version.file_id, version.tenant_id)).fetchone()
            return row[0] if row else None

    def upload_to_personal_drive(self, principal: Principal, request_id: str, drive: PersonalDriveClient) -> dict:
        receipt = self.get_upload_request(principal, request_id)
        if not receipt["resume_allowed"] or not receipt["file"] or receipt["file"]["status"] != READY:
            return receipt
        version = self.get_version(receipt["file"]["file_id"], receipt["file"]["id"])
        if version is None:
            raise KeyError("missing")
        with self._conn() as guard:
            key = "personal-drive:" + version.id
            locked = guard.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()
            if not locked or not locked[0]:
                return self.get_upload_request(principal, request_id)
            try:
                inputs = self._upload_request(version) or {}
                folder = inputs.get("feishu_folder_token")
                progress = inputs.get("cloud_upload", {})
                if not folder or progress.get("phase") in {"finishing", "ready"}:
                    return self.get_upload_request(principal, request_id)

                def save(progress):
                    nonlocal inputs
                    updated = {**inputs, "cloud_upload": progress}
                    # Use the lock-owning session after every provider response.
                    # A dead session cannot publish late state on a new connection.
                    with guard.transaction():
                        changed = guard.execute(
                            "UPDATE product.file_versions SET upload_request = %s WHERE id = %s AND upload_request = %s",
                            (Jsonb(updated), version.id, Jsonb(inputs)),
                        )
                        if changed.rowcount != 1:
                            raise ValueError("upload_request_conflict")
                    inputs = updated

                try:
                    body = self.version_bytes(version)
                    plan = progress.get("plan")
                    if not plan:
                        plan = drive.prepare(version.original_filename, len(body), folder)
                        progress = {"phase": "parts", "plan": plan, "next_part": 0}
                        save(progress)
                    for seq in range(progress.get("next_part", 0), plan["block_num"]):
                        drive.part(plan, seq, body[seq * plan["block_size"] : (seq + 1) * plan["block_size"]])
                        progress = {"phase": "parts", "plan": plan, "next_part": seq + 1}
                        save(progress)
                    save({"phase": "finishing", "plan": plan})
                    token = drive.finish(plan)
                    with guard.transaction():
                        changed = guard.execute(
                            "UPDATE product.file_versions SET upload_request = %s WHERE id = %s AND upload_request = %s",
                            (Jsonb({**inputs, "cloud_upload": {"phase": "ready", "file_token": token}}), version.id, Jsonb(inputs)),
                        )
                        if changed.rowcount != 1:
                            raise ValueError("upload_request_conflict")
                        guard.execute(
                            "INSERT INTO product.file_version_sources (file_version_id, provider, object_kind, object_ref) VALUES (%s, 'feishu', 'file', %s)",
                            (version.id, token),
                        )
                except UploadRejected:
                    save({**progress, "reason": "飞书未接收本次上传。请核对目录权限和剩余空间，再继续原请求。"})
                except UploadUnknown:
                    # A published file may exist after finish. Keep the durable
                    # intent for read-back; never repeat that non-idempotent call.
                    if inputs.get("cloud_upload", {}).get("phase") != "finishing":
                        save({**progress, "reason": "飞书上传尚未完成，可以继续原请求。"})
            finally:
                if not guard.closed:
                    guard.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
        return self.get_upload_request(principal, request_id)

    def get_upload_request(self, principal: Principal, request_id: str) -> dict:
        parsed = _as_uuid(request_id)
        if not parsed:
            raise ValueError("invalid_upload_request")
        receipt: dict[str, Any] = {"request_id": parsed, "state": "unknown", "file": None, "reason": "尚未读到原上传请求，不能确认文件未接收。", "retry_allowed": False, "resume_allowed": False}
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id, file_id, tenant_id, original_filename, content_type, byte_size, sha256, object_key, status, upload_request
                FROM product.file_versions WHERE id = %s""",
                (parsed,),
            ).fetchone()
        if not row:
            return receipt
        version = self._version_from_row(row)
        resource = self.resource(version.file_id)
        if (
            version.tenant_id != principal.tenant_id
            or not row[9]
            or row[9].get("user_id") != principal.user_id
            or resource is None
            or not can_read(principal, resource, self.grants(version.file_id, principal.user_id))
        ):
            raise KeyError("missing")
        state = {STAGING: "uploading", READY: "ready", FAILED: "failed"}[version.status]
        reason = None if version.status == READY else "上传尚未完成，可沿用原请求继续传输。" if version.status == STAGING else "原上传已失败，不能继续写入该版本。"
        receipt.update(state=state, file=version.public(), resume_allowed=version.status == STAGING, reason=reason)
        if row[9].get("feishu_folder_token") and version.status == READY:
            cloud = row[9].get("cloud_upload", {})
            source = self.file_source(version.id)
            if cloud.get("phase") == "ready" and source and source["provider"] == "feishu" and source["object_kind"] == "file" and source["object_ref"] == cloud.get("file_token"):
                return receipt
            finishing = cloud.get("phase") in {"finishing", "ready"}
            receipt.update(
                state="unknown" if finishing else "uploading",
                resume_allowed=not finishing,
                reason="尚未确认飞书文件的创建结果，请核对原请求，不要重新上传。" if finishing else cloud.get("reason") or "文件已接收，正在存入所选飞书目录。",
            )
        if row[9].get("source") and not row[9].get("sha256"):
            receipt["file"] = None
        return receipt

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

    def readable_version(self, principal: Principal, version_id: str) -> FileVersion:
        vid = _as_uuid(version_id)
        if not vid:
            raise KeyError("missing")
        with self._conn() as conn:
            prepared = self._prepared_items(conn, principal, [{"file_version_id": vid, "role": "reference"}])
            row = conn.execute(
                "SELECT id, file_id, tenant_id, original_filename, content_type, byte_size, sha256, object_key, status FROM product.file_versions WHERE file_id = %s AND id = %s",
                (prepared[0]["file_id"], vid),
            ).fetchone()
        if row is None:
            raise KeyError("missing")
        return self._version_from_row(row)

    def read_file_version(self, principal: Principal, file_id: str, version_id: str, *, round_id: str | None = None) -> FileVersion:
        fid, vid = _as_uuid(file_id), _as_uuid(version_id)
        rid = _as_uuid(round_id) if round_id else fid
        if not fid or not vid or not rid:
            raise KeyError("missing")
        with self._conn() as conn:
            row = conn.execute(
                """SELECT f.id, f.file_id, f.tenant_id, f.original_filename, f.content_type, f.byte_size, f.sha256, f.object_key, f.status,
                    r.id, r.tenant_id, r.kind,
                    EXISTS (SELECT 1 FROM product.audit_rounds a JOIN product.audit_drafts d ON d.id = a.source_draft_id
                        WHERE a.id = r.id AND d.task_fingerprint IS NOT NULL)
                    OR EXISTS (SELECT 1 FROM product.deep_rule_changes c WHERE c.analysis_round_id=r.id),
                    ARRAY(SELECT g.capability FROM product.resource_grants g WHERE g.resource_id = r.id AND g.user_id = %s)
                FROM product.file_versions f JOIN product.resources r ON r.id = %s
                WHERE f.file_id = %s AND f.id = %s AND f.tenant_id = %s
                  AND (%s OR EXISTS (SELECT 1 FROM product.round_inputs i WHERE i.round_id = r.id AND i.file_version_id = f.id))""",
                (principal.user_id, rid, fid, vid, principal.tenant_id, round_id is None),
            ).fetchone()
        if row is None or row[11] != ("audit_round" if round_id else "file") or not can_read(principal, Resource(str(row[9]), str(row[10]), row[11], row[12]), set(row[13])):
            raise KeyError("missing")
        return self._version_from_row(row[:9])

    def put_blob(self, version: FileVersion, body: bytes) -> FileVersion:
        if len(body) > MAX_UPLOAD_BYTES:
            raise ValueError("invalid_size")
        current = self.get_version(version.file_id, version.id)
        if current is None:
            raise ObjectExists()
        inputs = self._upload_request(current)
        if inputs and (len(body) != inputs["byte_size"] or sha256_bytes(body) != inputs["sha256"]):
            raise ValueError("upload_content_conflict")
        if current.status != STAGING:
            if inputs and current.status == READY and current.sha256 == inputs["sha256"]:
                return current
            raise ObjectExists()
        try:
            self.objects.put_exclusive(current.object_key, body, current.content_type)
        except ObjectExists:
            if not inputs:
                raise
            present = self.objects.get(current.object_key)
            if len(present) != inputs["byte_size"] or sha256_bytes(present) != inputs["sha256"]:
                raise ValueError("upload_content_conflict")
        if inputs:
            return self.finalize_version(current)
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
        inputs = self._upload_request(version)
        if inputs:
            if expected_sha256 and expected_sha256 != inputs["sha256"]:
                raise ValueError("sha256_conflict")
            expected_sha256 = inputs["sha256"]
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
        if expected_sha256 and expected_sha256 != digest or inputs and len(body) != inputs["byte_size"]:
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

    def accept_file_validation(self, principal, request_id, source_file_id, source_file_version_id, scope=None) -> dict:
        return self._accept_file_job(principal, request_id, source_file_id, source_file_version_id, "file_validation", validation_scope=scope)

    def _accept_file_job(self, principal, request_id, source_file_id, source_file_version_id, kind, document_type="", *, validation_scope=None) -> dict:
        job_id, payload = rule_parse_request(request_id, source_file_id, source_file_version_id, document_type)
        payload["initiating_user_id"] = principal.user_id
        if validation_scope is not None:
            if kind != "file_validation" or validation_scope != {"customer_preference": "UAES", "preset_key": "U13"}:
                raise ValueError("invalid_validation_scope")
            payload["scope"] = dict(validation_scope)
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
            return self._file_jobs(conn, principal, [parsed], kind)[parsed]

    def _file_jobs(self, conn, principal: Principal, job_ids: list[str], kind: str) -> dict:
        if any(not _as_uuid(job_id) for job_id in job_ids):
            raise KeyError("missing")
        if not job_ids:
            return {}
        rows = conn.execute(
            """SELECT id, payload, status, result, lease_until < now() FROM engine.jobs
            WHERE id = ANY(%s::uuid[]) AND tenant_id = %s AND kind = %s""",
            (job_ids, principal.tenant_id, kind),
        ).fetchall()
        if len(rows) != len(set(job_ids)):
            raise KeyError("missing")
        for task_id in {row[1]["task_id"] for row in rows if row[1].get("task_id")}:
            self._recognition_owner(conn, principal, task_id)
        sources = {row[1]["source_file_version_id"] for row in rows}
        self._prepared_items(conn, principal, [{"file_version_id": version_id, "role": "reference"} for version_id in sources])
        return {str(row[0]): rule_parse_view(str(row[0]), row[1], row[2], row[3], bool(row[4])) for row in rows}

    def _reaudit_source(self, conn, principal: Principal, predecessor_id: str) -> dict:
        resource = self._resource(conn, predecessor_id)
        if not resource or resource.kind != "audit_round" or not can_read(principal, resource, self._grants(conn, predecessor_id, principal.user_id)):
            raise KeyError("missing")
        row = conn.execute(
            """SELECT r.execution, r.analysis_mode, d.customer_preference,
            CASE WHEN d.task_fingerprint IS NOT NULL THEN d.id END
            FROM product.audit_rounds r LEFT JOIN product.audit_drafts d ON d.id = r.source_draft_id WHERE r.id = %s""",
            (predecessor_id,),
        ).fetchone()
        if not row or row[0] not in {"completed", "failed"}:
            raise ValueError("reaudit_source_not_finished")
        if row[3]:
            self._recognition_owner(conn, principal, str(row[3]))
        materials = conn.execute(
            """SELECT i.file_version_id, i.role, i.rule_version_id, i.rule_title, i.rule_body, j.result
            FROM product.round_inputs i JOIN product.audit_rounds r ON r.id = i.round_id
            LEFT JOIN product.draft_items di ON di.draft_id = r.source_draft_id AND di.file_version_id = i.file_version_id
            LEFT JOIN engine.jobs j ON j.id = di.analysis_job_id
            WHERE i.round_id = %s ORDER BY di.position NULLS LAST, i.file_version_id""",
            (predecessor_id,),
        ).fetchall()
        return {
            "analysis_mode": row[1],
            "customer_preference": row[2],
            "task_id": str(row[3]) if row[3] else None,
            "inputs": {
                str(item[0]): {
                    "role": item[1],
                    "rule_id": str(item[2]) if item[2] else None,
                    "rule": {"id": str(item[2]), "title": item[3], "body": item[4]} if item[2] else None,
                    "recognition": item[5] or {},
                }
                for item in materials
            },
        }

    @staticmethod
    def _reaudit_rule_options(rule: dict | None, all_rules: list[dict]) -> list[dict]:
        if not rule:
            return []
        options = [{"id": rule["id"], "title": rule["title"] + " · 原审核版本"}]
        by_id = {item["id"]: item for item in all_rules}
        for candidate in latest_rule_versions(all_rules):
            ancestor, seen = candidate["id"], set()
            while ancestor in by_id and ancestor not in seen:
                if ancestor == rule["id"]:
                    if candidate["id"] != rule["id"]:
                        options.append({"id": candidate["id"], "title": candidate["title"] + " · 较新版本"})
                    break
                seen.add(ancestor)
                ancestor = by_id[ancestor].get("predecessor_id")
        return options

    def reaudit_inputs(self, principal: Principal, predecessor_id: str) -> dict:
        """Prepare from frozen inputs; reading creates no Task, audit, usage or review."""
        with self._conn() as conn:
            source = self._reaudit_source(conn, principal, predecessor_id)
            all_rules = self.list_rules(principal.tenant_id)
            files = []
            for vid, material in source["inputs"].items():
                prepared = self._prepared_items(conn, principal, [{"file_version_id": vid, "role": "reference"}])[0]
                version = self.get_version(prepared["file_id"], vid)
                if version is None:
                    raise KeyError("missing")
                checked = self.latest_file_validation(principal, version.file_id, vid)
                rule = material["rule"]
                options = self._reaudit_rule_options(rule, all_rules)
                files.append(
                    {
                        "file": version.public(),
                        "selection": "selected",
                        "removal_reason": None,
                        "validation": checked["result"] if checked and checked["status"] == "succeeded" else {"state": "pending", "reasons": ["文件待检查。"]},
                        **({"validation_id": checked["id"]} if checked else {}),
                        "match": None,
                        **({"preferred_rule_version_id": rule["id"], "rule_options": options} if rule else {"reference_only": True}),
                    }
                )
            return {"predecessor_id": predecessor_id, "analysis_mode": source["analysis_mode"], "customer_preference": source["customer_preference"], "files": files}

    def create_audit_task(self, principal: Principal, body: dict, *, usage_policy=None) -> dict:
        request_id = _as_uuid(str(body.get("request_id") or ""))
        revising = "task_id" in body or "expected_revision" in body
        promoting = "source_draft_id" in body or "expected_draft_revision" in body
        task_id = _as_uuid(str(body.get("task_id") or "")) if revising else _as_uuid(str(body.get("source_draft_id") or "")) if promoting else request_id
        expected = body.get("expected_revision")
        preparation_revision = body.get("expected_draft_revision")
        if promoting and (revising or type(preparation_revision) is not int or preparation_revision < 0):
            raise ValueError("invalid_draft_revision")
        files = body.get("files")
        customer = body.get("customer_preference")
        predecessor = _as_uuid(str(body.get("predecessor_id") or ""))
        if body.get("predecessor_id") and not predecessor:
            raise ValueError("invalid_task_request")
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
            if (
                not vid
                or not isinstance(selection, str)
                or selection not in {"selected", "unselected", "removed"}
                or (not validation_id and (selection != "removed" or raw.get("validation_id") is not None))
            ):
                raise ValueError("invalid_task_request")
            if selection == "removed":
                if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
                    raise ValueError("invalid_removal_reason")
                reason = reason.strip()
            elif reason is not None:
                raise ValueError("invalid_removal_reason")
            normalized.append({"file_version_id": vid, "validation_id": validation_id, "selection": selection, "removal_reason": reason})
            if raw.get("replacement_of_file_version_id"):
                replacement = _as_uuid(str(raw["replacement_of_file_version_id"]))
                if not replacement or replacement == vid:
                    raise ValueError("invalid_task_request")
                normalized[-1]["replacement_of_file_version_id"] = replacement
            if raw.get("preferred_rule_version_id"):
                preferred = _as_uuid(str(raw["preferred_rule_version_id"]))
                if not preferred:
                    raise ValueError("invalid_task_request")
                normalized[-1]["preferred_rule_version_id"] = preferred
            if raw.get("reference_only") is not None and type(raw["reference_only"]) is not bool:
                raise ValueError("invalid_task_request")
            if raw.get("reference_only"):
                normalized[-1]["reference_only"] = True
        if not any(row["selection"] == "selected" for row in normalized):
            raise ValueError("selected_file_required")
        inputs = {"files": normalized, "analysis_mode": mode, "customer_preference": customer}
        if predecessor:
            inputs["predecessor_id"] = predecessor
        if revising:
            inputs.update(task_id=task_id, expected_revision=expected)
        if promoting:
            inputs.update(source_draft_id=task_id, expected_draft_revision=preparation_revision)
        fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        with self._conn() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("recognition-request:" + request_id,))
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("recognition:" + task_id,))
            if revising:
                draft = self._recognition_owner(conn, principal, task_id, lock=True)
                if (str(draft["predecessor_id"]) if draft["predecessor_id"] else None) != predecessor:
                    raise ValueError("task_input_locked")
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
            if existing and existing[0] is not None:
                self._recognition_owner(conn, principal, task_id)
                if existing[0] != fingerprint:
                    raise ValueError("idempotency_conflict")
                if promoting:
                    receipt = conn.execute("SELECT kind, payload FROM engine.jobs WHERE id = %s AND tenant_id = %s", (request_id, principal.tenant_id)).fetchone()
                    if not receipt or receipt[0] != "file_analysis" or receipt[1].get("task_id") != task_id or (receipt[1].get("recognition_request") or {}).get("fingerprint") != fingerprint:
                        raise ValueError("idempotency_conflict")
                return self._recognition_public(conn, principal, task_id)
            if promoting:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("SELECT * FROM product.audit_drafts WHERE id = %s FOR UPDATE", (task_id,))
                    preparation_draft = cur.fetchone()
                if not preparation_draft or str(preparation_draft["tenant_id"]) != principal.tenant_id or str(preparation_draft["created_by"]) != principal.user_id:
                    raise KeyError("missing")
                self._require_preparation_owner(conn, principal, task_id)
                preparation = (preparation_draft["case_context"] or {}).get(PREPARATION_KEY)
                if preparation_draft["task_fingerprint"] is not None:
                    raise ValueError("idempotency_conflict")
                if not preparation or preparation_draft["status"] != DRAFT_OPEN:
                    raise ValueError("task_input_locked")
                if preparation_draft["input_revision"] != preparation_revision:
                    raise ValueError("draft_revision_conflict")
                submitted = {key: value for key, value in inputs.items() if key not in {"source_draft_id", "expected_draft_revision"}}
                if preparation_inputs(preparation) != submitted:
                    raise ValueError("upload_preparation_input_conflict")
                if conn.execute("SELECT 1 FROM product.audit_rounds WHERE source_draft_id = %s", (task_id,)).fetchone():
                    raise ValueError("draft_already_accepted")
                if request_id != task_id and conn.execute("SELECT 1 FROM product.resources WHERE id = %s", (request_id,)).fetchone():
                    raise ValueError("idempotency_conflict")
            elif conn.execute("SELECT 1 FROM product.resources WHERE id = %s", (task_id,)).fetchone():
                raise KeyError("missing")
            # Initial creation and rematching share the endpoint's request identity.
            prior_job = conn.execute("SELECT tenant_id FROM engine.jobs WHERE id = %s", (request_id,)).fetchone()
            if prior_job:
                if str(prior_job[0]) != principal.tenant_id:
                    raise KeyError("missing")
                raise ValueError("idempotency_conflict")
            self._check_usage_policy(conn, principal, usage_policy)
            if predecessor:
                self._reaudit_source(conn, principal, predecessor)
            day = cast(tuple, conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone())[0]
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"task-number:{principal.tenant_id}:{day}",))
            sequence = cast(tuple, conn.execute("SELECT COALESCE(MAX(task_sequence), 0) + 1 FROM product.audit_drafts WHERE tenant_id = %s AND task_day = %s", (principal.tenant_id, day)).fetchone())[
                0
            ]
            if promoting:
                conn.execute(
                    """UPDATE product.audit_drafts SET analysis_mode = %s, task_fingerprint = %s, task_day = %s,
                    task_sequence = %s, customer_preference = %s, predecessor_id = %s, input_revision = 0,
                    recognition_request_id = %s, updated_at = now() WHERE id = %s""",
                    (mode, fingerprint, day, sequence, customer, predecessor, request_id, task_id),
                )
            else:
                conn.execute("INSERT INTO product.resources (id, tenant_id, kind) VALUES (%s, %s, 'audit_draft')", (task_id, principal.tenant_id))
                conn.execute(
                    """INSERT INTO product.audit_drafts (id, tenant_id, created_by, status, analysis_mode, task_fingerprint, task_day, task_sequence, customer_preference, predecessor_id)
                    VALUES (%s, %s, %s, 'open', %s, %s, %s, %s, %s, %s)""",
                    (task_id, principal.tenant_id, principal.user_id, mode, fingerprint, day, sequence, customer, predecessor),
                )
                conn.execute(
                    "INSERT INTO product.resource_grants (resource_id, user_id, capability) VALUES (%s, %s, 'read'), (%s, %s, 'review')", (task_id, principal.user_id, task_id, principal.user_id)
                )
            self._recognition_inputs(conn, principal, task_id, normalized, customer, 0, request_id if promoting else None, fingerprint if promoting else None)
            return self._recognition_public(conn, principal, task_id)

    def _recognition_inputs(self, conn, principal, task_id, files, customer, revision, request_id=None, fingerprint=None) -> None:
        prepared = self._prepared_items(conn, principal, [{"file_version_id": row["file_version_id"], "role": "reference"} for row in files])
        validation_scopes = {}
        for row, source in zip(files, prepared):
            if row["selection"] == "removed" and row["validation_id"] is None:
                continue
            validation = self.get_file_validation(principal, row["validation_id"])
            if validation["source_file_version_id"] != row["file_version_id"] or validation["source_file_id"] != source["file_id"]:
                raise ValueError("precheck_required")
            if row["selection"] != "removed" and (validation["status"] != "succeeded" or (validation.get("result") or {}).get("state") != "valid"):
                raise ValueError("precheck_required")
            validation_scopes[row["file_version_id"]] = validation.get("scope")
            if row["selection"] != "removed" and validation.get("scope") and (customer != "UAES" or validation["scope"] != {"customer_preference": "UAES", "preset_key": "U13"}):
                raise ValueError("validation_scope_mismatch")
        all_rules = self.list_rules(principal.tenant_id)
        candidates = latest_rule_versions(all_rules)
        draft = self._recognition_owner(conn, principal, task_id)
        predecessor = self._reaudit_source(conn, principal, str(draft["predecessor_id"])) if draft["predecessor_id"] else None
        replacements = [row["replacement_of_file_version_id"] for row in files if row["selection"] == "selected" and row.get("replacement_of_file_version_id")]
        if len(replacements) != len(set(replacements)):
            raise ValueError("invalid_replacement_binding")
        for row in files:
            if row.get("replacement_of_file_version_id") and (not predecessor or row["replacement_of_file_version_id"] not in predecessor["inputs"]):
                raise ValueError("invalid_replacement_binding")
        for replaced in replacements:
            if any(row["file_version_id"] == replaced and row["selection"] == "selected" for row in files):
                raise ValueError("invalid_replacement_binding")
        conn.execute("DELETE FROM product.draft_items WHERE draft_id = %s", (task_id,))
        receipt_id = request_id
        retained_replacements = {row["file_version_id"]: row["replacement_of_file_version_id"] for row in files if row["selection"] != "selected" and row.get("replacement_of_file_version_id")}
        for position, (row, source) in enumerate(zip(files, prepared)):
            preferred = row.get("preferred_rule_version_id")
            replacement = row.get("replacement_of_file_version_id")
            inherited = predecessor["inputs"].get(replacement or row["file_version_id"]) if predecessor else None
            reference_only = row.get("reference_only", False)
            v4_reference = bool(customer and any(rule.get("rule_profile") == "v4" and customer.casefold() in {name.casefold() for name in rule.get("customers") or []} for rule in candidates))
            if reference_only and (preferred or not (v4_reference or inherited and inherited["role"] == "reference")):
                raise ValueError("invalid_reference_binding")
            reuse = bool(inherited and (reference_only or preferred and preferred == inherited["rule_id"]))
            if row["selection"] == "selected" and reuse and not reference_only and predecessor is not None and customer != predecessor["customer_preference"]:
                raise ValueError("reaudit_scope_changed")
            chosen = next((rule for rule in all_rules if rule["id"] == preferred), None) if preferred else None
            if row["selection"] == "selected" and preferred and (not chosen or not reuse and not any(rule["id"] == preferred for rule in candidates)):
                raise ValueError("rule_candidate_expired")
            analysis_id = (receipt_id or str(uuid.uuid4())) if row["selection"] == "selected" else None
            if analysis_id:
                payload = {
                    "task_id": task_id,
                    "initiating_user_id": principal.user_id,
                    "task_revision": revision,
                    "source_file_id": source["file_id"],
                    "source_file_version_id": row["file_version_id"],
                    "document_type": "",
                    "candidate_rules": [chosen] if chosen else candidates,
                    "customer_preference": customer,
                    "reference_only": reference_only,
                    "matching_scope": "customer_document_type",
                    "validation_scope": validation_scopes.get(row["file_version_id"]),
                }
                if replacement:
                    payload["replacement_of_file_version_id"] = replacement
                if retained_replacements:
                    # The existing revision-bound job retains inactive input intent
                    # without scheduling recognition for any excluded file.
                    payload["retained_replacements"] = retained_replacements
                    retained_replacements = {}
                if inherited is not None and reuse and (not replacement or reference_only):
                    payload["confirmed_match"] = {
                        "matching_status": "reference" if reference_only else "matched",
                        "matched_rule_id": preferred,
                        "document_type": (inherited["rule"] or {}).get("body", {}).get("type") or inherited["recognition"].get("document_type"),
                        "metadata": inherited["recognition"].get("metadata") or {"customer": None, "model": None, "part": None, "part_number": None},
                        "notices": [],
                    }
                if receipt_id:
                    payload["recognition_request"] = {"request_id": receipt_id, "input_revision": revision, "fingerprint": fingerprint}
                    receipt_id = None
                conn.execute("INSERT INTO engine.jobs (id, tenant_id, kind, payload, status) VALUES (%s, %s, 'file_analysis', %s, 'queued')", (analysis_id, principal.tenant_id, Jsonb(payload)))
            conn.execute(
                """INSERT INTO product.draft_items (draft_id, file_version_id, role, selection, removal_reason, validation_job_id, analysis_job_id, position, rule_version_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (task_id, row["file_version_id"], "reference" if reference_only else "target", row["selection"], row["removal_reason"], row["validation_id"], analysis_id, position, preferred),
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

    def get_audit_task(self, principal: Principal, task_id: str, request_id: str | None = None) -> dict:
        if request_id is not None and not _as_uuid(request_id):
            raise ValueError("invalid_task_confirmation")
        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            task = self._recognition_public(conn, principal, task_id)
            if request_id and task.get("acceptance", {}).get("request_id") != _as_uuid(request_id):
                queued = self._task_confirmation_receipt(conn, principal, task_id, request_id=_as_uuid(request_id))
                task["acceptance"] = queued or {"request_id": _as_uuid(request_id), "state": "pending", "reason": None}
            return task

    def _precheck_receipt(self, row, *, current_revision: int | None = None) -> dict:
        payload = row[1]
        return {
            "id": str(row[0]),
            "task_id": payload["task_id"],
            "input_revision": payload["input_revision"],
            "current": current_revision == payload["input_revision"],
            "status": "unknown" if row[2] == "leased" and row[4] else row[2],
            "input": payload,
            "result": row[3] if row[2] in {"succeeded", "failed"} else None,
            "created_at": row[5].isoformat(),
            "updated_at": row[6].isoformat(),
        }

    def _current_task_precheck(self, conn, task_id: str, tenant_id: str, revision: int) -> dict | None:
        row = conn.execute(
            """SELECT id, payload, status, result, lease_until < now(), created_at, updated_at
               FROM engine.jobs WHERE tenant_id = %s AND kind = 'task_precheck'
                 AND payload->>'task_id' = %s AND payload->>'input_revision' = %s
                 AND payload->>'execution_revision' = %s
                 AND NOT EXISTS (
                     SELECT 1 FROM jsonb_array_elements(payload->'files') AS source
                     LEFT JOIN product.draft_items current_input
                       ON current_input.draft_id = %s
                      AND current_input.file_version_id::text = source->'file'->>'id'
                     WHERE current_input.file_version_id IS NULL
                        OR current_input.selection IS DISTINCT FROM source->>'selection'
                 ) ORDER BY created_at DESC, id DESC LIMIT 1""",
            (tenant_id, task_id, str(revision), self.settings.standard_execution_policy().engine_revision(), task_id),
        ).fetchone()
        from product.store.prechecks import precheck_suites_current

        return self._precheck_receipt(row, current_revision=revision) if row and precheck_suites_current(self, conn, row[1]) else None

    def _queue_task_precheck(self, conn, principal: Principal, task: dict, *, retry_key: str | None = None) -> dict | None:
        from product.prechecks import precheck_identity
        from product.store.prechecks import freeze_task_precheck

        v4_customers = {customer for row in task["files"] for customer in _preset_customers((((row.get("match") or {}).get("rule") or {}).get("body") or {}))}
        if task["customer_preference"] not in {"UAES", "理想", "蔚来"} and not v4_customers:
            return None
        if task["state"] == "matching":
            raise ValueError("task_not_ready")
        payload = freeze_task_precheck(self, conn, principal, task)
        identifier = precheck_identity(payload)
        previous = conn.execute(
            "SELECT id, status FROM engine.jobs WHERE tenant_id = %s AND kind = 'task_precheck' AND payload = %s ORDER BY created_at DESC, id DESC LIMIT 1", (principal.tenant_id, Jsonb(payload))
        ).fetchone()
        if previous:
            identifier = str(previous[0])
            if retry_key and previous[1] in {"failed", "cancelled"}:
                identifier = str(uuid.uuid5(uuid.UUID(precheck_identity(payload)), "retry:" + retry_key))
        conn.execute(
            """INSERT INTO engine.jobs (id, tenant_id, kind, payload, status)
                     VALUES (%s, %s, 'task_precheck', %s, 'queued') ON CONFLICT (id) DO NOTHING""",
            (identifier, principal.tenant_id, Jsonb(payload)),
        )
        row = conn.execute(
            "SELECT id, payload, status, result, lease_until < now(), created_at, updated_at FROM engine.jobs WHERE id = %s AND tenant_id = %s AND kind = 'task_precheck'",
            (identifier, principal.tenant_id),
        ).fetchone()
        if not row or row[1] != payload:
            raise ValueError("precheck_identity_conflict")
        return self._precheck_receipt(row, current_revision=task["input_revision"])

    def _queue_recognized_precheck(self, conn, task_id: str) -> None:
        # Worker reads only preparation columns. It does not impersonate a user
        # or acquire access to sessions, identities, grants or manual reviews.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("recognition:" + task_id,))
        draft = conn.execute("SELECT tenant_id, input_revision, customer_preference, analysis_mode FROM product.audit_drafts WHERE id = %s", (task_id,)).fetchone()
        if not draft:
            return
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT i.file_version_id, i.selection, i.role, f.file_id, f.tenant_id, f.original_filename,
                f.content_type, f.byte_size, f.sha256, f.object_key, f.status, j.payload, j.result, j.status AS job_status
                FROM product.draft_items i JOIN product.file_versions f ON f.id = i.file_version_id
                LEFT JOIN engine.jobs j ON j.id = i.analysis_job_id WHERE i.draft_id = %s ORDER BY i.position, i.file_version_id""",
                (task_id,),
            )
            rows = cur.fetchall()
        files = []
        initiators = set()
        for row in rows:
            match = None
            if row["selection"] == "selected":
                payload, result = row["payload"] or {}, row["result"] or {}
                if payload.get("initiating_user_id"):
                    initiators.add(payload["initiating_user_id"])
                if row["job_status"] in {"queued", "leased"}:
                    return
                bound = self._analysis_snapshot_bound(payload, result, task_id, draft[1], str(row["file_version_id"]))
                state = result.get("matching_status") if bound and row["job_status"] == "succeeded" else "failed"
                match = {
                    "state": state,
                    "document_type": result.get("document_type") if bound else None,
                    "metadata": result.get("metadata") or {"customer": None, "model": None, "part": None, "part_number": None},
                    "gaps": result.get("notices") or (["rule_matching_failed"] if state == "failed" else []),
                    "rule": None,
                }
                if state == "matched":
                    candidate = next((rule for rule in payload.get("candidate_rules", []) if rule["id"] == result.get("matched_rule_id")), None)
                    if candidate:
                        stored_rule = conn.execute("SELECT id, title, body FROM product.rule_versions WHERE id = %s AND tenant_id = %s", (candidate["id"], draft[0])).fetchone()
                        if stored_rule:
                            match["rule"] = {"id": str(stored_rule[0]), "title": stored_rule[1], "body": stored_rule[2]}
                    else:
                        match.update(state="failed", gaps=["matching_snapshot_invalid"])
            version = self._version_from_row(
                (row["file_version_id"], *(row[key] for key in ("file_id", "tenant_id", "original_filename", "content_type", "byte_size", "sha256", "object_key", "status")))
            )
            files.append({"file": version.public(), "selection": row["selection"], "match": match, "reference_only": row["role"] == "reference"})
        task = {"id": task_id, "input_revision": draft[1], "customer_preference": draft[2], "analysis_mode": draft[3], "state": "pending_confirm", "files": files}
        if len(initiators) != 1:
            return
        principal = Principal(next(iter(initiators)), str(draft[0]), "", "", "", "", "")
        self._queue_task_precheck(conn, principal, task)

    def request_task_precheck(self, principal: Principal, task_id: str, request_id, expected_revision) -> dict:
        from product.standard_policy import config_digest

        key = _as_uuid(request_id)
        if not key or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("invalid_precheck_request")
        with self._conn() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("precheck-request:" + key,))
            draft = self._recognition_owner(conn, principal, task_id, lock=True)
            prior = conn.execute("SELECT tenant_id, kind, payload FROM engine.jobs WHERE id = %s", (key,)).fetchone()
            if prior:
                if str(prior[0]) != principal.tenant_id or prior[1] != "task_precheck_request" or prior[2].get("task_id") != task_id:
                    raise KeyError("missing")
                if prior[2].get("input_revision") != expected_revision:
                    raise ValueError("idempotency_conflict")
                return self._read_task_precheck(conn, principal, task_id, key, draft["input_revision"])
            if draft["input_revision"] != expected_revision:
                raise ValueError("task_revision_conflict")
            receipt = self._queue_task_precheck(conn, principal, self._recognition_public(conn, principal, task_id), retry_key=key)
            if receipt is None:
                raise ValueError("precheck_not_available")
            operation = {"task_id": task_id, "input_revision": expected_revision, "snapshot_id": receipt["id"], "input_fingerprint": config_digest(receipt["input"])}
            created = conn.execute(
                "INSERT INTO engine.jobs (id, tenant_id, kind, payload, status) VALUES (%s, %s, 'task_precheck_request', %s, 'succeeded') ON CONFLICT (id) DO NOTHING RETURNING id",
                (key, principal.tenant_id, Jsonb(operation)),
            ).fetchone()
            if not created:
                raise ValueError("idempotency_conflict")
            receipt["request_id"] = key
            return receipt

    def _read_task_precheck(self, conn, principal: Principal, task_id: str, snapshot_id: str, revision: int) -> dict:
        if not _as_uuid(snapshot_id):
            raise KeyError("missing")
        operation = conn.execute(
            "SELECT payload FROM engine.jobs WHERE id = %s AND tenant_id = %s AND kind = 'task_precheck_request' AND payload->>'task_id' = %s", (snapshot_id, principal.tenant_id, task_id)
        ).fetchone()
        request_id = snapshot_id if operation else None
        resolved_id = operation[0]["snapshot_id"] if operation else snapshot_id
        row = conn.execute(
            """SELECT id, payload, status, result, lease_until < now(), created_at, updated_at FROM engine.jobs
                              WHERE id = %s AND tenant_id = %s AND kind = 'task_precheck' AND payload->>'task_id' = %s""",
            (resolved_id, principal.tenant_id, task_id),
        ).fetchone()
        if not row:
            raise KeyError("missing")
        self._prepared_items(conn, principal, [{"file_version_id": item["file"]["id"], "role": "reference"} for item in row[1]["files"]])
        receipt = self._precheck_receipt(row, current_revision=revision)
        current = self._current_task_precheck(conn, task_id, principal.tenant_id, revision)
        receipt["current"] = bool(current and current["id"] == receipt["id"])
        if request_id:
            receipt["request_id"] = request_id
        return receipt

    def get_task_precheck(self, principal: Principal, task_id: str, snapshot_id: str) -> dict:
        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            draft = self._recognition_owner(conn, principal, task_id)
            return self._read_task_precheck(conn, principal, task_id, snapshot_id, draft["input_revision"])

    def list_task_prechecks(self, principal: Principal, task_id: str) -> dict:
        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            draft = self._recognition_owner(conn, principal, task_id)
            rows = conn.execute(
                """SELECT id, payload, status, result, lease_until < now(), created_at, updated_at FROM engine.jobs
                                 WHERE tenant_id = %s AND kind = 'task_precheck' AND payload->>'task_id' = %s ORDER BY created_at DESC, id DESC""",
                (principal.tenant_id, task_id),
            ).fetchall()
            records = [self._read_task_precheck(conn, principal, task_id, str(row[0]), draft["input_revision"]) for row in rows]
            return {"snapshots": records}

    def _notification_preference(self, conn, draft: dict) -> dict:
        row = conn.execute(
            "SELECT status, result, lease_until < now() FROM engine.jobs WHERE tenant_id = %s AND kind = 'feishu_notification' AND payload->>'task_id' = %s AND payload->>'stage' = 'completed'",
            (draft["tenant_id"], str(draft["id"])),
        ).fetchone()
        state, reason = "not_due", None
        if row:
            result = row[1] or {}
            state = result.get("state", "queued")
            reason = result.get("reason")
            if state == "dispatching":
                state = "unknown" if row[2] else "sending"
        available = bool(self.settings.feishu_notifications_enabled and self.settings.feishu_app_id and self.settings.feishu_app_secret)
        if not available and state in {"not_due", "queued", "preparing"}:
            reason = "feishu_notifications_unavailable"
        return {
            "task_id": str(draft["id"]),
            "enabled": draft["notification_enabled"],
            "revision": draft["notification_revision"],
            "can_change": state in {"not_due", "queued", "preparing"},
            "available": available,
            "delivery": {"state": state, "reason": reason},
        }

    def get_task_notification(self, principal: Principal, task_id: str) -> dict:
        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return self._notification_preference(conn, self._recognition_owner(conn, principal, task_id))

    def save_task_notification(self, principal: Principal, task_id: str, body: dict) -> dict:
        if set(body) != {"enabled", "expected_revision"} or type(body.get("enabled")) is not bool or type(body.get("expected_revision")) is not int or body["expected_revision"] < 0:
            raise ValueError("invalid_notification_preference")
        with self._conn() as conn, conn.transaction():
            draft = self._recognition_owner(conn, principal, task_id, lock=True)
            if draft["notification_revision"] != body["expected_revision"]:
                raise ValueError("notification_revision_conflict")
            if not self._notification_preference(conn, draft)["can_change"]:
                raise ValueError("notification_already_dispatched")
            conn.execute(
                "UPDATE product.audit_drafts SET notification_enabled = %s, notification_revision = notification_revision + 1, updated_at = now() WHERE id = %s",
                (body["enabled"], draft["id"]),
            )
            return self._notification_preference(conn, self._recognition_owner(conn, principal, task_id))

    def _queue_task_notification(self, conn, task_id: str | None, stage: str, round_id: str | None = None, *, package_id: str | None = None, operation_id: str | None = None) -> None:
        if stage == "optimization_completed":
            # The original operation, not a browser session or task creator,
            # owns this intent. Bare historical Rounds need no artificial Task.
            owner = conn.execute(
                """SELECT o.tenant_id, r.source_draft_id FROM product.checker_optimizations o
                   JOIN product.audit_rounds r ON r.id=%s AND r.tenant_id=o.tenant_id
                   WHERE o.operation_id=%s AND o.completed_at IS NOT NULL
                   AND o.state IN ('completed','failed','unknown','stopped')""",
                (round_id, operation_id),
            ).fetchone()
            if not owner:
                return
            task_id = str(owner[1]) if owner[1] else None
        else:
            owner = conn.execute("SELECT tenant_id FROM product.audit_drafts WHERE id=%s AND task_fingerprint IS NOT NULL", (task_id,)).fetchone()
            if not owner:
                return
        payload = {"stage": stage, **({"task_id": task_id} if task_id else {}), **({"package_id": package_id} if package_id else {}), **({"operation_id": operation_id} if operation_id else {})}
        conn.execute(
            """INSERT INTO engine.jobs (id, tenant_id, audit_round_id, kind, payload, status, result)
            VALUES (gen_random_uuid(), %s, %s, 'feishu_notification', %s, 'queued', %s)
            ON CONFLICT DO NOTHING""",
            (owner[0], round_id, Jsonb(payload), Jsonb({"state": "queued", "reason": None})),
        )

    def _queue_finished_notification(self, conn, task_id: str, round_id: str) -> None:
        if not conn.execute(
            """SELECT 1 FROM engine.jobs WHERE audit_round_id = %s AND kind = 'report_pdf'
            AND status IN ('queued', 'leased') AND NOT (payload ? 'regeneration_request_id')""",
            (round_id,),
        ).fetchone():
            self._queue_task_notification(conn, task_id, "completed", round_id)

    def claim_task_notification(self, worker_ident: str, *, task_id: str | None = None) -> tuple[ClaimedJob, Principal] | None:
        if task_id is not None and not _as_uuid(task_id):
            raise ValueError("invalid_task_id")
        with self._conn() as conn, conn.transaction():
            conn.execute(
                """UPDATE engine.jobs SET result = result || '{"state":"unknown","reason":"send_receipt_unknown"}'::jsonb,
                lease_until = NULL, updated_at = now() WHERE kind = 'feishu_notification' AND status = 'leased'
                AND result->>'state' = 'dispatching' AND lease_until < now()
                AND (%s::text IS NULL OR payload->>'task_id' = %s)""",
                (task_id, task_id),
            )
            row = conn.execute(
                """SELECT id, tenant_id, audit_round_id, payload FROM engine.jobs WHERE kind = 'feishu_notification'
                AND ((status = 'queued' AND (lease_until IS NULL OR lease_until < now())) OR
                    (status = 'leased' AND result->>'state' = 'preparing' AND lease_until < now()))
                AND (%s::text IS NULL OR payload->>'task_id' = %s)
                ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1""",
                (task_id, task_id),
            ).fetchone()
            if not row:
                return None
            attempt = cast(
                tuple,
                conn.execute(
                    """UPDATE engine.jobs SET status = 'leased', attempt = attempt + 1, lease_owner = %s,
                lease_until = now() + interval '90 seconds', result = '{"state":"preparing","reason":null}'::jsonb,
                updated_at = now() WHERE id = %s RETURNING attempt""",
                    (worker_ident, row[0]),
                ).fetchone(),
            )[0]
            conn.execute("INSERT INTO engine.attempts (id, job_id, attempt, worker_id) VALUES (gen_random_uuid(), %s, %s, %s)", (row[0], attempt, worker_ident))
            claimed = ClaimedJob(str(row[0]), "feishu_notification", row[3], attempt, str(row[2]) if row[2] else None, str(row[1]))
            if row[3].get("stage") == "optimization_completed":
                identities = conn.execute(
                    """SELECT i.user_id, i.provider, i.app_id, i.tenant_key, i.subject, m.role, i.display_name
                    FROM product.checker_optimizations o JOIN product.tenants t ON t.id=o.tenant_id
                    JOIN product.memberships m ON m.tenant_id=t.id AND m.user_id=o.initiating_user_id
                    JOIN product.identities i ON i.user_id=o.initiating_user_id AND i.provider=t.provider AND i.tenant_key=t.external_key
                    WHERE o.operation_id=%s AND o.tenant_id=%s AND i.app_id=%s AND m.role='member'""",
                    (_as_uuid(str(row[3].get("operation_id") or "")), row[1], self.settings.feishu_app_id),
                ).fetchall()
            else:
                identities = conn.execute(
                    """SELECT i.user_id, i.provider, i.app_id, i.tenant_key, i.subject, m.role, i.display_name
                    FROM product.audit_drafts d JOIN product.tenants t ON t.id = d.tenant_id
                    JOIN product.memberships m ON m.tenant_id = t.id AND m.user_id = d.created_by
                    JOIN product.identities i ON i.user_id = d.created_by AND i.provider = t.provider AND i.tenant_key = t.external_key
                    WHERE d.id = %s AND d.tenant_id = %s AND i.app_id = %s""",
                    (row[3].get("task_id"), row[1], self.settings.feishu_app_id),
                ).fetchall()
            if len(identities) != 1:
                self._finish_task_notification(conn, claimed, worker_ident, {"state": "suppressed", "reason": "recipient_unavailable", "message_id": None}, before_dispatch=True)
                return None
            identity = identities[0]
            principal = Principal(str(identity[0]), str(row[1]), *identity[1:])
            return claimed, principal

    def _finish_task_notification(self, conn, claimed: ClaimedJob, worker_ident: str, result: dict, *, before_dispatch: bool = False) -> bool:
        state = result["state"]
        status = {"sent": "succeeded", "failed": "failed", "unknown": "leased", "suppressed": "cancelled"}[state]
        updated = conn.execute(
            """UPDATE engine.jobs SET status = %s, result = %s, lease_until = NULL, updated_at = now()
            WHERE id = %s AND kind = 'feishu_notification' AND attempt = %s AND lease_owner = %s AND status = 'leased'
            AND result->>'state' = ANY(%s) RETURNING id""",
            (status, Jsonb(result), claimed.id, claimed.attempt, worker_ident, ["preparing"] if before_dispatch else ["dispatching", "unknown"]),
        ).fetchone()
        if not updated:
            return False
        conn.execute(
            "UPDATE engine.attempts SET finished_at = now(), outcome = %s, diagnostic = %s WHERE job_id = %s AND attempt = %s",
            (state, Jsonb({"reason": result.get("reason")}), claimed.id, claimed.attempt),
        )
        return True

    def finish_task_notification(self, claimed: ClaimedJob, worker_ident: str, result: dict) -> bool:
        if result.get("state") not in {"sent", "unknown", "failed"} or result["state"] == "sent" and (not isinstance(result.get("message_id"), str) or not result["message_id"].strip()):
            raise ValueError("invalid_notification_receipt")
        with self._conn() as conn, conn.transaction():
            return self._finish_task_notification(conn, claimed, worker_ident, result)

    def _defer_task_notification(self, conn, claimed: ClaimedJob, worker_ident: str) -> bool:
        row = conn.execute(
            """UPDATE engine.jobs SET status = 'queued', lease_until = now() + interval '60 seconds', updated_at = now(),
                result = '{"state":"queued","reason":"feishu_connection_unavailable"}'::jsonb
                WHERE id = %s AND attempt = %s AND lease_owner = %s AND status = 'leased' AND result->>'state' = 'preparing' RETURNING id""",
            (claimed.id, claimed.attempt, worker_ident),
        ).fetchone()
        if row:
            conn.execute("UPDATE engine.attempts SET finished_at = now(), outcome = 'deferred' WHERE job_id = %s AND attempt = %s", (claimed.id, claimed.attempt))
        return row is not None

    def defer_task_notification(self, claimed: ClaimedJob, worker_ident: str) -> bool:
        with self._conn() as conn, conn.transaction():
            return self._defer_task_notification(conn, claimed, worker_ident)

    def start_task_notification(self, claimed: ClaimedJob, worker_ident: str, principal: Principal) -> dict | None:
        task_id, stage = _as_uuid(str(claimed.payload.get("task_id") or "")), claimed.payload.get("stage")
        package_id = _as_uuid(str(claimed.payload.get("package_id") or "")) if stage == "zip_ready" else None
        operation_id = _as_uuid(str(claimed.payload.get("operation_id") or "")) if stage == "optimization_completed" else None
        if stage not in {"confirmation", "started", "completed", "zip_ready", "optimization_completed"}:
            raise ValueError("invalid_notification_scope")
        if stage == "optimization_completed" and not operation_id or stage != "optimization_completed" and not task_id or stage == "zip_ready" and not package_id:
            raise ValueError("invalid_notification_scope")
        with self._conn() as conn, conn.transaction():
            reason, report_package = None, None
            try:
                identity = conn.execute(
                    """SELECT 1 FROM product.identities i JOIN product.memberships m ON m.user_id = i.user_id
                    WHERE i.user_id = %s AND i.provider = 'feishu' AND i.app_id = %s AND i.tenant_key = %s
                    AND i.subject = %s AND m.tenant_id = %s""",
                    (principal.user_id, self.settings.feishu_app_id, principal.tenant_key, principal.subject, principal.tenant_id),
                ).fetchone()
                if not identity or not self.settings.tenant_allowed(principal.tenant_key):
                    raise KeyError("recipient_unavailable")
                if stage == "optimization_completed":
                    from product.store.checker_development import CheckerDevelopmentStore

                    operation, sample = CheckerDevelopmentStore(self).notification_context(conn, principal, operation_id)
                    card = optimization_notification_card(operation, sample, self.settings.canonical_web_url())
                else:
                    assert task_id is not None
                    draft = self._recognition_owner(conn, principal, task_id, lock=True)
                    task = self._recognition_public(conn, principal, task_id)
                    if stage == "confirmation" and task["state"] == "matching":
                        self._defer_task_notification(conn, claimed, worker_ident)
                        return None
                    if stage == "confirmation" and task["state"] != "pending_confirm" or stage == "started" and task["round_id"] != claimed.round_id:
                        reason = "task_advanced"
                    if stage == "completed" and not draft["notification_enabled"]:
                        reason = "completion_disabled"
                    if stage == "completed" and task["state"] != "completed":
                        raise ValueError("notification_task_not_completed")
                    if stage == "zip_ready":
                        request = conn.execute(
                            """SELECT id, payload FROM engine.jobs WHERE tenant_id = %s AND kind = 'report_zip_request'
                            AND payload->>'task_id' = %s AND payload->>'package_id' = %s AND payload->>'created_by' = %s
                            ORDER BY created_at, id LIMIT 1""",
                            (principal.tenant_id, task_id, package_id, principal.user_id),
                        ).fetchone()
                        if not request:
                            raise KeyError("missing")
                        report_package = self._report_package_public(conn, principal, task, str(request[0]), request[1])
                        artifact = report_package.get("artifact") or {}
                        if report_package["state"] != "ready" or not artifact.get("artifact_id") or artifact.get("content_type") != "application/zip" or not artifact.get("byte_size", 0) > 0:
                            reason = "zip_not_ready"
            except KeyError:
                reason = "notification_access_revoked"
            if reason:
                self._finish_task_notification(conn, claimed, worker_ident, {"state": "suppressed", "reason": reason, "message_id": None}, before_dispatch=True)
                return None
            if stage != "optimization_completed":
                card = task_notification_card(task, stage, self.settings.canonical_web_url(), report_package, confirmation_id=claimed.id if self.settings.feishu_verification_token.strip() else None)
            dispatch = {"receive_id": principal.subject, "card": card}
            saved = {"dispatch": dispatch}
            if any(
                behavior.get("type") == "callback" and behavior.get("value", {}).get("action") == "confirm_audit_task"
                for element in card["body"]["elements"]
                for behavior in element.get("behaviors", [])
            ):
                saved["confirmation"] = {
                    "request_id": str(uuid.uuid5(uuid.UUID(claimed.id), "confirm")),
                    "expected_revision": task["input_revision"],
                    "expected_confirmation_basis_digest": task["confirmation_basis_digest"],
                }
            row = conn.execute(
                """UPDATE engine.jobs SET result = '{"state":"dispatching","reason":null}'::jsonb,
                payload = payload || %s, updated_at = now(), lease_until = now() + interval '90 seconds'
                WHERE id = %s AND kind = 'feishu_notification' AND attempt = %s AND lease_owner = %s
                AND status = 'leased' AND result->>'state' = 'preparing' AND lease_until > now()
                AND tenant_id = %s AND COALESCE(payload->>'task_id', '') = %s AND payload->>'stage' = %s
                AND COALESCE(payload->>'package_id', '') = %s AND COALESCE(payload->>'operation_id', '') = %s RETURNING id""",
                (Jsonb(saved), claimed.id, claimed.attempt, worker_ident, principal.tenant_id, task_id or "", stage, package_id or "", operation_id or ""),
            ).fetchone()
            return dispatch if row else None

    def _confirm_task_notification(self, conn, action: CardAction, *, expected_confirmation: dict, usage_policy=None) -> dict:
        notification_id = _as_uuid(action.notification_id)
        if not notification_id or not self.settings.tenant_allowed(action.tenant_key):
            raise KeyError("missing")
        row = conn.execute(
            """SELECT j.payload, i.user_id, j.tenant_id, m.role, i.display_name
                FROM engine.jobs j JOIN product.audit_drafts d ON d.id::text = j.payload->>'task_id' AND d.tenant_id = j.tenant_id
                JOIN product.tenants t ON t.id = d.tenant_id
                JOIN product.identities i ON i.user_id = d.created_by AND i.provider = t.provider AND i.tenant_key = t.external_key
                JOIN product.memberships m ON m.user_id = i.user_id AND m.tenant_id = d.tenant_id
                WHERE j.id = %s AND j.kind = 'feishu_notification' AND j.payload->>'stage' = 'confirmation'
                AND j.result->>'state' = 'sent' AND j.result->>'message_id' = %s
                AND i.provider = 'feishu' AND i.app_id = %s AND i.tenant_key = %s AND i.subject = %s""",
            (notification_id, action.message_id, self.settings.feishu_app_id, action.tenant_key, action.subject),
        ).fetchone()
        if not row:
            raise KeyError("missing")
        confirmation = row[0].get("confirmation")
        if not isinstance(confirmation, dict):
            raise KeyError("missing")
        principal = Principal(str(row[1]), str(row[2]), PROVIDER_FEISHU, self.settings.feishu_app_id, action.tenant_key, action.subject, row[3], row[4])
        task_id = row[0]["task_id"]
        if (
            task_id != expected_confirmation.get("task_id")
            or principal.user_id != expected_confirmation.get("created_by")
            or confirmation.get("request_id") != expected_confirmation.get("request_id")
            or confirmation.get("expected_revision") != expected_confirmation.get("expected_revision")
            or confirmation.get("expected_confirmation_basis_digest") != expected_confirmation.get("expected_confirmation_basis_digest")
        ):
            raise ValueError("task_revision_conflict")
        try:
            self._accept_locked(
                conn,
                principal,
                confirmation["request_id"],
                source_draft_id=task_id,
                predecessor_id=None,
                items=None,
                task_revision=confirmation["expected_revision"],
                usage_policy=usage_policy,
                expected_confirmation_basis_digest=confirmation.get("expected_confirmation_basis_digest"),
                require_confirmation_basis=True,
            )
        except ValueError as error:
            if str(error) != "draft_already_accepted":
                raise
        return self._recognition_public(conn, principal, task_id)

    @staticmethod
    def _task_confirmation_receipt(conn, principal: Principal, task_id: str, *, request_id: str | None = None) -> dict | None:
        row = conn.execute(
            """SELECT j.id, j.result FROM engine.jobs j JOIN product.audit_drafts d ON d.id::text = j.payload->>'task_id'
            WHERE j.kind = 'task_confirmation' AND j.tenant_id = %s AND d.tenant_id = j.tenant_id
            AND d.id = %s AND d.created_by = %s AND j.payload->>'created_by' = %s
            AND (%s::uuid IS NULL AND (j.payload->>'expected_revision')::bigint = d.input_revision OR j.id = %s::uuid)
            ORDER BY j.created_at DESC, j.id DESC LIMIT 1""",
            (principal.tenant_id, task_id, principal.user_id, principal.user_id, request_id, request_id),
        ).fetchone()
        return {"request_id": str(row[0]), "state": (row[1] or {}).get("state", "pending"), "reason": (row[1] or {}).get("reason")} if row else None

    def enqueue_task_confirmation(self, action: CardAction) -> dict:
        """Acknowledge only a committed, authorized intent; never perform quota I/O here."""
        notification_id = _as_uuid(action.notification_id)
        if not notification_id or not self.settings.tenant_allowed(action.tenant_key):
            raise KeyError("missing")
        # Override the ordinary 15-second connect timeout only for this callback.
        # A failed or unconfirmed commit propagates; it is not a pending receipt.
        with psycopg.connect(self.settings.dsn("api"), autocommit=True, connect_timeout=1, options="-c statement_timeout=500 -c lock_timeout=200") as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            query = """WITH source AS (
                    SELECT j.tenant_id, d.id AS task_id, d.created_by, j.payload->'confirmation' AS confirmation,
                        r.id AS round_id
                    FROM engine.jobs j JOIN product.audit_drafts d ON d.id::text = j.payload->>'task_id' AND d.tenant_id = j.tenant_id
                    JOIN product.tenants t ON t.id = d.tenant_id
                    JOIN product.identities i ON i.user_id = d.created_by AND i.provider = t.provider AND i.tenant_key = t.external_key
                    JOIN product.memberships m ON m.user_id = i.user_id AND m.tenant_id = d.tenant_id
                    LEFT JOIN product.audit_rounds r ON r.source_draft_id = d.id AND r.tenant_id = d.tenant_id
                    WHERE j.id = %s AND j.kind = 'feishu_notification' AND j.payload->>'stage' = 'confirmation'
                    AND j.result->>'state' = 'sent' AND j.result->>'message_id' = %s
                    AND i.provider = 'feishu' AND i.app_id = %s AND i.tenant_key = %s AND i.subject = %s
                    AND jsonb_typeof(j.payload->'confirmation') = 'object' AND d.task_fingerprint IS NOT NULL
                    AND EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id = d.id AND g.user_id = d.created_by AND g.capability IN ('read', 'review'))
                    AND (r.id IS NULL OR EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id = r.id AND g.user_id = d.created_by AND g.capability IN ('read', 'review')))
                    AND NOT EXISTS (SELECT 1 FROM product.draft_items di JOIN product.file_versions f ON f.id = di.file_version_id
                        WHERE di.draft_id = d.id AND NOT EXISTS (SELECT 1 FROM product.resource_grants g
                            WHERE g.resource_id = f.file_id AND g.user_id = d.created_by AND g.capability IN ('read', 'review')))
                ), queued AS (
                    INSERT INTO engine.jobs (id, tenant_id, kind, payload, status, result)
                    SELECT (confirmation->>'request_id')::uuid, tenant_id, 'task_confirmation',
                        jsonb_build_object('task_id', task_id::text, 'created_by', created_by::text,
                            'request_id', confirmation->>'request_id', 'expected_revision', confirmation->'expected_revision',
                            'expected_confirmation_basis_digest', confirmation->'expected_confirmation_basis_digest',
                            'notification_id', %s::text, 'message_id', %s::text, 'tenant_key', %s::text, 'subject', %s::text),
                        'queued', jsonb_build_object('state', 'pending', 'reason', '已收到卡片确认，正在核对用量，尚未受理。')
                    FROM source WHERE true ON CONFLICT (id) DO NOTHING
                    RETURNING id, tenant_id, kind, payload, result
                ), receipt AS (
                    SELECT * FROM queued UNION ALL
                    SELECT j.id, j.tenant_id, j.kind, j.payload, j.result FROM engine.jobs j JOIN source s
                        ON j.id = (s.confirmation->>'request_id')::uuid WHERE NOT EXISTS (SELECT 1 FROM queued)
                ) SELECT q.id, q.tenant_id, q.kind, q.payload, q.result FROM receipt q JOIN source s ON s.tenant_id = q.tenant_id
                    AND s.task_id::text = q.payload->>'task_id' AND s.created_by::text = q.payload->>'created_by'"""
            params = (notification_id, action.message_id, self.settings.feishu_app_id, action.tenant_key, action.subject, notification_id, action.message_id, action.tenant_key, action.subject)
            row = conn.execute(query, params).fetchone()
            if row is None:
                # ON CONFLICT can see a concurrently committed unique key that
                # the same statement's READ COMMITTED snapshot cannot read yet.
                # One fresh, bounded statement reads that exact authorized ID;
                # a database wait/commit failure still propagates as unknown.
                row = conn.execute(query, params).fetchone()
            if not row or row[2] != "task_confirmation" or row[3].get("notification_id") != notification_id:
                raise KeyError("missing")
            receipt = {"request_id": str(row[0]), "state": row[4]["state"], "reason": row[4].get("reason")}
        return receipt

    def claim_task_confirmation(self, worker_ident: str) -> ClaimedJob | None:
        with self._conn() as conn, conn.transaction():
            row = conn.execute(
                """SELECT id, tenant_id, payload FROM engine.jobs WHERE kind = 'task_confirmation'
                AND (status = 'queued' OR status = 'leased' AND lease_until < now())
                ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1"""
            ).fetchone()
            if not row:
                return None
            attempt = cast(
                tuple,
                conn.execute(
                    """UPDATE engine.jobs SET status = 'leased', attempt = attempt + 1, lease_owner = %s,
                lease_until = now() + interval '90 seconds', updated_at = now() WHERE id = %s RETURNING attempt""",
                    (worker_ident, row[0]),
                ).fetchone(),
            )[0]
            conn.execute("INSERT INTO engine.attempts (id, job_id, attempt, worker_id) VALUES (gen_random_uuid(), %s, %s, %s)", (row[0], attempt, worker_ident))
            return ClaimedJob(str(row[0]), "task_confirmation", row[2], attempt, tenant_id=str(row[1]))

    def process_task_confirmation(self, claimed: ClaimedJob, worker_ident: str, *, usage_policy=None) -> dict | None:
        if claimed.kind != "task_confirmation":
            return None
        with self._conn() as conn, conn.transaction():
            row = conn.execute(
                """SELECT payload FROM engine.jobs WHERE id = %s AND tenant_id = %s AND kind = 'task_confirmation'
                AND status = 'leased' AND attempt = %s AND lease_owner = %s AND lease_until > clock_timestamp() FOR UPDATE""",
                (claimed.id, claimed.tenant_id, claimed.attempt, worker_ident),
            ).fetchone()
            if not row:
                return None
            payload = row[0]
            receipt = {"request_id": claimed.id, "state": "accepted", "reason": None}
            try:
                # Roll back all admission writes before recording a definite rejection.
                with conn.transaction():
                    self._confirm_task_notification(
                        conn,
                        CardAction(payload["tenant_key"], payload["subject"], payload["notification_id"], payload["message_id"]),
                        usage_policy=usage_policy,
                        expected_confirmation=payload,
                    )
            except (ValueError, KeyError) as error:
                reason = {
                    "rule_candidate_expired": "规则已更新，请打开页面重新识别并核对。",
                    "task_revision_conflict": "文件或审核模式已变化，请打开页面核对。",
                    "task_confirmation_changed": "审核依据已更新，本次未受理。请打开页面刷新并核对后重新确认。",
                    "usage_quota_unavailable": "当前无法确认用量，请打开页面核对后重新确认。",
                    "usage_ai_disabled": "飞书AI包开关已关闭，请联系管理员。",
                    "usage_balance_empty": "当前没有可用额度，请联系管理员。",
                }.get(str(error), "本次未受理，请由任务创建人打开页面核对权限和当前任务。")
                receipt.update(state="rejected", reason=reason)
            updated = conn.execute(
                """UPDATE engine.jobs SET status = %s, result = %s, lease_until = NULL, updated_at = now()
                WHERE id = %s AND attempt = %s AND lease_owner = %s AND lease_until > clock_timestamp() RETURNING id""",
                ("succeeded" if receipt["state"] == "accepted" else "failed", Jsonb(receipt), claimed.id, claimed.attempt, worker_ident),
            ).fetchone()
            if not updated:
                raise TimeoutError("task_confirmation_lease_expired")
            conn.execute(
                "UPDATE engine.attempts SET finished_at = now(), outcome = %s, diagnostic = %s WHERE job_id = %s AND attempt = %s",
                ("succeeded" if receipt["state"] == "accepted" else "failed", Jsonb(receipt), claimed.id, claimed.attempt),
            )
        return receipt

    def _archive_rows(self, conn, principal: Principal) -> list:
        return conn.execute(
            """WITH history AS (SELECT d.id, i.file_version_id, s.object_kind, s.object_ref, a.created_at,
                a.id AS round_id, f.file_id, f.original_filename, matched.metadata,
                matched.document_type, display.display_values,
                GREATEST(a.created_at, display.created_at, (SELECT max(work.updated_at) FROM engine.jobs work WHERE work.audit_round_id = a.id)) AS archive_updated_at,
                row_number() OVER (PARTITION BY s.object_kind, s.object_ref ORDER BY a.created_at, a.id, i.file_version_id) AS sequence
            FROM product.audit_drafts d JOIN product.audit_rounds a ON a.source_draft_id = d.id AND a.tenant_id = d.tenant_id
            JOIN product.round_inputs i ON i.round_id = a.id AND i.role = 'target'
            JOIN product.file_versions f ON f.id = i.file_version_id AND f.tenant_id = d.tenant_id
            JOIN product.file_version_sources s ON s.file_version_id = f.id AND s.provider = 'feishu'
            JOIN product.draft_items di ON di.draft_id = d.id AND di.file_version_id = f.id
            LEFT JOIN engine.jobs j ON j.id = di.analysis_job_id
            LEFT JOIN LATERAL (SELECT j.result->'metadata' AS metadata, j.result->>'document_type' AS document_type
                WHERE j.status IN ('succeeded', 'failed', 'cancelled')
                AND j.payload->>'task_id' = d.id::text
                AND COALESCE(jsonb_typeof(j.payload->'task_revision'), 'number') = 'number'
                AND COALESCE(j.payload->>'task_revision', '0') = d.input_revision::text
                AND j.payload->>'source_file_version_id' = i.file_version_id::text
                AND j.result->>'source_file_version_id' = i.file_version_id::text) matched ON true
            LEFT JOIN LATERAL (SELECT display_values, created_at FROM product.audit_display_requests x
                WHERE x.task_id = d.id AND x.round_id = a.id AND x.file_version_id = f.id AND x.state = 'saved'
                ORDER BY expected_revision DESC LIMIT 1) display ON true
            WHERE d.tenant_id = %s AND d.created_by = %s AND d.task_fingerprint IS NOT NULL)
            SELECT id, file_version_id, object_kind, object_ref, created_at, sequence,
                original_filename, metadata, document_type, display_values, archive_updated_at, round_id FROM history h
            WHERE NOT EXISTS (SELECT 1 FROM unnest(ARRAY[h.id, h.round_id, h.file_id]) wanted(resource_id)
                WHERE NOT EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id = wanted.resource_id
                    AND g.user_id = %s AND g.capability IN ('read', 'review')))
            AND NOT EXISTS (SELECT 1 FROM product.draft_items other
                JOIN product.file_versions other_file ON other_file.id = other.file_version_id
                WHERE other.draft_id = h.id AND NOT EXISTS (SELECT 1 FROM product.resource_grants g
                    WHERE g.resource_id = other_file.file_id AND g.user_id = %s AND g.capability IN ('read', 'review')))
            ORDER BY created_at, round_id, file_version_id""",
            (principal.tenant_id, principal.user_id, principal.user_id, principal.user_id),
        ).fetchall()

    @staticmethod
    def _archive_document_id(principal: Principal, row) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([principal.tenant_id, "feishu", row[2], row[3]])))

    def _archive_task_summary(self, conn, principal: Principal, task_id: str, round_id: str) -> dict:
        """Read only archive fields, retaining the full task's ownership/readiness checks."""
        draft = self._recognition_owner(conn, principal, task_id)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT i.file_version_id, i.selection, i.validation_job_id, f.original_filename,
                    j.status AS analysis_status, j.payload AS analysis_payload, j.result AS analysis_result
                FROM product.draft_items i JOIN product.file_versions f ON f.id = i.file_version_id
                LEFT JOIN engine.jobs j ON j.id = i.analysis_job_id
                WHERE i.draft_id = %s ORDER BY i.position""",
                (task_id,),
            )
            files = cur.fetchall()
        self._prepared_items(conn, principal, [{"file_version_id": str(file["file_version_id"]), "role": "reference"} for file in files])
        self._file_jobs(conn, principal, [str(file["validation_job_id"]) for file in files if file["validation_job_id"]], "file_validation")
        round = self._round_public(conn, round_id)
        if round is None or round["source_draft_id"] != task_id:
            raise KeyError("missing")
        reports, _updated = self._task_file_reports(conn, principal.tenant_id, task_id, round_id, round["result_attempt"])
        projected = []
        for file in files:
            vid = str(file["file_version_id"])
            frozen = next((item for item in round["inputs"] if item["file_version_id"] == vid and item["role"] == "target"), None)
            if file["selection"] != "selected" or frozen is None:
                continue
            audit = file_round_audit(round, frozen)
            if vid in reports:
                audit["report"] = reports[vid]
            metadata = {"customer": None, "model": None, "part": None, "part_number": None}
            result = file["analysis_result"] or {}
            bound = self._analysis_snapshot_bound(file["analysis_payload"] or {}, result, task_id, draft["input_revision"], vid)
            terminal = file["analysis_status"] in {"succeeded", "failed", "cancelled"}
            match: dict[str, Any] = {"metadata": (result.get("metadata") or metadata) if bound and terminal else metadata, "document_type": result.get("document_type") if bound and terminal else None}
            projected.append(
                {
                    "file": {"id": vid, "original_filename": file["original_filename"]},
                    "audit": audit,
                    "match": match,
                    "display_info": {"values": {key: match["metadata"].get(key) for key in ("customer", "model", "part_number", "part")}},
                }
            )
        return {"round_id": round_id, "files": projected}

    @staticmethod
    def _analysis_snapshot_bound(payload: dict, result: dict, task_id: str, revision: int, version_id: str) -> bool:
        snapshot_revision = payload.get("task_revision", 0)
        return (
            payload.get("task_id") == task_id
            and type(snapshot_revision) is int
            and snapshot_revision == revision
            and payload.get("source_file_version_id") == version_id
            and result.get("source_file_version_id") == version_id
        )

    def _archive_records(self, conn, principal: Principal, rows: list, *, details: bool = False) -> dict:
        tasks: dict[str, dict] = {}
        records: dict[str, dict] = {}
        for row in rows:
            task_id, version_id, _kind, _source, accepted_at, sequence = row[:6]
            tid, vid = str(task_id), str(version_id)
            if tid not in tasks:
                tasks[tid] = self._recognition_public(conn, principal, tid) if details else self._archive_task_summary(conn, principal, tid, str(row[11]))
            task = tasks[tid]
            file = next((item for item in task["files"] if item["file"]["id"] == vid and item.get("audit")), None)
            if file is None:
                continue
            audit = file["audit"]
            document_id = self._archive_document_id(principal, row)
            record = records.setdefault(document_id, {"entries": [], "details": []})
            entry = {
                "document_id": document_id,
                "reference": {"task_id": tid, "round_id": task["round_id"], "file_version_id": vid, "result_attempt": audit["result_attempt"]},
                "sequence": sequence,
                "audited_at": accepted_at.isoformat(),
                "initiator": principal.display_name,
                "execution": audit["execution"],
                "conclusion": audit["conclusion"],
                "source_revision": None,
                "report": audit["report"],
                "report_access": {"state": "allowed", "reason": None},
            }
            record["entries"].append(entry)
            record["details"].append((entry, task, file))
            metadata = (file.get("match") or {}).get("metadata") or {"customer": None, "model": None, "part": None}
            metadata = {**metadata, **((file.get("display_info") or {}).get("values") or {})}
            if not details:
                metadata = {**metadata, **(row[9] or {})}
            completed = next((row for row in reversed(record["entries"]) if row["execution"] == "completed" and row["conclusion"] in {"PASS", "NG"}), None)
            record["document"] = {
                "document_id": document_id,
                "provider": "feishu",
                "title": file["file"]["original_filename"],
                "metadata": metadata,
                "document_type": (file.get("match") or {}).get("document_type"),
                "updated_at": max(row[10].isoformat(), record.get("document", {}).get("updated_at", "")),
                "latest_completed": completed,
                "latest_activity": entry,
            }
        return records

    def list_audit_archive(self, principal: Principal, query: dict) -> dict:
        from .archive import archive_page

        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            rows = self._archive_rows(conn, principal)
            summaries: dict[str, dict] = {}
            for row in rows:
                document_id = self._archive_document_id(principal, row)
                summaries[document_id] = {
                    "document_id": document_id,
                    "title": row[6],
                    "metadata": {**(row[7] or {}), **(row[9] or {})},
                    "document_type": row[8],
                    "updated_at": max(row[10].isoformat(), summaries.get(document_id, {}).get("updated_at", "")),
                    "latest_completed": None,
                    "latest_activity": {"initiator": principal.display_name, "audited_at": row[4].isoformat()},
                }
            page = archive_page(list(summaries.values()), query)
            selected = {document["document_id"] for document in page["documents"]}
            records = self._archive_records(conn, principal, [row for row in rows if self._archive_document_id(principal, row) in selected])
            page["documents"] = [records[document["document_id"]]["document"] for document in page["documents"]]
            return page

    def read_audit_history(self, principal: Principal, document_id: str, reference: dict | None = None) -> dict:
        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            rows = [row for row in self._archive_rows(conn, principal) if self._archive_document_id(principal, row) == document_id]
            if reference is not None:
                rows = [row for row in rows if str(row[0]) == reference.get("task_id") and str(row[1]) == reference.get("file_version_id")]
            record = self._archive_records(conn, principal, rows, details=reference is not None).get(document_id)
            if record is None:
                raise KeyError("missing")
            if reference is None:
                completed = record["document"]["latest_completed"]
                return {"document": record["document"], "entries": list(reversed(record["entries"])), "default_reference": completed["reference"] if completed else None}
            selected = next((detail for detail in record["details"] if detail[0]["reference"] == reference), None)
            if selected is None:
                raise KeyError("missing")
            entry, task, file = selected
            inputs = conn.execute(
                """SELECT i.file_version_id, f.file_id, EXISTS (SELECT 1 FROM product.resource_grants g
                WHERE g.resource_id = f.file_id AND g.user_id = %s AND g.capability IN ('read', 'review'))
                , f.tenant_id, f.original_filename, f.content_type, f.byte_size, f.sha256, f.object_key, f.status
                FROM product.round_inputs i JOIN product.file_versions f ON f.id = i.file_version_id
                WHERE i.round_id = %s AND f.tenant_id = %s""",
                (principal.user_id, task["round_id"], principal.tenant_id),
            ).fetchall()
            originals = []
            for version_id, file_id, allowed, *version_fields in inputs:
                version = self._version_from_row((version_id, file_id, *version_fields)) if allowed else None
                originals.append(
                    {
                        "file_version_id": str(version_id),
                        "file": version.public() if version else None,
                        "access": {"state": "allowed" if version else "denied", "reason": None if version else "当前账号无权读取这份原件。"},
                    }
                )
            return {
                "entry": entry,
                "file": file,
                "frozen_file_version_ids": [str(row[0]) for row in inputs],
                "originals": originals,
                "source_task_access": {"state": "allowed", "reason": None},
                "report_access": entry["report_access"],
            }

    def confirm_audit_task(
        self,
        principal: Principal,
        task_id: str,
        request_id: str,
        expected_revision: int,
        *,
        usage_policy=None,
        expected_confirmation_basis_digest: str | None = None,
        require_confirmation_basis: bool = False,
    ) -> dict:
        parsed_task, key = _as_uuid(task_id), _as_uuid(request_id)
        if not parsed_task or not key or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("invalid_task_confirmation")
        rejection = None
        with self._conn() as conn, conn.transaction():
            # Lock an existing confirmation before its admission lock, matching
            # the card worker's order. A lost HTTP rejection is read from the
            # same receipt store; no new operation ledger is needed.
            prior = conn.execute("SELECT tenant_id, kind, payload, result FROM engine.jobs WHERE id=%s FOR UPDATE", (key,)).fetchone()
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (principal.tenant_id + ":" + key,))
            self._recognition_owner(conn, principal, parsed_task)
            if prior:
                if str(prior[0]) != principal.tenant_id or prior[1] != "task_confirmation" or prior[2].get("task_id") != parsed_task or prior[2].get("created_by") != principal.user_id:
                    raise ValueError("idempotency_conflict")
                if (prior[3] or {}).get("state") == "rejected":
                    raise ValueError((prior[3] or {}).get("error") or "task_confirmation_changed")
            try:
                with conn.transaction():
                    self._accept_locked(
                        conn,
                        principal,
                        key,
                        source_draft_id=parsed_task,
                        predecessor_id=None,
                        items=None,
                        task_revision=expected_revision,
                        usage_policy=usage_policy,
                        expected_confirmation_basis_digest=expected_confirmation_basis_digest,
                        require_confirmation_basis=require_confirmation_basis,
                    )
            except ValueError as error:
                if str(error) != "task_confirmation_changed":
                    raise
                rejection = str(error)
                payload = {
                    "task_id": parsed_task,
                    "created_by": principal.user_id,
                    "request_id": key,
                    "expected_revision": expected_revision,
                    "expected_confirmation_basis_digest": expected_confirmation_basis_digest,
                }
                result = {"state": "rejected", "reason": "审核依据已更新，本次未受理。请刷新并核对后重新确认。", "error": rejection}
                if prior:
                    raise ValueError("idempotency_conflict") from None
                saved = conn.execute(
                    """INSERT INTO engine.jobs (id, tenant_id, kind, payload, status, result)
                       VALUES (%s, %s, 'task_confirmation', %s, 'failed', %s) ON CONFLICT (id) DO NOTHING RETURNING id""",
                    (key, principal.tenant_id, Jsonb(payload), Jsonb(result)),
                ).fetchone()
                if not saved:
                    raise ValueError("idempotency_conflict") from None
            else:
                return self._recognition_public(conn, principal, parsed_task)
        # Raise only after committing the definite rejection. Lost responses can
        # recover this exact request without accepting a newly published Suite.
        raise ValueError(rejection)

    @staticmethod
    def _display_receipt(row: dict) -> dict:
        request = {
            "request_id": str(row["request_id"]),
            "reference": {key: str(row[key]) for key in ("task_id", "round_id", "file_version_id")},
            "expected_revision": row["expected_revision"],
            "values": row["display_values"],
        }
        return {
            "request": request,
            "state": row["state"],
            "info": {"revision": row["expected_revision"] + 1, "values": row["display_values"]} if row["state"] == "saved" else row["conflict_info"],
            "reason": None if row["state"] == "saved" else "显示信息已有更新，请核对最新版本。" if row["state"] == "conflict" else "显示版本无效，请重新读取任务。",
        }

    def _display_projection(self, conn, principal: Principal, task: dict) -> dict:
        reference = {"task_id": task["id"], "round_id": task["round_id"]}
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT DISTINCT ON (file_version_id) file_version_id, expected_revision, display_values
                FROM product.audit_display_requests WHERE task_id = %s AND round_id = %s AND state = 'saved'
                ORDER BY file_version_id, expected_revision DESC""",
                (reference["task_id"], reference["round_id"]),
            )
            saved = {str(row["file_version_id"]): {"revision": row["expected_revision"] + 1, "values": row["display_values"]} for row in cur.fetchall()}
        editable = {
            str(row[0])
            for row in conn.execute(
                "SELECT resource_id FROM product.resource_grants WHERE resource_id = ANY(%s::uuid[]) AND user_id = %s AND capability = 'review'", ([task["id"], task["round_id"]], principal.user_id)
            ).fetchall()
        } == {task["id"], task["round_id"]}
        for file in task["files"]:
            if file["selection"] != "selected" or not file.get("audit"):
                continue
            metadata = (file.get("match") or {}).get("metadata") or {}
            file["display_info"] = saved.get(file["file"]["id"], {"revision": 0, "values": {key: metadata.get(key) for key in ("customer", "model", "part_number", "part")}})
            file["display_edit_access"] = {"state": "allowed" if editable else "denied", "reason": None if editable else "你目前没有更正显示信息的权限。"}
        return task

    def save_audit_display(self, principal: Principal, task_id: str, body: dict) -> dict:
        reference, values = body.get("reference"), body.get("values")
        request_id = body.get("request_id")
        if (
            set(body) != {"request_id", "reference", "expected_revision", "values"}
            or not isinstance(request_id, str)
            or _as_uuid(request_id) != request_id
            or not isinstance(reference, dict)
            or set(reference) != {"task_id", "round_id", "file_version_id"}
            or any(not isinstance(value, str) or _as_uuid(value) != value for value in reference.values())
            or reference["task_id"] != task_id
            or type(body.get("expected_revision")) is not int
            or not 0 <= body["expected_revision"] < 9007199254740991
            or not isinstance(values, dict)
            or set(values) != {"customer", "model", "part_number", "part"}
            or any(value is not None and not isinstance(value, str) for value in values.values())
            or len(json.dumps(body, ensure_ascii=False).encode()) > 262144
        ):
            raise ValueError("invalid_display_request")
        with self._conn() as conn, conn.transaction():
            self._recognition_owner(conn, principal, task_id, lock=True)
            task = self._recognition_public(conn, principal, task_id)
            file = next((file for file in task["files"] if file["file"]["id"] == reference["file_version_id"] and file["selection"] == "selected" and file.get("audit")), None)
            if task["round_id"] != reference["round_id"] or file is None:
                raise KeyError("missing")
            grants = conn.execute(
                "SELECT resource_id FROM product.resource_grants WHERE resource_id = ANY(%s::uuid[]) AND user_id = %s AND capability = 'review' FOR SHARE",
                ([task_id, task["round_id"]], principal.user_id),
            ).fetchall()
            if {str(row[0]) for row in grants} != {task_id, task["round_id"]}:
                raise KeyError("missing")
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM product.audit_display_requests WHERE request_id = %s", (body["request_id"],))
                existing = cur.fetchone()
                if existing is None:
                    current = file["display_info"]
                    state = "saved" if current["revision"] == body["expected_revision"] else "conflict" if current["revision"] > body["expected_revision"] else "rejected"
                    cur.execute(
                        """INSERT INTO product.audit_display_requests
                        (request_id, task_id, round_id, file_version_id, created_by, expected_revision, display_values, state, conflict_info)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (request_id) DO NOTHING RETURNING *""",
                        (
                            body["request_id"],
                            task_id,
                            task["round_id"],
                            reference["file_version_id"],
                            principal.user_id,
                            body["expected_revision"],
                            Jsonb(values),
                            state,
                            Jsonb(current) if state == "conflict" else None,
                        ),
                    )
                    existing = cur.fetchone()
                    if existing is None:
                        cur.execute("SELECT * FROM product.audit_display_requests WHERE request_id = %s", (body["request_id"],))
                        existing = cur.fetchone()
                if existing is None:
                    raise RuntimeError("display_receipt_unavailable")
                receipt = self._display_receipt(existing)
                if receipt["request"] != body or str(existing["created_by"]) != principal.user_id:
                    raise ValueError("idempotency_conflict")
                return receipt

    def get_audit_display(self, principal: Principal, task_id: str, request_id: str) -> dict | None:
        if not _as_uuid(request_id):
            raise ValueError("invalid_display_request")
        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            self._recognition_public(conn, principal, task_id)
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM product.audit_display_requests WHERE request_id = %s AND task_id = %s AND created_by = %s", (request_id, task_id, principal.user_id))
                row = cur.fetchone()
                return self._display_receipt(row) if row else None

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
            preparation = conn.execute("SELECT task_fingerprint, case_context FROM product.audit_drafts WHERE id = %s", (task_id,)).fetchone()
            if preparation and preparation[0] is None and PREPARATION_KEY in (preparation[1] or {}):
                self._require_preparation_owner(conn, principal, task_id)
                return read
            owned = self._recognition_owner(conn, principal, task_id)
            revision = 0 if task_id == request_id and PREPARATION_KEY not in (owned["case_context"] or {}) else None
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
        original_rules = {}
        if draft["predecessor_id"]:
            original_rules = {
                str(row[0]): {"id": str(row[1]), "title": row[2]}
                for row in conn.execute(
                    """SELECT i.file_version_id, i.rule_version_id, i.rule_title FROM product.round_inputs i
                JOIN product.audit_rounds r ON r.id = i.round_id
                WHERE r.id = %s AND r.tenant_id = %s AND i.rule_version_id IS NOT NULL
                AND EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id = r.id AND g.user_id = %s AND g.capability IN ('read', 'review'))""",
                    (draft["predecessor_id"], principal.tenant_id, principal.user_id),
                ).fetchall()
            }
        available_rules = self._list_rules(conn, principal.tenant_id) if original_rules else []
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT i.*, f.file_id, f.tenant_id, f.original_filename, f.content_type, f.byte_size, f.sha256, f.object_key, f.status,
                j.status AS analysis_status, j.result AS analysis_result, j.payload AS analysis_payload,
                j.lease_until < now() AS analysis_expired, j.updated_at AS analysis_updated_at
                FROM product.draft_items i JOIN product.file_versions f ON f.id = i.file_version_id
                LEFT JOIN engine.jobs j ON j.id = i.analysis_job_id
                WHERE i.draft_id = %s ORDER BY i.position""",
                (task_id,),
            )
            rows = cur.fetchall()
        retained_replacements = {
            file_id: original_id
            for row in rows
            if (row["analysis_payload"] or {}).get("task_id") == task_id and (row["analysis_payload"] or {}).get("task_revision", 0) == draft["input_revision"]
            for file_id, original_id in (row["analysis_payload"] or {}).get("retained_replacements", {}).items()
        }
        self._prepared_items(conn, principal, [{"file_version_id": str(row["file_version_id"]), "role": "reference"} for row in rows])
        validations = self._file_jobs(conn, principal, [str(row["validation_job_id"]) for row in rows if row["validation_job_id"]], "file_validation")
        rule_ids = {
            candidate["id"] for row in rows for candidate in (row["analysis_payload"] or {}).get("candidate_rules", []) if candidate["id"] == (row["analysis_result"] or {}).get("matched_rule_id")
        }
        rules = (
            {
                str(rule[0]): rule
                for rule in conn.execute("SELECT id, title, body FROM product.rule_versions WHERE id = ANY(%s::uuid[]) AND tenant_id = %s", (list(rule_ids), principal.tenant_id)).fetchall()
            }
            if rule_ids
            else {}
        )
        files = []
        updated = draft["updated_at"]
        for row in rows:
            vid = str(row["file_version_id"])
            replacement = (row["analysis_payload"] or {}).get("replacement_of_file_version_id") or retained_replacements.get(vid)
            version = self._version_from_row(
                (row["file_version_id"], *(row[key] for key in ("file_id", "tenant_id", "original_filename", "content_type", "byte_size", "sha256", "object_key", "status")))
            )
            validation = validations[str(row["validation_job_id"])] if row["validation_job_id"] else {}
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
                    bound = self._analysis_snapshot_bound(payload, result, task_id, draft["input_revision"], vid)
                    state = result.get("matching_status") if status == "succeeded" and bound else "failed"
                    if not bound:
                        result = {"notices": ["matching_snapshot_invalid"]}
                    match.update(
                        state=state if state in {"matched", "reference", "unmatched", "failed"} else "failed",
                        metadata=result.get("metadata") or match["metadata"],
                        document_type=result.get("document_type"),
                        gaps=result.get("notices") or (["rule_matching_failed"] if state == "failed" else []),
                    )
                    if "ambiguous_available_rules" in match["gaps"]:
                        ids = result.get("candidate_rule_ids") or []
                        match["candidates"] = [{"id": rule["id"], "title": rule["title"]} for rule in payload.get("candidate_rules", []) if rule["id"] in ids]
                    rid = result.get("matched_rule_id")
                    if match["state"] == "matched":
                        allowed = any(candidate["id"] == rid for candidate in payload.get("candidate_rules", []))
                        rule = rules.get(cast(str, rid)) if allowed else None
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
                    **({"validation_id": str(row["validation_job_id"])} if row["validation_job_id"] else {}),
                    "validation": checked,
                    **({"validation_scope": validation["scope"]} if validation.get("scope") else {}),
                    "match": match,
                    **({"preferred_rule_version_id": str(row["rule_version_id"])} if row["rule_version_id"] else {}),
                    **({"replacement_of_file_version_id": replacement} if replacement else {}),
                    **({"rule_options": self._reaudit_rule_options(original_rules.get(replacement or vid), available_rules)} if original_rules else {}),
                    **({"reference_only": True} if row["role"] == "reference" and (draft["predecessor_id"] or (row["analysis_payload"] or {}).get("reference_only")) else {}),
                }
            )
        selected = [row for row in files if row["selection"] == "selected"]
        matching = any(row["match"]["state"] in {"pending", "matching"} for row in selected)
        task = {
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
            "notification_enabled": draft["notification_enabled"],
            "notification_revision": draft["notification_revision"],
            "files": files,
            "completion_reason": draft["task_completion_reason"],
            "confirmation_basis_digest": None,
            "estimated_points": None if matching or any(row["match"]["state"] == "failed" for row in selected) else sum(row["match"]["state"] == "matched" for row in selected) * 500,
        }
        task["precheck"] = self._current_task_precheck(conn, task_id, principal.tenant_id, draft["input_revision"])
        if draft["predecessor_id"]:
            task["predecessor_id"] = str(draft["predecessor_id"])
            previous = conn.execute(
                """SELECT d.id FROM product.audit_rounds r JOIN product.audit_drafts d ON d.id = r.source_draft_id
                WHERE r.id = %s AND d.created_by = %s AND d.tenant_id = %s AND d.task_fingerprint IS NOT NULL
                AND EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id = d.id AND g.user_id = %s AND g.capability IN ('read', 'review'))""",
                (draft["predecessor_id"], principal.user_id, principal.tenant_id, principal.user_id),
            ).fetchone()
            if previous:
                task["predecessor_task_id"] = str(previous[0])
        accepted = conn.execute(
            """SELECT a.id, a.idempotency_key, COALESCE(j.updated_at, a.created_at), EXISTS (
                SELECT 1 FROM product.resource_grants g WHERE g.resource_id = a.id AND g.user_id = %s AND g.capability IN ('read', 'review'))
            FROM product.audit_rounds a LEFT JOIN engine.jobs j ON j.audit_round_id = a.id AND j.kind = 'audit'
            WHERE a.source_draft_id = %s AND a.tenant_id = %s ORDER BY a.created_at LIMIT 1""",
            (principal.user_id, task_id, principal.tenant_id),
        ).fetchone()
        if not accepted:
            confirmation = self._task_confirmation_receipt(conn, principal, task_id)
            if confirmation:
                task["acceptance"] = confirmation
        if accepted:
            if not accepted[3]:
                raise KeyError("missing")
            task["acceptance"] = {"request_id": accepted[1], "state": "accepted", "reason": None}
            task["updated_at"] = max(updated, accepted[2]).isoformat()
            round = self._round_public(conn, str(accepted[0]))
            if round is None:
                raise KeyError("missing")
            reports, report_updated = self._task_file_reports(conn, principal.tenant_id, task_id, round["id"], round["result_attempt"])
            if report_updated:
                task["updated_at"] = max(datetime.fromisoformat(task["updated_at"]), report_updated).isoformat()
            task = self._display_projection(conn, principal, task_round_view(task, round, reports))
            task["successor_tasks"] = [
                {"id": str(row[0]), "number": f"PPAP-{row[1]:%Y%m%d}-{row[2]:03d}"}
                for row in conn.execute(
                    """SELECT d.id, d.task_day, d.task_sequence FROM product.audit_drafts d
                WHERE d.predecessor_id = %s AND d.created_by = %s AND d.tenant_id = %s AND d.task_fingerprint IS NOT NULL
                AND EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id = d.id AND g.user_id = %s AND g.capability IN ('read', 'review'))
                ORDER BY d.created_at, d.id""",
                    (round["id"], principal.user_id, principal.tenant_id, principal.user_id),
                ).fetchall()
            ]
            activity = conn.execute(
                """SELECT GREATEST(
                (SELECT max(updated_at) FROM product.reviews WHERE round_id = %s),
                (SELECT max(created_at) FROM product.audit_display_requests WHERE task_id = %s AND state = 'saved'))""",
                (round["id"], task_id),
            ).fetchone()[0]
            if activity:
                task["updated_at"] = max(datetime.fromisoformat(task["updated_at"]), activity).isoformat()
            package = conn.execute(
                """SELECT r.id, r.payload, GREATEST(r.updated_at, p.updated_at) FROM engine.jobs r
                JOIN engine.jobs p ON p.id = (r.payload->>'package_id')::uuid
                WHERE r.kind = 'report_zip_request' AND r.tenant_id = %s AND r.payload->>'task_id' = %s
                  AND r.payload->>'created_by' = %s AND NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements(r.payload->'reports') reference
                    WHERE reference->>'task_id' <> r.payload->>'task_id')
                ORDER BY r.created_at DESC, r.id DESC LIMIT 1""",
                (principal.tenant_id, task_id, principal.user_id),
            ).fetchone()
            if package:
                task["report_package"] = self._report_package_public(conn, principal, task, str(package[0]), package[1])
                task["updated_at"] = max(datetime.fromisoformat(task["updated_at"]), package[2]).isoformat()
            from product.store.report_sharing import ReportShareStore

            share = ReportShareStore(self).latest_for_task(conn, principal, task)
            if share:
                task["report_share"] = share
            return task
        # Recognition's rule version is provenance. Show the same adopted Suite
        # body that acceptance will freeze, without publishing or authorizing it.
        # Accepted tasks returned above must never resolve a newer Suite head.
        from product.standard_policy import config_digest
        from product.store.suites import freeze_selected_targets

        targets = []
        for file in task["files"]:
            match = file.get("match") or {}
            if match.get("state") != "matched" or not match.get("rule"):
                continue
            prepared = [{"role": "target", "file_version_id": file["file"]["id"], "rule_body": match["rule"]["body"]}]
            try:
                targets.extend(freeze_selected_targets(self, conn, principal.tenant_id, prepared, preparation=True))
            except (KeyError, ValueError):
                match.update(state="failed", rule=None, gaps=[*match.get("gaps", []), "checker_suite_unavailable"])
                task["estimated_points"] = None
            else:
                match["rule"] = {**match["rule"], "body": prepared[0]["rule_body"]}
        if targets and task["state"] == "pending_confirm" and all(row["validation"]["state"] == "valid" and row["match"]["state"] in {"matched", "reference", "unmatched"} for row in selected):
            # current_selection holds the publication locks through admission's
            # transaction. Bind source identity, clauses, code, related Suites
            # and verification risk, not merely visible rule text or input_revision.
            task["confirmation_basis_digest"] = config_digest(
                {
                    "task_id": task_id,
                    "tenant_id": principal.tenant_id,
                    "created_by": principal.user_id,
                    "input_revision": task["input_revision"],
                    "analysis_mode": task["analysis_mode"],
                    "customer_preference": task["customer_preference"],
                    "estimated_points": task["estimated_points"],
                    "files": [{key: row.get(key) for key in ("file", "selection", "removal_reason", "validation", "validation_scope", "match", "reference_only")} for row in files],
                    "targets": [target.to_dict() for target in targets],
                }
            )
        return task

    def list_audit_tasks(self, principal: Principal, limit: int = 20, offset: int = 0, q: str = "", state: str = "") -> list[dict]:
        if state not in {"", "matching", "pending_confirm", "reviewing", "completed"}:
            raise ValueError("invalid_task_state")
        needle = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            rows = conn.execute(
                """WITH visible AS (SELECT d.id, d.input_revision, d.analysis_mode, d.created_at,
                GREATEST(d.updated_at, r.created_at,
                    (SELECT max(j.updated_at) FROM product.draft_items i JOIN engine.jobs j ON j.id = i.analysis_job_id WHERE i.draft_id = d.id),
                    (SELECT max(j.updated_at) FROM engine.jobs j WHERE j.audit_round_id = r.id AND j.kind IN ('audit', 'report_pdf')),
                    (SELECT max(v.updated_at) FROM product.reviews v WHERE v.round_id = r.id),
                    (SELECT max(x.created_at) FROM product.audit_display_requests x WHERE x.task_id = d.id AND x.state = 'saved')) AS updated_at,
                d.task_day, d.task_sequence,
                d.task_completion_reason, r.id AS round_id,
                CASE WHEN d.task_completion_reason IS NOT NULL OR (r.execution IN ('completed', 'failed') AND NOT EXISTS (
                    SELECT 1 FROM engine.jobs p WHERE p.audit_round_id = r.id AND p.kind = 'report_pdf' AND p.status IN ('queued', 'leased')
                      AND NOT (p.payload ? 'regeneration_request_id'))) THEN 'completed'
                    WHEN r.id IS NOT NULL THEN 'reviewing'
                    WHEN EXISTS (SELECT 1 FROM product.draft_items i JOIN engine.jobs j ON j.id = i.analysis_job_id WHERE i.draft_id = d.id AND j.status IN ('queued', 'leased')) THEN 'matching'
                    ELSE 'pending_confirm' END AS state
                FROM product.audit_drafts d
                LEFT JOIN product.audit_rounds r ON r.source_draft_id = d.id
                WHERE d.tenant_id = %s AND d.created_by = %s AND d.task_fingerprint IS NOT NULL
                AND EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id = d.id AND g.user_id = %s AND g.capability IN ('read', 'review'))
                AND (r.id IS NULL OR EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id = r.id AND g.user_id = d.created_by AND g.capability IN ('read', 'review')))
                AND (%s = '' OR EXISTS (SELECT 1 FROM product.draft_items i JOIN product.file_versions f ON f.id = i.file_version_id WHERE i.draft_id = d.id AND f.original_filename ILIKE %s))
                ) SELECT * FROM visible WHERE (%s = '' OR state = %s) ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s""",
                (principal.tenant_id, principal.user_id, principal.user_id, q, needle, state, state, limit, offset),
            ).fetchall()
            if not rows:
                return []
            files = conn.execute(
                """SELECT i.draft_id, i.selection, f.id, f.file_id, f.tenant_id, f.original_filename, f.content_type, f.byte_size, f.sha256, f.object_key, f.status
                FROM product.draft_items i JOIN product.file_versions f ON f.id = i.file_version_id
                WHERE i.draft_id = ANY(%s::uuid[]) ORDER BY i.draft_id, i.position""",
                ([str(row[0]) for row in rows],),
            ).fetchall()
            self._prepared_items(conn, principal, [{"file_version_id": vid, "role": "reference"} for vid in dict.fromkeys(str(file[2]) for file in files)])
            grouped: dict[str, list] = {}
            for file in files:
                grouped.setdefault(str(file[0]), []).append({"file": self._version_from_row(file[2:]).public(), "selection": file[1]})
            histories = {row["id"]: row for row in self._round_histories(conn, [str(row[8]) for row in rows if row[8]])}
            return [
                {
                    "id": str(row[0]),
                    "input_revision": row[1],
                    "analysis_mode": row[2],
                    "created_at": row[3].isoformat(),
                    "updated_at": row[4].isoformat(),
                    "number": f"PPAP-{row[5]:%Y%m%d}-{row[6]:03d}",
                    "completion_reason": row[7] or ("processed" if row[8] and row[9] == "completed" else None),
                    "round_id": str(row[8]) if row[8] else None,
                    "state": row[9],
                    "files": grouped.get(str(row[0]), []),
                    **({key: histories[str(row[8])][key] for key in ("execution", "has_execution_error", "result_counts", "reviewed_count", "uncovered_count")} if row[8] else {}),
                }
                for row in rows
            ]

    def end_unmatched_task(self, principal: Principal, task_id: str, expected_revision: int, removals: list, unselected_file_version_ids: list | None = None) -> dict:
        with self._conn() as conn, conn.transaction():
            draft = self._recognition_owner(conn, principal, task_id, lock=True)
            if type(expected_revision) is not int or expected_revision < 0:
                raise ValueError("invalid_task_revision")
            if draft["input_revision"] != expected_revision:
                raise ValueError("task_revision_conflict")
            task = self._recognition_public(conn, principal, task_id)
            if not isinstance(removals, list):
                raise ValueError("invalid_removal_reason")
            unselected = set()
            if unselected_file_version_ids is not None and not isinstance(unselected_file_version_ids, list):
                raise ValueError("invalid_task_selection")
            for raw_id in unselected_file_version_ids or []:
                vid = _as_uuid(str(raw_id or ""))
                row = next((row for row in task["files"] if row["file"]["id"] == vid), None)
                if not vid or vid in unselected or not row or row["selection"] not in {"selected", "unselected"}:
                    raise ValueError("invalid_task_selection")
                if task["completion_reason"] and row["selection"] != "unselected":
                    raise ValueError("task_input_locked")
                unselected.add(vid)
            removed: dict[str, str] = {}
            for raw in removals:
                if not isinstance(raw, dict):
                    raise ValueError("invalid_removal_reason")
                vid, reason = _as_uuid(str(raw.get("file_version_id") or "")), raw.get("removal_reason")
                if not vid or vid in removed or vid in unselected or not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
                    raise ValueError("invalid_removal_reason")
                row = next((row for row in task["files"] if row["file"]["id"] == vid), None)
                if task["completion_reason"] and row and row["selection"] != "removed":
                    raise ValueError("task_input_locked")
                if not row or not (row["selection"] == "selected" and (row["match"] or {}).get("state") == "unmatched" or row["selection"] == "removed" and row["removal_reason"] == reason.strip()):
                    raise ValueError("task_not_zero_match")
                removed[vid] = reason.strip()
            if task["completion_reason"] == "no_matching_rules":
                return task
            selected = [row for row in task["files"] if row["selection"] == "selected" and row["file"]["id"] not in unselected]
            if task["state"] != "pending_confirm" or any((row["match"] or {}).get("state") not in {"unmatched", "reference"} for row in selected):
                raise ValueError("task_not_zero_match")
            for vid in unselected:
                conn.execute("UPDATE product.draft_items SET selection = 'unselected', removal_reason = NULL WHERE draft_id = %s AND file_version_id = %s", (task_id, vid))
            for vid, reason in removed.items():
                conn.execute("UPDATE product.draft_items SET selection = 'removed', removal_reason = %s WHERE draft_id = %s AND file_version_id = %s", (reason, task_id, vid))
            conn.execute("UPDATE product.audit_drafts SET task_completion_reason = 'no_matching_rules', updated_at = now() WHERE id = %s", (task_id,))
            self._queue_task_notification(conn, task_id, "completed")
            return self._recognition_public(conn, principal, task_id)

    def customer_default(self, principal: Principal) -> dict:
        """Read a member's tenant preference without changing any task input."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT t.default_customer_preference, t.customer_preference_revision
                   FROM product.tenants t JOIN product.memberships m ON m.tenant_id = t.id
                   WHERE t.id = %s AND m.user_id = %s""",
                (principal.tenant_id, principal.user_id),
            ).fetchone()
            if row is None:
                raise KeyError("access_denied")
            return {"customer_preference": row[0], "revision": row[1]}

    def set_customer_default(self, principal: Principal, customer, expected_revision, confirmed) -> dict:
        """Compare-and-swap one tenant default; lost replies are recovered by readback."""
        if customer not in (None, "UAES", "理想", "蔚来") or type(expected_revision) is not int or expected_revision < 0 or confirmed is not True:
            raise ValueError("invalid_customer_default")
        with self._conn() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE product.tenants t SET default_customer_preference = %s,
                       customer_preference_revision = customer_preference_revision + 1
                   WHERE t.id = %s AND t.customer_preference_revision = %s
                     AND EXISTS (SELECT 1 FROM product.memberships m WHERE m.tenant_id = t.id AND m.user_id = %s)
                   RETURNING default_customer_preference, customer_preference_revision""",
                (customer, principal.tenant_id, expected_revision, principal.user_id),
            ).fetchone()
            if row is None:
                if not conn.execute("SELECT 1 FROM product.memberships WHERE tenant_id = %s AND user_id = %s", (principal.tenant_id, principal.user_id)).fetchone():
                    raise KeyError("access_denied")
                raise ValueError("customer_default_conflict")
            return {"customer_preference": row[0], "revision": row[1]}

    def list_rules(self, tenant_id: str) -> list[dict]:
        with self._conn() as conn:
            return self._list_rules(conn, tenant_id)

    @staticmethod
    def _list_rules(conn, tenant_id: str) -> list[dict]:
        rows = conn.execute(
            "SELECT id, title, body, created_at, predecessor_id, case_context FROM product.rule_versions WHERE tenant_id = %s ORDER BY created_at DESC, id DESC",
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
                    "customer_scope": customer_scope(body, row[5]),
                    **{field: body.get(field) for field in ("type", "rule_profile", "customers", "models", "parts")},
                }
            )
        return out

    def list_rule_library(self, tenant_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, title, created_at, predecessor_id, body->'customers', body->'parts', case_context,
                    CASE WHEN jsonb_typeof(body->'rules') = 'array' THEN jsonb_array_length(body->'rules') ELSE 0 END
                FROM product.rule_versions WHERE tenant_id = %s AND body->>'kind' = 'engine' AND source_file_version_id IS NOT NULL
                ORDER BY created_at DESC, id DESC""",
                (tenant_id,),
            ).fetchall()
        versions = []
        for row in rows:
            customers, parts, context = row[4] or [], row[5] or [], row[6] or {}
            try:
                registration = json.loads(context.get("registration") or "{}")
            except (TypeError, ValueError):
                registration = {}
            registration = registration if isinstance(registration, dict) else {}
            stored_scope = registration.get("scope") or {}
            stored_scope = stored_scope if isinstance(stored_scope, dict) else {}
            scope = {"customer": customers[0] if customers else "", "part": parts[0] if parts else "", "revision": "", "allParts": not parts, "allRevisions": True}
            scope.update({key: stored_scope[key] for key in ("customer", "part", "revision") if isinstance(stored_scope.get(key), str)})
            scope.update({key: stored_scope[key] for key in ("allParts", "allRevisions") if isinstance(stored_scope.get(key), bool)})
            scope["allCustomers"] = customer_scope({"customers": customers}, context) == "all"
            basis = registration.get("basis")
            source = basis.get("source") if isinstance(basis, dict) else None
            version = source.get("version") if isinstance(source, dict) else None
            versions.append(
                {
                    "id": str(row[0]),
                    "title": row[1],
                    "version": version if isinstance(version, str) else row[2].isoformat()[:16].replace("T", " "),
                    "scope": scope,
                    "reason": registration.get("reason") if isinstance(registration.get("reason"), str) else "",
                    "created_at": row[2].isoformat(),
                    "predecessor_id": str(row[3]) if row[3] else None,
                    "clause_count": row[7],
                }
            )
        return versions

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
            "customer_scope": customer_scope(row[2], row[8]),
        }

    def publish_rule(self, principal: Principal, title, body, source_file_version_id, predecessor_id=None, request_id=None, case_context=None) -> dict:
        from product.store.custom_suites import publish_custom_rule_suite
        from product.store.suite_releases import _lock, _member

        title, body = rule_title(title), normalize_rule_body(body)
        rule_id = _as_uuid(request_id) if request_id else str(uuid.uuid4())
        if not rule_id:
            raise ValueError("invalid_rule_request")
        context = case_context or {}
        if not isinstance(context, dict) or len(json.dumps(context, ensure_ascii=False).encode()) > 262144:
            raise ValueError("invalid_case_context")
        if "customer_scope" in context and (context["customer_scope"] != "all" or body["customers"]):
            raise ValueError("invalid_rule_scope")
        with self._conn() as conn, conn.transaction():
            _member(conn, principal)
            _lock(conn, principal.tenant_id)
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("rule-publication:" + rule_id,))
            self._prepared_items(conn, principal, [{"file_version_id": source_file_version_id, "role": "reference"}])
            predecessor = _as_uuid(predecessor_id) if predecessor_id else None
            previous = self._rule_for_tenant(conn, predecessor, principal.tenant_id) if predecessor else None
            if predecessor_id and previous is None:
                raise KeyError("missing")
            if previous and (previous[2] or {}).get("rule_profile") == "v4":
                raise ValueError("product_preset_required")
            existing = conn.execute("SELECT tenant_id, title, body, source_file_version_id, predecessor_id, case_context FROM product.rule_versions WHERE id = %s", (rule_id,)).fetchone()
            if existing:
                if str(existing[0]) != principal.tenant_id:
                    raise KeyError("missing")
                if (
                    existing[1] != title
                    or normalize_rule_body(existing[2]) != body
                    or str(existing[3]) != str(source_file_version_id)
                    or (str(existing[4]) if existing[4] else None) != predecessor
                    or existing[5] != context
                ):
                    raise ValueError("idempotency_conflict")
                # Return the original publication, never re-adopt an old head.
            else:
                body = publish_custom_rule_suite(self, conn, principal, rule_id=rule_id, title=title, body=body, previous=previous[2] if previous else None)
                conn.execute(
                    """INSERT INTO product.rule_versions
                        (id, tenant_id, title, body, source_file_version_id, predecessor_id, created_by, case_context)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (rule_id, principal.tenant_id, title, Jsonb(body), source_file_version_id, predecessor, principal.user_id, Jsonb(context)),
                )
        return cast(dict, self.get_rule(principal.tenant_id, rule_id))

    def _reject_task_predecessor(self, conn, predecessor_id: str | None) -> None:
        if (
            predecessor_id
            and conn.execute(
                "SELECT 1 FROM product.audit_rounds r JOIN product.audit_drafts d ON d.id = r.source_draft_id WHERE r.id = %s AND d.task_fingerprint IS NOT NULL", (predecessor_id,)
            ).fetchone()
        ):
            raise ValueError("task_input_locked")

    def _require_preparation_owner(self, conn, principal: Principal, draft_id: str) -> None:
        if not conn.execute(
            """SELECT 1 FROM product.audit_drafts d JOIN product.resources r ON r.id = d.id
            WHERE d.id = %s AND d.tenant_id = %s AND d.created_by = %s
            AND r.tenant_id = d.tenant_id AND r.kind = 'audit_draft'
            AND EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id = d.id
                        AND g.user_id = %s AND g.capability IN ('read', 'review'))""",
            (draft_id, principal.tenant_id, principal.user_id, principal.user_id),
        ).fetchone():
            raise KeyError("missing")

    def _preparation_items(self, conn, principal: Principal, preparation: dict) -> list[dict]:
        prepared = self._prepared_items(conn, principal, [{"file_version_id": row["file_version_id"], "role": "reference"} for row in preparation["files"]])
        for row, source in zip(preparation["files"], prepared):
            if row["validation_id"]:
                validation = self.get_file_validation(principal, row["validation_id"])
                if validation["source_file_version_id"] != row["file_version_id"] or validation["source_file_id"] != source["file_id"]:
                    raise ValueError("precheck_required")
        return prepared

    def create_draft(
        self,
        principal: Principal,
        predecessor_id: str | None = None,
        *,
        request_id: str | None = None,
        items: list[dict] | None = None,
        case_context: dict | None = None,
        upload_preparation: dict | None = None,
    ) -> dict:
        draft_id = str(uuid.uuid4()) if request_id is None else _as_uuid(request_id) if isinstance(request_id, str) else None
        if not draft_id or items is not None and not isinstance(items, list):
            raise ValueError("invalid_draft_request")
        context = {} if case_context is None else case_context
        if not isinstance(context, dict) or PREPARATION_KEY in context or len(json.dumps(context, ensure_ascii=False).encode()) > 262144:
            raise ValueError("invalid_case_context")
        preparation = normalize_preparation(upload_preparation) if upload_preparation is not None else None
        if preparation is not None:
            if items is not None or case_context is not None or predecessor_id or request_id is None:
                raise ValueError("invalid_upload_preparation")
            self._offered_analysis_mode(preparation["analysis_mode"])
            context = {PREPARATION_KEY: preparation}
        if predecessor_id:
            resource = self.resource(predecessor_id)
            if resource is None or resource.kind != "audit_round" or not can_read(principal, resource, self.grants(predecessor_id, principal.user_id)):
                raise KeyError("missing")
            predecessor_id = resource.id
        with self._conn() as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("draft-create:" + draft_id,))
                self._reject_task_predecessor(conn, predecessor_id)
                existing = conn.execute("SELECT tenant_id, created_by, predecessor_id FROM product.audit_drafts WHERE id = %s", (draft_id,)).fetchone()
                if existing:
                    resource = self.resource(draft_id)
                    if (
                        str(existing[0]) != principal.tenant_id
                        or str(existing[1]) != principal.user_id
                        or resource is None
                        or not can_read(principal, resource, self.grants(draft_id, principal.user_id))
                    ):
                        raise KeyError("missing")
                    saved = cast(dict, self._draft_public(conn, draft_id))
                    if preparation is not None:
                        if saved.get("upload_preparation") != preparation or saved.get("task_id"):
                            raise ValueError("idempotency_conflict")
                        return saved
                    if saved.get("upload_preparation") is not None:
                        raise ValueError("idempotency_conflict")
                    if (str(existing[2]) if existing[2] else None) != predecessor_id or case_context is not None and saved["case_context"] != context:
                        raise ValueError("idempotency_conflict")
                    if items is not None:
                        requested = self._bound_audit_items(conn, principal, items, require_rules=False)

                        def binding(rows):
                            return sorted((row["file_version_id"], row["role"], row["rule_version_id"]) for row in rows)

                        if binding(requested) != binding(saved["items"]):
                            raise ValueError("idempotency_conflict")
                    return saved
                if conn.execute("SELECT 1 FROM product.resources WHERE id = %s", (draft_id,)).fetchone():
                    raise KeyError("missing")
                prepared = self._preparation_items(conn, principal, preparation) if preparation is not None else self._bound_audit_items(conn, principal, items or [], require_rules=False)
                mode = preparation["analysis_mode"] if preparation is not None else ANALYSIS_STANDARD
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
                    INSERT INTO product.audit_drafts (id, tenant_id, created_by, status, predecessor_id, analysis_mode, case_context)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (draft_id, principal.tenant_id, principal.user_id, DRAFT_OPEN, predecessor_id, mode, Jsonb(context)),
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
                for row in prepared:
                    conn.execute(
                        "INSERT INTO product.draft_items (draft_id, file_version_id, role, rule_version_id) VALUES (%s, %s, %s, %s)",
                        (draft_id, row["file_version_id"], row["role"], row.get("rule_version_id")),
                    )
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
                     AND (NOT (d.case_context ? '_native_upload_preparation') OR d.created_by = %s)
                     AND (EXISTS (SELECT 1 FROM product.draft_items i WHERE i.draft_id = d.id)
                          OR jsonb_array_length(COALESCE(d.case_context->'_native_upload_preparation'->'uploads', '[]'::jsonb)) > 0)
                     AND (%s = '' OR d.case_context::text ILIKE %s OR EXISTS (
                         SELECT 1 FROM product.draft_items i JOIN product.file_versions f ON f.id = i.file_version_id
                         WHERE i.draft_id = d.id AND f.original_filename ILIKE %s))
                   ORDER BY d.updated_at DESC, d.id DESC LIMIT %s OFFSET %s""",
                (principal.tenant_id, DRAFT_OPEN, principal.user_id, principal.user_id, q, needle, needle, limit, offset),
            ).fetchall()
            return [cast(dict, self._draft_public(conn, row[0])) for row in rows]

    def get_draft(self, draft_id: str, principal: Principal | None = None) -> dict | None:
        parsed = _as_uuid(draft_id)
        if not parsed:
            return None
        with self._conn() as conn:
            draft = self._draft_public(conn, parsed)
            if principal is not None and draft and draft.get("upload_preparation") is not None:
                self._require_preparation_owner(conn, principal, parsed)
            return draft

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
        *,
        upload_preparation: dict | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        parsed = _as_uuid(draft_id)
        if not parsed:
            raise KeyError("missing")
        with self._conn() as conn:
            with conn.transaction():
                draft = conn.execute(
                    """
                    SELECT id, tenant_id, status, task_fingerprint, case_context, input_revision FROM product.audit_drafts
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
                previous_preparation = (draft[4] or {}).get(PREPARATION_KEY)
                if upload_preparation is not None or previous_preparation is not None:
                    self._require_preparation_owner(conn, principal, parsed)
                    if previous_preparation is None or upload_preparation is None or any(value is not None for value in (items, analysis_mode, case_context)):
                        raise ValueError("invalid_upload_preparation")
                    if type(expected_revision) is not int or expected_revision < 0:
                        raise ValueError("invalid_draft_revision")
                    if draft[5] != expected_revision:
                        raise ValueError("draft_revision_conflict")
                    preparation = normalize_preparation(upload_preparation)
                    validate_preparation_update(previous_preparation, preparation)
                    mode = self._offered_analysis_mode(preparation["analysis_mode"])
                    prepared = self._preparation_items(conn, principal, preparation)
                    if writeback_target is not None:
                        # The target belongs to the same revision as the saved
                        # inputs. Promotion holds this row lock too, so a late
                        # save cannot replace the target of an accepted task.
                        self.save_draft_writeback(conn, parsed, normalize_external_ref(writeback_target) if writeback_target else None)
                    conn.execute("DELETE FROM product.draft_items WHERE draft_id = %s", (parsed,))
                    for row in prepared:
                        conn.execute("INSERT INTO product.draft_items (draft_id, file_version_id, role) VALUES (%s, %s, 'reference')", (parsed, row["file_version_id"]))
                    conn.execute(
                        "UPDATE product.audit_drafts SET case_context = %s, input_revision = input_revision + 1, analysis_mode = %s, updated_at = now() WHERE id = %s",
                        (Jsonb({PREPARATION_KEY: preparation}), mode, parsed),
                    )
                    return cast(dict, self._draft_public(conn, parsed))
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
                    if not isinstance(case_context, dict) or PREPARATION_KEY in case_context or len(json.dumps(case_context, ensure_ascii=False).encode()) > 262144:
                        raise ValueError("invalid_case_context")
                    conn.execute("UPDATE product.audit_drafts SET case_context = %s, updated_at = now() WHERE id = %s", (Jsonb(case_context), parsed))
                return cast(dict, self._draft_public(conn, parsed))

    def accept_draft(self, principal: Principal, draft_id: str, idempotency_key: str, predecessor_id: str | None = None, *, usage_policy=None) -> dict:
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
                return self._accept_locked(conn, principal, key, source_draft_id=parsed, predecessor_id=predecessor_id, items=None, usage_policy=usage_policy)

    def resubmit_round(
        self,
        principal: Principal,
        round_id: str,
        idempotency_key: str,
        items: list[dict] | None,
        *,
        usage_policy=None,
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
                self._reject_task_predecessor(conn, parsed)
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
                    usage_policy=usage_policy,
                )

    def read_deep_feedback(self, principal: Principal, round_id: str) -> dict:
        from .deep_feedback import read_feedback

        return read_feedback(self, principal, round_id)

    def apply_rule_change(self, principal: Principal, round_id: str, payload: dict, *, usage_policy=None) -> dict:
        from .deep_feedback import apply_change

        return apply_change(self, principal, round_id, payload, usage_policy=usage_policy)

    def read_rule_change(self, principal: Principal, round_id: str, operation_id: str) -> dict | None:
        from .deep_feedback import read_change

        return read_change(self, principal, round_id, operation_id)

    def export_deep_feedback(self, principal: Principal, round_id: str) -> dict:
        from .deep_feedback import export_feedback

        return export_feedback(self, principal, round_id)

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

    def get_round(self, round_id: str, *, read_view: bool = False, principal: Principal | None = None) -> dict | None:
        parsed = _as_uuid(round_id)
        if not parsed:
            return None
        with self._conn() as conn:
            if read_view:
                with conn.transaction():
                    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    return self._round_public(conn, parsed, read_view=True, principal=principal)
            return self._round_public(conn, parsed)

    def list_rounds(self, principal: Principal, limit: int = 50, offset: int = 0, q: str = "", execution: str = "", unreviewed: bool = False) -> list[dict]:
        needle = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT r.id, GREATEST(r.created_at,
                     (SELECT max(j.updated_at) FROM engine.jobs j WHERE j.audit_round_id = r.id),
                     (SELECT max(v.updated_at) FROM product.reviews v WHERE v.round_id = r.id)) AS activity_at
                   FROM product.audit_rounds r
                   LEFT JOIN product.audit_drafts d ON d.id = r.source_draft_id
                   WHERE r.tenant_id = %s
                     AND ((d.task_fingerprint IS NULL AND NOT EXISTS (SELECT 1 FROM product.deep_rule_changes c WHERE c.analysis_round_id=r.id)) OR EXISTS (SELECT 1 FROM product.resource_grants g
                         WHERE g.resource_id = r.id AND g.user_id = %s AND g.capability IN ('read', 'review')))
                     AND (%s = '' OR r.execution = %s)
                     AND (NOT %s OR EXISTS (
                         SELECT 1 FROM product.findings f WHERE f.round_id = r.id
                         AND NOT EXISTS (SELECT 1 FROM product.reviews v WHERE v.round_id = f.round_id AND v.item_key = f.item_key)))
                     AND (%s = '' OR r.case_context::text ILIKE %s OR EXISTS (
                         SELECT 1 FROM product.round_inputs i JOIN product.file_versions f ON f.id = i.file_version_id
                         WHERE i.round_id = r.id AND (f.original_filename ILIKE %s OR i.rule_title ILIKE %s)))
                   ORDER BY activity_at DESC, r.id DESC LIMIT %s OFFSET %s""",
                (principal.tenant_id, principal.user_id, execution, execution, unreviewed, q, needle, needle, needle, limit, offset),
            ).fetchall()
            return self._round_histories(conn, [str(row[0]) for row in rows])

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

        from product.bundles import archive_member_names

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

    def save_review(
        self,
        principal: Principal,
        round_id: str,
        item_key: str,
        judgment: str,
        rationale: str,
        next_step: str,
        version: int,
        citations: list[dict] | None = None,
        *,
        mcp_operation_id: str | None = None,
    ) -> dict:
        text, why, nxt, seen = normalize_review(judgment, rationale, next_step, version)
        parsed = _as_uuid(round_id)
        key = str(item_key or "").strip()
        if not parsed or not key:
            raise KeyError("missing")
        with self._conn() as conn:
            with conn.transaction():
                if mcp_operation_id is not None:
                    # This identity arrives only through the in-process adapter.
                    # The review and its transport receipt commit together, so a
                    # process loss cannot turn a saved review into a version conflict.
                    operation = conn.execute(
                        """SELECT o.arguments,o.state,o.result,o.confirmed_by FROM product.mcp_operations o
                           JOIN product.mcp_grants g ON g.id=o.grant_id
                           WHERE o.id=%s AND o.tool='submit_review' AND g.user_id=%s AND g.tenant_id=%s
                           AND g.revoked_at IS NULL AND g.expires_at>now() AND 'reviews:write'=ANY(g.scopes)
                           FOR UPDATE OF o""",
                        (mcp_operation_id, principal.user_id, principal.tenant_id),
                    ).fetchone()
                    command = {"item_key": item_key, "judgment": judgment, "rationale": rationale, "next_step": next_step, "version": version}
                    if citations is not None:
                        command["citations"] = citations
                    if not operation or str(operation[3]) != principal.user_id or operation[0].get("path") != f"/rounds/{round_id}/reviews" or operation[0].get("body") != command:
                        raise ValueError("invalid_review_confirmation")
                    if operation[1] == "succeeded":
                        return operation[2]["data"]
                    if operation[1] != "running":
                        raise ValueError("invalid_review_confirmation")
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
                result = cast(dict, self._round_public(conn, parsed))
                if mcp_operation_id is not None:
                    conn.execute(
                        """UPDATE product.mcp_operations SET state='succeeded',updated_at=now(),result=%s
                                    WHERE id=%s AND state='running'""",
                        (Jsonb({"data": result, "http_status": 200, "probes": [f"/rounds/{round_id}"]}), mcp_operation_id),
                    )
                return result

    def round_work(self, round_id: str) -> RoundWork | None:
        parsed = _as_uuid(round_id)
        if not parsed:
            return None
        with self._conn() as conn:
            head = conn.execute(
                """
                SELECT id, tenant_id, result_attempt, analysis_mode, created_by
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
            str(head[4]) if head[4] else "",
        )

    def claim_job(self, worker_ident: str) -> ClaimedJob | None:
        with self._conn() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    SELECT id, kind, payload, tenant_id FROM engine.jobs
                    WHERE kind <> ALL(%s) AND (
                        (status = 'queued' AND (lease_until IS NULL OR lease_until < now()))
                        OR (status = 'leased' AND kind <> ALL(%s) AND lease_until IS NOT NULL AND lease_until < now())
                    )
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    (["feishu_notification", "task_confirmation"], [*FILE_PREPARATION_KINDS, "checker_optimization"]),
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
                if round_id and row[1] == "audit":
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
                  AND (kind <> 'audit' OR lease_until > clock_timestamp())
                  AND (kind NOT IN ('task_precheck','checker_optimization') OR lease_until > now()) RETURNING id""",
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
                     AND j.status = 'leased' AND j.lease_until > clock_timestamp()
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
                task_id = claimed.payload.get("task_id") if claimed.kind in {"file_analysis", "report_pdf", "report_zip"} else None
                if claimed.kind == "audit" and claimed.round_id:
                    source = conn.execute("SELECT source_draft_id FROM product.audit_rounds WHERE id = %s", (claimed.round_id,)).fetchone()
                    task_id = str(source[0]) if source and source[0] else None
                if task_id:
                    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("task-notification:" + task_id,))
                won = conn.execute(
                    """
                    UPDATE engine.jobs
                    SET status = %s, lease_until = NULL, updated_at = now(),
                        result = CASE WHEN kind = ANY(%s) THEN %s ELSE result END
                    WHERE id = %s AND attempt = %s AND lease_owner = %s AND status = 'leased'
                      AND (kind <> 'audit' OR lease_until > clock_timestamp())
                      AND (kind NOT IN ('task_precheck','checker_optimization') OR lease_until > now())
                    RETURNING id
                    """,
                    (
                        "succeeded" if outcome == "succeeded" else "failed",
                        [*FILE_PREPARATION_KINDS, "report_pdf", "report_zip", "task_precheck"],
                        Jsonb(result) if claimed.kind in {*FILE_PREPARATION_KINDS, "report_pdf", "report_zip", "task_precheck"} else None,
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
                if claimed.kind != "audit" or not claimed.round_id:
                    if (
                        task_id
                        and claimed.kind == "file_analysis"
                        and not conn.execute(
                            """SELECT 1 FROM product.draft_items i LEFT JOIN engine.jobs j ON j.id = i.analysis_job_id
                        WHERE i.draft_id = %s AND i.selection = 'selected' AND (j.status IS NULL OR j.status IN ('queued', 'leased'))""",
                            (task_id,),
                        ).fetchone()
                    ):
                        self._queue_task_notification(conn, task_id, "confirmation")
                        try:
                            with conn.transaction():
                                self._queue_recognized_precheck(conn, task_id)
                        except Exception as exc:
                            emit("task_precheck_not_queued", task_id=task_id, error_type=type(exc).__name__)
                    if task_id and claimed.kind == "report_pdf" and not claimed.payload.get("regeneration_request_id"):
                        self._queue_finished_notification(conn, task_id, claimed.payload["source_round_id"])
                    if task_id and claimed.kind == "report_zip" and outcome == "succeeded":
                        self._queue_task_notification(conn, task_id, "zip_ready", claimed.round_id, package_id=claimed.id)
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
                if outcome == "succeeded":
                    self._queue_file_reports(conn, claimed, result)
                if task_id:
                    self._queue_finished_notification(conn, task_id, claimed.round_id)
                return True

    def _queue_file_reports(self, conn, claimed: ClaimedJob, result: dict) -> None:
        head = conn.execute("SELECT source_draft_id, analysis_mode FROM product.audit_rounds WHERE id = %s AND tenant_id = %s", (claimed.round_id, claimed.tenant_id)).fetchone()
        if not head or not head[0] or not conn.execute("SELECT id FROM product.audit_drafts WHERE id = %s AND task_fingerprint IS NOT NULL", (head[0],)).fetchone():
            return
        rows = conn.execute(
            """SELECT i.file_version_id, i.role, i.rule_version_id, i.rule_title, i.rule_body, v.original_filename
            FROM product.round_inputs i JOIN product.file_versions v ON v.id = i.file_version_id WHERE i.round_id = %s""",
            (claimed.round_id,),
        ).fetchall()
        inputs = [{"file_version_id": str(row[0]), "role": row[1], "rule": {"id": str(row[2]), "title": row[3], "body": row[4]} if row[2] else None} for row in rows]
        round = {
            "id": claimed.round_id,
            "analysis_mode": head[1],
            "execution": "completed",
            "result_attempt": claimed.attempt,
            "result_job_id": claimed.id,
            "inputs": inputs,
            "coverage": result.get("coverage") or {},
            "findings": result.get("findings") or [],
            **_executor_public(claimed.payload),
        }
        originals = {str(row[0]): row[5] for row in rows}
        for frozen in inputs:
            if frozen["role"] != "target" or not frozen["rule"]:
                continue
            audit = file_round_audit(round, frozen)
            if not report_eligible(audit["checks"], audit["execution"] != "completed", audit["unmapped_findings"]):
                continue
            audit = deepcopy(audit)
            audit.pop("report")
            for check in audit["checks"]:
                check.pop("review", None)
                check.pop("review_item_key", None)
                check["finding"].pop("review", None)
            payload = {
                "task_id": str(head[0]),
                "source_round_id": claimed.round_id,
                "source_file_version_id": frozen["file_version_id"],
                "result_attempt": claimed.attempt,
                "report_input": {"filename": originals[frozen["file_version_id"]], "originals": originals, "audit": audit},
            }
            conn.execute(
                """INSERT INTO engine.jobs (id, tenant_id, audit_round_id, kind, payload, status)
                VALUES (%s, %s, %s, 'report_pdf', %s, 'queued') ON CONFLICT DO NOTHING""",
                (uuid.uuid4(), claimed.tenant_id, claimed.round_id, Jsonb(payload)),
            )

    def _task_file_reports(self, conn, tenant_id, task_id, round_id, result_attempt) -> tuple[dict, datetime | None]:
        rows = conn.execute(
            """SELECT id, payload, status, result, updated_at FROM engine.jobs
            WHERE tenant_id = %s AND audit_round_id = %s AND kind = 'report_pdf' AND payload->>'task_id' = %s
              AND payload->>'result_attempt' = %s""",
            (tenant_id, round_id, task_id, str(result_attempt)),
        ).fetchall()
        reports = {}
        for row in rows:
            state = {"queued": "pending", "leased": "generating", "succeeded": "ready"}.get(row[2], "failed")
            result = row[3] or {}
            if state == "ready" and not result.get("artifact"):
                state = "failed"
            reports[row[1]["source_file_version_id"]] = {
                "state": state,
                "report_id": str(row[0]),
                "reason": "报告生成失败。审核结果已保存。" if state == "failed" else None,
                "artifact": result.get("artifact") if state == "ready" else None,
                "generated_at": result.get("generated_at") if state == "ready" else None,
                "regenerate_allowed": state == "failed" and row[2] == "failed",
                "regenerate_reason": "重试前将重新检查可用额度，不产生新的审核费用。" if self.settings.usage_charge_enabled else None,
                **({"generation_request": {"request_id": row[1]["regeneration_request_id"], "state": "accepted", "reason": None}} if row[1].get("regeneration_request_id") else {}),
            }
        return reports, max((row[4] for row in rows), default=None)

    def _report_recovery_context(self, conn, principal: Principal, reference: dict, *, lock: bool = False) -> tuple[dict, str]:
        if (
            not isinstance(reference, dict)
            or set(reference) != {"task_id", "round_id", "file_version_id", "result_attempt", "report_id"}
            or any(not isinstance(reference.get(key), str) or _as_uuid(reference[key]) != reference[key] for key in ("task_id", "round_id", "file_version_id", "report_id"))
            or type(reference.get("result_attempt")) is not int
            or reference["result_attempt"] < 1
        ):
            raise ValueError("invalid_report_regeneration")
        self._recognition_owner(conn, principal, reference["task_id"], lock=lock)
        if lock:
            grants = conn.execute(
                """SELECT resource_id FROM product.resource_grants WHERE resource_id = ANY(%s::uuid[])
                AND user_id = %s AND capability IN ('read', 'review') ORDER BY resource_id FOR SHARE""",
                ([reference["task_id"], reference["round_id"]], principal.user_id),
            ).fetchall()
            if {str(row[0]) for row in grants} != {reference["task_id"], reference["round_id"]}:
                raise KeyError("missing")
        row = conn.execute(
            "SELECT payload, status FROM engine.jobs WHERE id = %s AND tenant_id = %s AND kind = 'report_pdf'" + (" FOR UPDATE" if lock else ""),
            (reference["report_id"], principal.tenant_id),
        ).fetchone()
        if not row:
            raise KeyError("missing")
        payload, state = row
        expected = {
            "task_id": payload["task_id"],
            "round_id": payload["source_round_id"],
            "file_version_id": payload["source_file_version_id"],
            "result_attempt": payload["result_attempt"],
            "report_id": reference["report_id"],
        }
        task = self._recognition_public(conn, principal, reference["task_id"])
        file = next((file for file in task["files"] if file["file"]["id"] == reference["file_version_id"] and file.get("audit")), None)
        if (
            expected != reference
            or task["round_id"] != reference["round_id"]
            or file is None
            or file["audit"]["result_attempt"] != reference["result_attempt"]
            or file["audit"]["report"]["report_id"] != reference["report_id"]
        ):
            raise KeyError("missing")
        return file["audit"]["report"], state

    def _report_recovery_receipt(self, conn, principal: Principal, reference: dict, request_id: str) -> bool:
        row = conn.execute("SELECT tenant_id, kind, payload FROM engine.jobs WHERE id = %s", (request_id,)).fetchone()
        if row is None:
            return False
        if str(row[0]) != principal.tenant_id or row[1] != "report_pdf_request" or row[2].get("created_by") != principal.user_id:
            raise KeyError("missing")
        if row[2].get("reference") != reference:
            raise ValueError("idempotency_conflict")
        return True

    def regenerate_audit_report(self, principal: Principal, reference: dict, request_id: str, *, usage_policy=None) -> dict:
        if not isinstance(request_id, str) or _as_uuid(request_id) != request_id:
            raise ValueError("invalid_report_regeneration")
        with self._conn() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("report-recovery:" + request_id,))
            report, state = self._report_recovery_context(conn, principal, reference, lock=True)
            if not self._report_recovery_receipt(conn, principal, reference, request_id):
                if state != "failed" or report["state"] != "failed":
                    raise ValueError("report_recovery_unavailable")
                self._check_usage_policy(conn, principal, usage_policy)
                conn.execute(
                    """UPDATE engine.jobs SET status = 'queued', result = NULL, lease_owner = NULL, lease_until = NULL,
                    payload = payload || %s, updated_at = now() WHERE id = %s""",
                    (Jsonb({"regeneration_request_id": request_id}), reference["report_id"]),
                )
                conn.execute(
                    """INSERT INTO engine.jobs (id, tenant_id, audit_round_id, kind, payload, status)
                    VALUES (%s, %s, %s, 'report_pdf_request', %s, 'succeeded')""",
                    (request_id, principal.tenant_id, reference["round_id"], Jsonb({"created_by": principal.user_id, "reference": reference})),
                )
                report, _state = self._report_recovery_context(conn, principal, reference)
            return {"reference": reference, "receipt": {"request_id": request_id, "state": "accepted", "reason": None}, "report": report}

    def read_audit_report_regeneration(self, principal: Principal, reference: dict, request_id: str) -> dict | None:
        if not isinstance(request_id, str) or _as_uuid(request_id) != request_id:
            raise ValueError("invalid_report_regeneration")
        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            report, _state = self._report_recovery_context(conn, principal, reference)
            if not self._report_recovery_receipt(conn, principal, reference, request_id):
                return None
            return {"reference": reference, "receipt": {"request_id": request_id, "state": "accepted", "reason": None}, "report": report}

    def write_audit_report(self, claimed: ClaimedJob, body: bytes, generated_at: str) -> dict:
        if claimed.kind != "report_pdf" or not claimed.tenant_id or not body.startswith(b"%PDF-") or not 0 < len(body) <= 64 * 1024 * 1024:
            raise ValueError("invalid_report_pdf")
        suffix = "-审核报告.pdf"
        filename = original_filename(claimed.payload["report_input"]["filename"])[: 255 - len(suffix)] + suffix
        return self._write_report_artifact(claimed, body, generated_at, filename, "application/pdf")

    def _write_report_artifact(self, claimed: ClaimedJob, body: bytes, generated_at: str, filename: str, content_type: str) -> dict:
        extension = "pdf" if content_type == "application/pdf" else "zip"
        key = f"reports/{claimed.tenant_id}/{claimed.id}/{claimed.attempt}.{extension}"
        digest = sha256_bytes(body)
        try:
            self.objects.put_exclusive(key, body, content_type)
        except ObjectExists:
            pass
        observed = self.objects.get(key)
        if len(observed) != len(body) or sha256_bytes(observed) != digest:
            raise ValueError("report_artifact_conflict")
        return {
            "artifact": {
                "artifact_id": f"{claimed.id}:{claimed.attempt}",
                "filename": filename,
                "content_type": content_type,
                "byte_size": len(body),
            },
            "object_key": key,
            "sha256": digest,
            "generated_at": generated_at,
        }

    def _report_source(self, conn, principal: Principal, task: dict, reference: dict, artifact_id: str | None = None) -> dict:
        row = conn.execute("SELECT payload, result, status FROM engine.jobs WHERE id = %s AND tenant_id = %s AND kind = 'report_pdf'", (reference["report_id"], principal.tenant_id)).fetchone()
        return self._frozen_report_source(task, reference, row, artifact_id)

    @staticmethod
    def _frozen_report_source(task: dict, reference: dict, row, artifact_id: str | None = None) -> dict:
        if not row:
            raise KeyError("missing")
        payload, result, state = row
        expected = {
            "report_id": reference["report_id"],
            "task_id": payload["task_id"],
            "round_id": payload["source_round_id"],
            "file_version_id": payload["source_file_version_id"],
            "result_attempt": payload["result_attempt"],
        }
        file = next((file for file in task["files"] if file["file"]["id"] == expected["file_version_id"] and file.get("audit")), None)
        if reference != expected or task["id"] != expected["task_id"] or task["round_id"] != expected["round_id"] or file is None or file["audit"]["result_attempt"] != expected["result_attempt"]:
            raise KeyError("missing")
        artifact = (result or {}).get("artifact")
        if artifact and artifact_id is not None and artifact["artifact_id"] != artifact_id:
            raise KeyError("missing")
        if state != "succeeded" or not artifact:
            raise ValueError("report_not_ready")
        return {
            "reference": reference,
            "artifact": artifact,
            "object_key": result["object_key"],
            "sha256": result["sha256"],
            "rule": {key: file["audit"]["rule"][key] for key in ("id", "title")},
            "generated_at": result["generated_at"],
        }

    def _report_package_sources(self, conn, principal, reports, task):
        tasks = {task["id"]: task}
        rows = conn.execute(
            "SELECT id, payload, result, status FROM engine.jobs WHERE id = ANY(%s::uuid[]) AND tenant_id = %s AND kind = 'report_pdf'",
            ([reference["report_id"] for reference in reports], principal.tenant_id),
        ).fetchall()
        reports_by_id = {str(row[0]): row[1:] for row in rows}
        sources = []
        for reference in sorted(reports, key=lambda reference: (reference["file_version_id"], reference["report_id"])):
            task_id = reference["task_id"]
            if task_id not in tasks:
                tasks[task_id] = self._recognition_public(conn, principal, task_id)
            sources.append(self._frozen_report_source(tasks[task_id], reference, reports_by_id.get(reference["report_id"])))
        return sources

    def accept_report_package(self, principal: Principal, body: dict) -> dict:
        request_id, reports = body.get("request_id"), body.get("reports")
        if (
            set(body) != {"request_id", "reports"}
            or not isinstance(request_id, str)
            or _as_uuid(request_id) != request_id
            or not isinstance(reports, list)
            or not 2 <= len(reports) <= 100
            or any(
                not isinstance(reference, dict)
                or set(reference) != {"task_id", "round_id", "file_version_id", "result_attempt", "report_id"}
                or any(not isinstance(reference.get(key), str) or _as_uuid(reference[key]) != reference[key] for key in ("task_id", "round_id", "file_version_id", "report_id"))
                or type(reference.get("result_attempt")) is not int
                or reference["result_attempt"] < 1
                for reference in reports
            )
            or len({tuple(reference[key] for key in ("task_id", "round_id", "file_version_id", "result_attempt", "report_id")) for reference in reports}) != len(reports)
        ):
            raise ValueError("invalid_report_package")
        task_id = min(reference["task_id"] for reference in reports)
        with self._conn() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("report-package:" + request_id,))
            task = self._recognition_public(conn, principal, task_id)
            request = {"created_by": principal.user_id, "task_id": task_id, "reports": reports}
            existing = conn.execute("SELECT tenant_id, kind, payload FROM engine.jobs WHERE id = %s", (request_id,)).fetchone()
            if existing:
                if str(existing[0]) != principal.tenant_id or existing[1] != "report_zip_request" or existing[2].get("created_by") != principal.user_id:
                    raise KeyError("missing")
                if {key: existing[2][key] for key in request} != request:
                    raise ValueError("idempotency_conflict")
                return self._report_package_public(conn, principal, task, request_id, existing[2])
            sources = self._report_package_sources(conn, principal, reports, task)
            if sum(source["artifact"]["byte_size"] for source in sources) > MAX_UPLOAD_BYTES:
                raise ValueError("report_package_too_large")
            frozen = {"created_by": principal.user_id, "task_id": task_id, "sources": sources}
            fingerprint = sha256_bytes(json.dumps(frozen, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
            package_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ppap-report-zip:{principal.tenant_id}:{fingerprint}"))
            if request_id == package_id:
                raise ValueError("idempotency_conflict")
            conn.execute(
                """INSERT INTO engine.jobs (id, tenant_id, audit_round_id, kind, payload, status)
                VALUES (%s, %s, %s, 'report_zip', %s, 'queued') ON CONFLICT (id) DO NOTHING""",
                (package_id, principal.tenant_id, task["round_id"], Jsonb(frozen)),
            )
            canonical = conn.execute("SELECT tenant_id, kind, payload FROM engine.jobs WHERE id = %s", (package_id,)).fetchone()
            if not canonical or str(canonical[0]) != principal.tenant_id or canonical[1] != "report_zip" or canonical[2] != frozen:
                raise ValueError("idempotency_conflict")
            request["package_id"] = package_id
            conn.execute(
                """INSERT INTO engine.jobs (id, tenant_id, audit_round_id, kind, payload, status)
                VALUES (%s, %s, %s, 'report_zip_request', %s, 'succeeded') ON CONFLICT (id) DO NOTHING""",
                (request_id, principal.tenant_id, task["round_id"], Jsonb(request)),
            )
            receipt = conn.execute("SELECT tenant_id, kind, payload FROM engine.jobs WHERE id = %s", (request_id,)).fetchone()
            if not receipt or str(receipt[0]) != principal.tenant_id or receipt[1] != "report_zip_request" or receipt[2] != request:
                raise ValueError("idempotency_conflict")
            return self._report_package_public(conn, principal, task, request_id, request)

    def retry_report_package(self, principal: Principal, source_request_id: str, body: dict, *, usage_policy=None) -> dict:
        if (
            set(body) != {"request_id", "package_id", "expected_attempt"}
            or any(not isinstance(value, str) or _as_uuid(value) != value for value in (source_request_id, body.get("request_id"), body.get("package_id")))
            or type(body.get("expected_attempt")) is not int
            or body["expected_attempt"] < 1
        ):
            raise ValueError("invalid_report_package_recovery")
        request_id = body["request_id"]
        with self._conn() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("report-package:" + request_id,))
            source = conn.execute("SELECT tenant_id, kind, payload FROM engine.jobs WHERE id = %s", (source_request_id,)).fetchone()
            if not source or str(source[0]) != principal.tenant_id or source[1] != "report_zip_request" or source[2].get("created_by") != principal.user_id:
                raise KeyError("missing")
            original = source[2]
            task_ids = sorted({reference["task_id"] for reference in original["reports"]})
            for task_id in task_ids:
                self._recognition_owner(conn, principal, task_id, lock=True)
            task = self._recognition_public(conn, principal, original["task_id"])
            resources = task_ids + sorted({reference["round_id"] for reference in original["reports"]})
            grants = conn.execute(
                """SELECT resource_id FROM product.resource_grants WHERE resource_id = ANY(%s::uuid[])
                AND user_id = %s AND capability IN ('read', 'review') ORDER BY resource_id FOR SHARE""",
                (resources, principal.user_id),
            ).fetchall()
            if {str(row[0]) for row in grants} != set(resources) or original["package_id"] != body["package_id"]:
                raise KeyError("missing")
            row = conn.execute(
                "SELECT status, attempt FROM engine.jobs WHERE id = %s AND tenant_id = %s AND kind = 'report_zip' FOR UPDATE",
                (body["package_id"], principal.tenant_id),
            ).fetchone()
            if not row:
                raise KeyError("missing")
            request = {**original, "retry_of_request_id": source_request_id, "expected_attempt": body["expected_attempt"]}
            existing = conn.execute("SELECT tenant_id, kind, payload FROM engine.jobs WHERE id = %s", (request_id,)).fetchone()
            if existing:
                if str(existing[0]) != principal.tenant_id or existing[1] != "report_zip_request" or existing[2].get("created_by") != principal.user_id:
                    raise KeyError("missing")
                if existing[2] != request:
                    raise ValueError("idempotency_conflict")
                return self._report_package_public(conn, principal, task, request_id, request)
            self._report_package_public(conn, principal, task, source_request_id, original)
            if row != ("failed", body["expected_attempt"]):
                raise ValueError("report_package_recovery_unavailable")
            self._check_usage_policy(conn, principal, usage_policy)
            conn.execute(
                "UPDATE engine.jobs SET status = 'queued', result = NULL, lease_owner = NULL, lease_until = NULL, updated_at = now() WHERE id = %s",
                (body["package_id"],),
            )
            conn.execute(
                """INSERT INTO engine.jobs (id, tenant_id, audit_round_id, kind, payload, status)
                VALUES (%s, %s, %s, 'report_zip_request', %s, 'succeeded')""",
                (request_id, principal.tenant_id, task["round_id"], Jsonb(request)),
            )
            return self._report_package_public(conn, principal, task, request_id, request)

    def _report_package_public(self, conn, principal, task, request_id, request) -> dict:
        row = conn.execute("SELECT payload, status, result, attempt FROM engine.jobs WHERE id = %s AND tenant_id = %s AND kind = 'report_zip'", (request["package_id"], principal.tenant_id)).fetchone()
        if not row or request["created_by"] != principal.user_id or row[0]["created_by"] != principal.user_id or row[0]["task_id"] != task["id"]:
            raise KeyError("missing")
        frozen, status, result, attempt = row
        sources = self._report_package_sources(conn, principal, request["reports"], task)
        if sources != frozen["sources"]:
            raise ValueError("report_artifact_conflict")
        result = result or {}
        state = {"queued": "pending", "leased": "generating", "succeeded": "ready"}.get(status, "failed")
        outcomes = result.get("outcomes") or [{"reference": reference, "state": "pending", "reason": None} for reference in request["reports"]]
        return {
            "package_id": request["package_id"],
            "attempt": attempt,
            "reports": request["reports"],
            "receipt": {"request_id": request_id, "state": "accepted", "reason": None},
            "state": state,
            "outcomes": [
                {**next(item for item in outcomes if item["reference"] == reference), "filename": next(source["artifact"]["filename"] for source in sources if source["reference"] == reference)}
                for reference in request["reports"]
            ],
            "artifact": result.get("artifact") if state == "ready" else None,
            "reason": ("ZIP 打包失败，已就绪的 PDF 不受影响。" + ("重试前将重新检查可用额度，不产生新的审核费用。" if self.settings.usage_charge_enabled else "")) if state == "failed" else None,
            "retry_allowed": status == "failed",
        }

    def get_report_package(self, principal: Principal, request_id: str) -> dict | None:
        if _as_uuid(request_id) != request_id:
            raise KeyError("missing")
        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = conn.execute("SELECT tenant_id, kind, payload FROM engine.jobs WHERE id = %s", (request_id,)).fetchone()
            if not row:
                return None
            if str(row[0]) != principal.tenant_id or row[1] != "report_zip_request" or row[2]["created_by"] != principal.user_id:
                raise KeyError("missing")
            task = self._recognition_public(conn, principal, row[2]["task_id"])
            return self._report_package_public(conn, principal, task, request_id, row[2])

    def build_report_package(self, claimed: ClaimedJob, generated_at: str, cancelled) -> dict:
        import tempfile
        from zipfile import ZIP_STORED, ZipFile

        if claimed.kind != "report_zip" or not claimed.tenant_id:
            raise ValueError("invalid_report_package")
        outcomes = [{"reference": source["reference"], "state": "pending", "reason": None} for source in claimed.payload["sources"]]
        manifest: list[dict] = []
        try:
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as target:
                with ZipFile(target, "w", compression=ZIP_STORED) as archive:
                    for index, source in enumerate(claimed.payload["sources"], 1):
                        if cancelled.is_set():
                            raise ValueError("report_lease_unknown")
                        try:
                            body = self.objects.get(source["object_key"])
                            if not body.startswith(b"%PDF-") or len(body) != source["artifact"]["byte_size"] or sha256_bytes(body) != source["sha256"]:
                                raise ValueError("report_artifact_conflict")
                        except Exception:
                            outcomes[index - 1] = {"reference": source["reference"], "state": "failed", "reason": "PDF 未能读取或校验，未打入 ZIP。"}
                            continue
                        outcomes[index - 1] = {"reference": source["reference"], "state": "prepared", "reason": None}
                        prefix = f"{index:03d}-"
                        stem = source["artifact"]["filename"][:-4].encode()[: 255 - len(prefix) - 4].decode("utf-8", "ignore")
                        name = prefix + stem + ".pdf"
                        archive.writestr(name, body)
                        manifest.append({"archive_name": name, "filename": source["artifact"]["filename"], **{key: source[key] for key in ("reference", "rule", "generated_at", "sha256")}})
                    archive.writestr("报告清单.txt", json.dumps({"reports": manifest}, ensure_ascii=False, indent=2).encode())
                if any(item["state"] == "failed" for item in outcomes):
                    return {"outcomes": outcomes, "error": "report_package_source_failed"}
                target.seek(0)
                body = target.read(MAX_UPLOAD_BYTES + 1)
                if len(body) > MAX_UPLOAD_BYTES or cancelled.is_set():
                    raise ValueError("report_package_unavailable")
                return {**self._write_report_artifact(claimed, body, generated_at, "审核报告.zip", "application/zip"), "outcomes": outcomes}
        except Exception:
            return {"outcomes": outcomes, "error": "report_package_artifact_failed"}

    def report_package_zip(self, principal: Principal, request_id: str, package_id: str, artifact_id: str) -> tuple[dict, bytes]:
        package = self.get_report_package(principal, request_id)
        if not package or package["package_id"] != package_id or package["artifact"] and package["artifact"]["artifact_id"] != artifact_id:
            raise KeyError("missing")
        if package["state"] != "ready" or not package["artifact"]:
            raise ValueError("report_not_ready")
        with self._conn() as conn:
            row = conn.execute("SELECT result FROM engine.jobs WHERE id = %s AND tenant_id = %s AND kind = 'report_zip'", (package_id, principal.tenant_id)).fetchone()
        if not row or row[0]["artifact"] != package["artifact"]:
            raise ValueError("report_artifact_conflict")
        body = self.objects.get(row[0]["object_key"])
        if not body.startswith(b"PK") or len(body) != package["artifact"]["byte_size"] or sha256_bytes(body) != row[0]["sha256"]:
            raise ValueError("report_artifact_conflict")
        return package["artifact"], body

    def audit_report_pdf(self, principal: Principal, reference: dict, artifact_id: str) -> tuple[dict, bytes]:
        report_id = _as_uuid(reference["report_id"])
        if report_id is None or report_id != reference["report_id"]:
            raise KeyError("missing")
        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            draft = self._recognition_owner(conn, principal, reference["task_id"])
            task_id = str(draft["id"])
            # Downloads retain the Task's complete file-access checks, but do not
            # need recognition, reviews, display edits, ZIPs or sharing projections.
            files = conn.execute(
                """SELECT i.file_version_id FROM product.draft_items i
                JOIN product.file_versions f ON f.id = i.file_version_id
                WHERE i.draft_id = %s ORDER BY i.position""",
                (task_id,),
            ).fetchall()
            self._prepared_items(conn, principal, [{"file_version_id": str(row[0]), "role": "reference"} for row in files])
            rows = conn.execute(
                """WITH current_round AS (
                    SELECT id, result_attempt FROM product.audit_rounds
                    WHERE source_draft_id = %s AND tenant_id = %s ORDER BY created_at LIMIT 1
                )
                SELECT r.id, r.result_attempt, i.file_version_id, i.rule_version_id, i.rule_title
                FROM current_round r JOIN product.round_inputs i ON i.round_id = r.id
                JOIN product.draft_items d ON d.draft_id = %s AND d.file_version_id = i.file_version_id
                WHERE i.role = 'target' AND i.rule_version_id IS NOT NULL AND d.selection = 'selected'
                AND EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id = r.id
                    AND g.user_id = %s AND g.capability IN ('read', 'review'))""",
                (task_id, principal.tenant_id, task_id, principal.user_id),
            ).fetchall()
            if not rows:
                raise KeyError("missing")
            task = {
                "id": task_id,
                "round_id": str(rows[0][0]),
                "files": [{"file": {"id": str(row[2])}, "audit": {"result_attempt": row[1], "rule": {"id": str(row[3]), "title": row[4]}}} for row in rows],
            }
            source = self._report_source(conn, principal, task, reference, artifact_id)
        body = self.objects.get(source["object_key"])
        if not body.startswith(b"%PDF-") or len(body) != source["artifact"]["byte_size"] or sha256_bytes(body) != source["sha256"]:
            raise ValueError("report_artifact_conflict")
        return source["artifact"], body

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
        parsed = []
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
            parsed.append((vid, role))
        rows = (
            {
                str(row[0]): row
                for row in conn.execute(
                    """SELECT f.id, f.file_id, f.tenant_id, f.status, f.original_filename, r.kind, r.tenant_id,
                EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id = f.file_id
                    AND g.user_id = %s AND g.capability IN ('read', 'review')),
                f.upload_request->>'feishu_folder_token' IS NULL OR
                    (f.upload_request->'cloud_upload'->>'phase' = 'ready' AND EXISTS
                        (SELECT 1 FROM product.file_version_sources s WHERE s.file_version_id = f.id
                         AND s.provider = 'feishu' AND s.object_ref = f.upload_request->'cloud_upload'->>'file_token'))
                FROM product.file_versions f LEFT JOIN product.resources r ON r.id = f.file_id
                WHERE f.id = ANY(%s::uuid[])""",
                    (principal.user_id, [vid for vid, _role in parsed]),
                ).fetchall()
            }
            if parsed
            else {}
        )
        for vid, role in parsed:
            row = rows.get(vid)
            if not row or str(row[2]) != principal.tenant_id:
                raise KeyError("missing")
            if row[3] != READY or not row[8]:
                raise ValueError("file_not_ready")
            if row[5] != "file" or str(row[6]) != principal.tenant_id or not row[7]:
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
            cast(dict[str, Any], row).update(
                rule_version_id=str(rule[0]) if rule else None,
                rule_title=rule[1] if rule else None,
                rule_body=rule[2] if rule else None,
                # freeze_selected_targets assigns the adopted Suite identity.
                rule_execution=None,
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
        task_revision: int | None = None,
        usage_policy=None,
        feedback_inputs: list[dict] | None = None,
        expected_confirmation_basis_digest: str | None = None,
        require_confirmation_basis: bool = False,
    ) -> dict:
        # Serialize one tenant/key before reading existing acceptance, including
        # competing requests from different drafts. Hash collisions only serialize.
        if feedback_inputs is None:
            self._reject_task_predecessor(conn, predecessor_id)
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (principal.tenant_id + ":" + key,))
        existing = conn.execute(
            """
            SELECT id, request_fingerprint, predecessor_id, analysis_mode, source_draft_id, created_by FROM product.audit_rounds
            WHERE tenant_id = %s AND idempotency_key = %s
            """,
            (principal.tenant_id, key),
        ).fetchone()
        if existing and str(existing[4]) != source_draft_id and conn.execute("SELECT 1 FROM product.audit_drafts WHERE id = %s AND task_fingerprint IS NOT NULL", (existing[4],)).fetchone():
            raise ValueError("idempotency_conflict")
        draft_status = None
        recognized_metadata: dict[str, dict] = {}
        recognized_types: dict[str, str | None] = {}
        analysis_mode = ANALYSIS_STANDARD
        if source_draft_id:
            draft = conn.execute(
                """
                SELECT id, tenant_id, status, task_fingerprint, case_context FROM product.audit_drafts
                WHERE id = %s FOR UPDATE
                """,
                (source_draft_id,),
            ).fetchone()
            if not draft or str(draft[1]) != principal.tenant_id:
                raise KeyError("missing")
            if draft[3] is not None and task_revision is None:
                raise ValueError("task_input_locked")
            if draft[3] is None and PREPARATION_KEY in (draft[4] or {}):
                raise ValueError("task_input_locked")
            draft_status = draft[2]
            if task_revision is not None:
                owned = self._recognition_owner(conn, principal, source_draft_id)
                predecessor_id = str(owned["predecessor_id"]) if owned["predecessor_id"] else None
                if existing and (str(existing[4]) != source_draft_id or str(existing[5]) != principal.user_id):
                    raise ValueError("idempotency_conflict")
                if owned["input_revision"] != task_revision:
                    raise ValueError("task_revision_conflict")
                if owned["task_completion_reason"]:
                    raise ValueError("task_input_locked")
                if draft_status == DRAFT_ACCEPTED and not existing:
                    raise ValueError("draft_already_accepted")
                if not existing and require_confirmation_basis and (not isinstance(expected_confirmation_basis_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_confirmation_basis_digest)):
                    raise ValueError("invalid_task_confirmation")
                task = self._recognition_public(conn, principal, source_draft_id)
                selected = [row for row in task["files"] if row["selection"] == "selected"]
                recognized_metadata = {row["file"]["id"]: dict((row.get("match") or {}).get("metadata") or {}) for row in selected}
                recognized_types = {row["file"]["id"]: (row.get("match") or {}).get("document_type") for row in selected}
                if not existing and (
                    task["state"] != "pending_confirm"
                    or any((row["match"] or {}).get("state") not in {"matched", "reference", "unmatched"} or row["validation"]["state"] != "valid" for row in selected)
                ):
                    raise ValueError("task_not_ready")
                if not existing:
                    v4_customers = {customer for row in selected for customer in _preset_customers(((row["match"].get("rule") or {}).get("body") or {}))}
                    if len(v4_customers) > 1 or v4_customers and owned["customer_preference"] and v4_customers != {owned["customer_preference"]}:
                        raise ValueError("customer_scope_conflict")
                    for row in selected:
                        scope = row.get("validation_scope")
                        rule_body = ((row.get("match") or {}).get("rule") or {}).get("body") or {}
                        if scope and row["match"]["state"] != "reference" and (owned["customer_preference"] != "UAES" or rule_body.get("suite_key") != "UAES:U13"):
                            raise ValueError("validation_scope_mismatch")
                    if require_confirmation_basis and task["confirmation_basis_digest"] != expected_confirmation_basis_digest:
                        raise ValueError("task_confirmation_changed")
                    if self.settings.precheck_gate_enforced:
                        from product.prechecks import blocked_targets
                        from product.store.prechecks import freeze_task_precheck

                        receipt = task.get("precheck")
                        blocked = set()
                        if receipt and receipt["status"] == "succeeded":
                            frozen = freeze_task_precheck(self, conn, principal, task)
                            if frozen == receipt["input"]:
                                blocked = blocked_targets(frozen, receipt["result"])
                        if blocked:
                            selected = [row for row in selected if row["file"]["id"] not in blocked]
                            if not any(row["match"]["state"] == "matched" for row in selected):
                                raise ValueError("precheck_blocked")
                            for version_id in blocked:
                                conn.execute(
                                    "UPDATE product.draft_items SET selection = 'removed', removal_reason = %s WHERE draft_id = %s AND file_version_id = %s",
                                    ("预检已确认的阻断缺陷，需修正后重新提交", source_draft_id, version_id),
                                )
                items = [
                    {
                        "file_version_id": row["file"]["id"],
                        "role": "reference" if row["match"]["state"] == "reference" else "target",
                        "rule_version_id": row["match"]["rule"]["id"] if row["match"]["rule"] else None,
                    }
                    for row in selected
                    if (row["match"] or {}).get("state") == "reference" or (row["match"] or {}).get("state") == "matched" and row["match"]["rule"]
                ]
                if not existing:
                    current_rules = {rule["id"] for rule in latest_rule_versions(self._list_rules(conn, principal.tenant_id))}
                    source = self._reaudit_source(conn, principal, predecessor_id) if predecessor_id else None
                    inherited = {
                        (file["file"]["id"], file.get("preferred_rule_version_id"))
                        for file in selected
                        if source
                        and source["customer_preference"] == owned["customer_preference"]
                        and source["inputs"].get(file.get("replacement_of_file_version_id") or file["file"]["id"], {}).get("rule_id") == file.get("preferred_rule_version_id")
                    }
                    if any(item["role"] == "target" and item["rule_version_id"] not in current_rules and (item["file_version_id"], item["rule_version_id"]) not in inherited for item in items):
                        raise ValueError("rule_candidate_expired")
                    for row in selected:
                        if row["match"]["state"] == "unmatched":
                            conn.execute(
                                "UPDATE product.draft_items SET selection = 'removed', removal_reason = %s WHERE draft_id = %s AND file_version_id = %s",
                                ("未匹配到生效规则，未纳入审核", source_draft_id, row["file"]["id"]),
                            )
            elif items is None:
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
        for item in prepared:
            item["frozen_metadata"] = recognized_metadata.get(item["file_version_id"], {})
            item["document_type"] = recognized_types.get(item["file_version_id"]) or (item.get("rule_body") or {}).get("type")
        if feedback_inputs is not None:
            if analysis_mode != ANALYSIS_DEEP or source_draft_id is not None:
                raise ValueError("deep_feedback_requires_deep")
            from product.store.deep_feedback import normalize_feedback_body

            overrides = {item["file_version_id"]: item for item in feedback_inputs}
            for item in prepared:
                if item["role"] == "target":
                    item["rule_body"] = normalize_feedback_body(overrides[item["file_version_id"]]["rule_body"])
        require_targets(prepared)
        fingerprint = request_fingerprint(prepared)
        if existing:
            resource = self._resource(conn, str(existing[0]))
            if resource is None or not can_read(principal, resource, self._grants(conn, str(existing[0]), principal.user_id)):
                raise KeyError("missing")
            if existing[1] != fingerprint or (str(existing[2]) if existing[2] else None) != predecessor_id or existing[3] != analysis_mode:
                raise ValueError("idempotency_conflict")
            return cast(dict, self._round_public(conn, str(existing[0])))
        analysis_mode = self._offered_analysis_mode(analysis_mode)
        if source_draft_id and draft_status == DRAFT_ACCEPTED:
            raise ValueError("draft_already_accepted")
        round_id = str(uuid.uuid4())
        from product.store.suites import SuiteStore, freeze_round_execution

        snapshot = freeze_round_execution(self, conn, principal, round_id=round_id, prepared=prepared, analysis_mode=analysis_mode, require_rule_match=feedback_inputs is not None)
        execution_payload: dict[str, Any] = {
            "round_id": round_id,
            "executor": "ppap-checker-core",
            "executor_revision": snapshot.runtime_revision,
            "checker_snapshot_digest": snapshot.input_digest,
        }
        self._check_usage_policy(conn, principal, usage_policy)
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
        SuiteStore(self.settings, self.objects, self.role).save_snapshot(conn, snapshot=snapshot)
        if source_draft_id:
            conn.execute(
                "UPDATE product.audit_rounds SET case_context = (SELECT case_context - %s FROM product.audit_drafts WHERE id = %s) WHERE id = %s", (PREPARATION_KEY, source_draft_id, round_id)
            )
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
                Jsonb(execution_payload),
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
        if source_draft_id:
            self._queue_task_notification(conn, source_draft_id, "started", round_id)
        return cast(dict, self._round_public(conn, round_id))

    def _check_usage_policy(self, conn, principal, policy) -> None:
        if not self.settings.usage_charge_enabled:
            return
        item_id = self.settings.feishu_ai_item_id.strip()
        if not item_id or principal.provider != PROVIDER_FEISHU or policy is None:
            raise ValueError("usage_quota_unavailable")
        # Hold the same account lock through admission, free allocation and
        # enqueue. Replays return before this external read or any new charge.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("usage:" + principal.tenant_id + ":" + item_id,))
        try:
            partner = policy(principal)
        except FeishuUsageError as error:
            raise ValueError("usage_quota_unavailable") from error
        if not isinstance(partner, PartnerAiItem):
            raise ValueError("usage_quota_unavailable")
        if not partner.enabled:
            raise ValueError("usage_ai_disabled")
        remaining = remaining_trial_points(self.settings.feishu_free_trial_quota_points, self._trial_ledger_consumed(conn, principal.tenant_id, item_id))
        if remaining <= 0 and partner.user_quota <= 0:
            raise ValueError("usage_balance_empty")

    def _record_round_usage(self, conn, principal, round_id, prepared) -> None:
        if not self.settings.usage_charge_enabled:
            return
        item_id = self.settings.feishu_ai_item_id.strip()
        if not item_id or principal.provider != PROVIDER_FEISHU:
            raise ValueError("usage_quota_unavailable")
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("usage:" + principal.tenant_id + ":" + item_id,))
        consumed = self._trial_ledger_consumed(conn, principal.tenant_id, item_id)
        remaining = remaining_trial_points(self.settings.feishu_free_trial_quota_points, consumed)
        for row in prepared:
            if row["role"] != "target":
                continue
            version = row["file_version_id"]
            for amount, trial in split_trial_and_partner(remaining, audit_usage_points(1)):
                conn.execute(
                    """INSERT INTO product.usage_events
                       (id, tenant_id, user_id, ai_item_id, consumed_points, is_free_trial,
                        external_status, trace_id, round_id, file_version_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        str(uuid.uuid4()),
                        principal.tenant_id,
                        principal.user_id,
                        item_id,
                        amount,
                        trial,
                        "none" if trial else "pending",
                        round_id + ":" + version + (":trial" if trial else ":partner"),
                        round_id,
                        version,
                    ),
                )
                if trial:
                    remaining -= amount

    def _draft_public(self, conn, draft_id: str) -> dict | None:
        row = conn.execute(
            """
            SELECT d.id, d.tenant_id, d.status, d.updated_at, d.predecessor_id, d.analysis_mode, d.case_context, d.input_revision, d.task_fingerprint
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
            "case_context": {key: value for key, value in (row[6] or {}).items() if key != PREPARATION_KEY},
            "upload_preparation": (row[6] or {}).get(PREPARATION_KEY),
            "preparation_revision": row[7] if PREPARATION_KEY in (row[6] or {}) and row[8] is None else None,
            "task_id": str(row[0]) if row[8] is not None else None,
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

    def _round_histories(self, conn, round_ids: list[str]) -> list[dict]:
        if not round_ids:
            return []
        with conn.pipeline():
            rounds = conn.execute(
                """SELECT r.id, r.execution, r.predecessor_id, r.created_at, r.analysis_mode, r.case_context, r.coverage,
                r.source_draft_id, CASE WHEN d.task_fingerprint IS NOT NULL THEN d.id END,
                GREATEST(r.created_at,
                    (SELECT max(j.updated_at) FROM engine.jobs j WHERE j.audit_round_id = r.id),
                    (SELECT max(v.updated_at) FROM product.reviews v WHERE v.round_id = r.id))
                FROM product.audit_rounds r LEFT JOIN product.audit_drafts d ON d.id = r.source_draft_id WHERE r.id = ANY(%s::uuid[])""",
                (round_ids,),
            )
            inputs = conn.execute(
                """SELECT i.round_id, i.role, fv.original_filename, fv.id, i.rule_version_id, i.rule_title
                FROM product.round_inputs i JOIN product.file_versions fv ON fv.id = i.file_version_id
                WHERE i.round_id = ANY(%s::uuid[]) ORDER BY i.role, fv.original_filename""",
                (round_ids,),
            )
            counts = conn.execute(
                """SELECT round_id, status, COUNT(*), jsonb_agg(jsonb_build_object(
                    'item_key', item_key, 'status', status, 'locator', jsonb_build_object(
                        'file_version_id', locator->>'file_version_id', 'rule_item_id', locator->>'rule_item_id')))
                FROM product.findings WHERE round_id = ANY(%s::uuid[]) GROUP BY round_id, status""",
                (round_ids,),
            )
            reviews = conn.execute("SELECT round_id, item_key, judgment, rationale, citations FROM product.reviews WHERE round_id = ANY(%s::uuid[])", (round_ids,))
        by_id = {str(row[0]): row for row in rounds.fetchall()}
        groups = []
        for cursor in (inputs, counts, reviews):
            grouped: dict[str, list] = {}
            for row in cursor.fetchall():
                grouped.setdefault(str(row[0]), []).append(row[1:])
            groups.append(grouped)
        return [self._round_history(by_id[ident], *(group.get(ident, []) for group in groups)) for ident in round_ids if ident in by_id]

    def _round_history(self, row, inputs, counts, reviews) -> dict:
        result_counts = {"PASS": 0, "NG": 0, "ERR": 0, "UNABLE": 0, "NA": 0}
        findings = []
        finding_keys = set()
        reviewable_keys = set()
        for status, n, items in counts:
            keys = [item["item_key"] for item in items]
            finding_keys.update(keys)
            findings.extend(items)
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
            for key, judgment, rationale, citations in reviews
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
            "has_execution_error": has_execution_error(row[1], row[6] or {}, findings),
            "source_draft_id": str(row[7]) if row[7] else None,
            "task_id": str(row[8]) if row[8] else None,
            "updated_at": row[9].isoformat() if row[9] is not None else None,
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

    def _round_public(self, conn, round_id: str, *, read_view: bool = False, principal: Principal | None = None) -> dict | None:
        # These reads are independent. Send them together on the caller's connection.
        with conn.pipeline():
            round_read = conn.execute(
                """SELECT id, execution, coverage, predecessor_id,
                       source_draft_id, result_attempt, created_at, analysis_mode, case_context,
                       EXISTS (SELECT 1 FROM product.audit_drafts d WHERE d.id = source_draft_id AND d.task_fingerprint IS NOT NULL),
                       (SELECT json_build_object('provider', provider, 'object_kind', object_kind, 'object_ref', object_ref)
                        FROM product.round_writeback_targets WHERE round_id = a.id)
                FROM product.audit_rounds a WHERE id = %s""",
                (round_id,),
            )
            inputs_read = conn.execute(
                """SELECT i.role, fv.id, fv.file_id, fv.original_filename, fv.status, i.rule_version_id, i.rule_title, i.rule_body,
                       fv.content_type, fv.byte_size, fv.sha256
                FROM product.round_inputs i
                JOIN product.file_versions fv ON fv.id = i.file_version_id
                WHERE i.round_id = %s ORDER BY i.role, fv.original_filename""",
                (round_id,),
            )
            findings_read = conn.execute(
                """SELECT f.item_key, f.classification, f.item, f.priority, f.status, f.suggestions, f.locator,
                       rv.judgment, rv.rationale, rv.next_step, rv.version, rv.citations
                FROM product.findings f
                LEFT JOIN product.reviews rv ON rv.round_id = f.round_id AND rv.item_key = f.item_key
                WHERE f.round_id = %s ORDER BY f.item_key""",
                (round_id,),
            )
            job_read = conn.execute(
                """SELECT j.payload, a.diagnostic, j.updated_at, j.id FROM engine.jobs j
                LEFT JOIN engine.attempts a ON a.job_id = j.id AND a.attempt = j.attempt
                WHERE j.audit_round_id = %s AND j.kind = 'audit' ORDER BY j.created_at LIMIT 1""",
                (round_id,),
            )
            reviews_read = conn.execute("SELECT item_key, judgment, rationale, next_step, version, citations FROM product.reviews WHERE round_id = %s AND item_key LIKE 'coverage:%%'", (round_id,))
        row = round_read.fetchone()
        if not row:
            return None
        inputs = inputs_read.fetchall()
        findings = findings_read.fetchall()
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
        job = job_read.fetchone()
        payload = job[0] if job else {}
        diagnostic = job[1] if job and job[1] else {}
        progress = diagnostic.get("progress")
        if progress and row[1] == EXEC_RUNNING:
            progress = {**progress, "updated_at": job[2].isoformat()}
        coverage = dict(row[2] or {})
        reviews = {review[0]: {"judgment": review[1], "rationale": review[2], "next_step": review[3], "version": review[4], "citations": review[5] or []} for review in reviews_read.fetchall()}
        if coverage.get("rule_items"):
            coverage["rule_items"] = [{**item, "review": reviews.get(f"coverage:{item.get('file_version_id')}:{item.get('id')}")} for item in coverage["rule_items"]]
        body = {
            "id": str(row[0]),
            "execution": row[1],
            "coverage": coverage,
            "predecessor_id": str(row[3]) if row[3] else None,
            "source_draft_id": str(row[4]) if row[4] else None,
            "preparation_allowed": row[1] == EXEC_FAILED and not row[9],
            "result_attempt": row[5],
            "result_job_id": str(job[3]) if job else None,
            "created_at": row[6].isoformat() if row[6] is not None else None,
            "analysis_mode": normalize_analysis_mode(row[7]),
            "case_context": row[8] or {},
            "writeback": public_target(row[10]),
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
        if read_view:
            versions = {
                str(item[1]): {"id": str(item[1]), "file_id": str(item[2]), "original_filename": item[3], "status": item[4], "content_type": item[8], "byte_size": int(item[9]), "sha256": item[10]}
                for item in inputs
            }
            view = round_read_view(body, versions)
            if row[9] and principal is not None:
                task_id = str(row[4])
                try:
                    self._recognition_owner(conn, principal, task_id)
                except KeyError:
                    reason = "当前账号无权访问此任务报告。"
                    view["report_source"] = {"task_id": None, "access": {"state": "denied", "reason": reason}}
                    for file in view["files"]:
                        if file["audit"]:
                            file["audit"]["report"] = {"state": "not_eligible", "report_id": None, "reason": reason}
                else:
                    reports, _updated_at = self._task_file_reports(conn, principal.tenant_id, task_id, round_id, row[5])
                    view["report_source"] = {"task_id": task_id, "access": {"state": "allowed", "reason": None}}
                    for file in view["files"]:
                        if file["audit"] and file["file"]["id"] in reports:
                            file["audit"]["report"] = reports[file["file"]["id"]]
            body["read_view"] = view
        return body

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
                "external_confirmed_at",
                "debit_order_id",
                "debited_at",
                "trace_id",
                "created_at",
                "round_id",
                "file_version_id",
            )
        }
        for key in ("id", "user_id", "round_id", "file_version_id"):
            if result[key] is not None:
                result[key] = str(result[key])
        for key in ("created_at", "external_confirmed_at", "debited_at"):
            value = result[key]
            if value is not None and hasattr(value, "isoformat"):
                result[key] = value.isoformat()
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

    def list_audit_usage(self, principal: Principal, query: dict) -> dict:
        from .usage import usage_page

        with self._conn() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            tenant = conn.execute("SELECT display_name FROM product.tenants WHERE id = %s", (principal.tenant_id,)).fetchone()
            if tenant is None:
                raise KeyError("missing")
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """SELECT u.*, a.source_draft_id::text AS task_id, a.result_attempt, a.created_at AS accepted_at,
                        f.original_filename AS filename, COALESCE(NULLIF(identity.display_name, ''), '发起人信息不可用') AS initiator
                    FROM product.usage_events u
                    LEFT JOIN product.audit_rounds a ON a.id = u.round_id AND a.tenant_id = u.tenant_id
                    LEFT JOIN product.file_versions f ON f.id = u.file_version_id AND f.tenant_id = u.tenant_id
                    LEFT JOIN LATERAL (SELECT i.display_name FROM product.identities i
                        WHERE i.user_id = u.user_id AND i.provider = %s AND i.app_id = %s AND i.tenant_key = %s
                        ORDER BY i.created_at, i.id LIMIT 1) identity ON true
                    WHERE u.tenant_id = %s ORDER BY u.created_at, u.id""",
                    (principal.provider, principal.app_id, principal.tenant_key, principal.tenant_id),
                )
                rows = [{**row, **self.usage_event_public(row)} for row in cur.fetchall()]
            item_id = self.settings.feishu_ai_item_id.strip()
            if item_id:
                total = max(0, self.settings.feishu_free_trial_quota_points)
                free: dict = {
                    "free_total": {"value": total, "reason": None},
                    "free_remaining": {"value": remaining_trial_points(total, self._trial_ledger_consumed(conn, principal.tenant_id, item_id)), "reason": None},
                }
            else:
                free = {key: {"value": None, "reason": "尚未确认当前企业的用量项目与免费额度。"} for key in ("free_total", "free_remaining")}
            archive = {(str(row[0]), str(row[1])): {"document_id": self._archive_document_id(principal, row), "sequence": row[5]} for row in self._archive_rows(conn, principal)}
            return usage_page(rows, query, {"id": principal.tenant_id, "name": tenant[0]}, free, archive, _now())

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
                     AND external_status = 'pending' AND NOT is_free_trial
                     AND debit_order_id IS NULL AND debited_at IS NULL
                   RETURNING id""",
                (claim, event_id, principal.tenant_id, principal.user_id),
            ).fetchone()
        return claim if row else None

    def pending_usage_sessions(self, app_id: str) -> Iterator[tuple[str, Session]]:
        # A viewer's session is never used to pay another user's obligation.
        # Expired/revoked authorization waits for that owner to sign in again.
        after_time, after_id = None, None
        while True:
            with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """SELECT u.id, u.created_at, u.user_id, u.tenant_id, s.access_token, s.expires_at,
                    s.access_expires_at, i.tenant_key, i.subject, i.display_name, m.role
                FROM product.usage_events u
                JOIN product.tenants t ON t.id = u.tenant_id AND t.provider = 'feishu'
                JOIN product.identities i ON i.user_id = u.user_id AND i.provider = t.provider
                    AND i.tenant_key = t.external_key AND i.app_id = %s
                JOIN product.memberships m ON m.user_id = u.user_id AND m.tenant_id = u.tenant_id
                JOIN LATERAL (
                    SELECT a.access_token, authority.expires_at, a.access_expires_at
                    FROM product.provider_authorizations a
                    JOIN LATERAL (
                        SELECT expires_at FROM product.sessions WHERE provider_authorization_id=a.id
                        AND user_id=u.user_id AND tenant_id=u.tenant_id AND revoked_at IS NULL AND expires_at>now()
                        UNION ALL
                        SELECT expires_at FROM product.mcp_grants WHERE provider_authorization_id=a.id
                        AND user_id=u.user_id AND tenant_id=u.tenant_id AND revoked_at IS NULL AND expires_at>now()
                        AND 'audits:execute'=ANY(scopes)
                    ) authority ON true
                    WHERE a.user_id=u.user_id AND a.tenant_id=u.tenant_id AND a.provider=i.provider
                    AND a.app_id=i.app_id AND a.subject=i.subject AND a.state='ready'
                    AND a.access_expires_at>now() AND a.access_token<>''
                    ORDER BY a.access_expires_at DESC LIMIT 1) s ON true
                WHERE u.external_status = 'pending' AND NOT u.is_free_trial
                    AND u.debit_order_id IS NULL AND u.debited_at IS NULL
                    AND (%s::timestamptz IS NULL OR (u.created_at, u.id) > (%s::timestamptz, %s::uuid))
                ORDER BY u.created_at, u.id LIMIT 20""",
                    (app_id, after_time, after_time, after_id),
                )
                rows = cur.fetchall()
            if not rows:
                return
            # Advance even when this page cannot pay, so one tenant's debt
            # cannot permanently hide another tenant's authorized pending work.
            after_time, after_id = rows[-1]["created_at"], rows[-1]["id"]
            for row in rows:
                yield (
                    str(row["id"]),
                    Session(
                        Principal(str(row["user_id"]), str(row["tenant_id"]), PROVIDER_FEISHU, app_id, row["tenant_key"], row["subject"], row["role"], row["display_name"]),
                        row["expires_at"],
                        row["access_token"],
                        access_expires_at=row["access_expires_at"],
                    ),
                )

    def settle_usage_external(self, principal, event_id, claim, status, record_id=None) -> dict:
        if status not in {"confirmed", "unknown"} or (status == "confirmed" and not record_id):
            raise ValueError("invalid_external_status")
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """UPDATE product.usage_events SET external_status = %s, external_record_id = %s,
                          external_confirmed_at = CASE WHEN %s = 'confirmed' THEN now() ELSE NULL END
                   WHERE id = %s AND tenant_id = %s AND user_id = %s
                     AND report_claim = %s AND external_status = 'reporting'
                   RETURNING *""",
                (status, record_id, status, event_id, principal.tenant_id, principal.user_id, claim),
            )
            row = cur.fetchone()
        if row is None:
            raise KeyError("usage_claim_lost")
        return row
