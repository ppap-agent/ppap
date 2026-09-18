"""Claimed job execution against a ready file version. Lazy-imports the engine."""
from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

from ppap_core.files import READY, sha256_bytes
from ppap_core.settings import Settings
from ppap_core.storage import S3Objects


class InputConflict(Exception):
    pass


def _as_uuid(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def load_ready_version(conn, tenant_id: str, file_id: str, version_id: str) -> dict:
    row = conn.execute(
        """
        SELECT id, file_id, tenant_id, original_filename, content_type, byte_size, sha256, object_key, status
        FROM product.file_versions
        WHERE id = %s AND file_id = %s AND tenant_id = %s
        """,
        (version_id, file_id, tenant_id),
    ).fetchone()
    if row is None:
        raise InputConflict("version_not_found")
    record = {
        "id": str(row[0]),
        "file_id": str(row[1]),
        "tenant_id": str(row[2]),
        "original_filename": row[3],
        "content_type": row[4],
        "byte_size": row[5],
        "sha256": row[6],
        "object_key": row[7],
        "status": row[8],
    }
    if record["status"] != READY or not record["sha256"]:
        raise InputConflict("not_ready")
    return record


def execute_claimed_job(settings: Settings, conn, job: dict, worker: str) -> None:
    from ppap_core.audit_run import findings_from_bytes

    payload = job["payload"] if isinstance(job["payload"], dict) else {}
    file_id = _as_uuid(payload.get("file_id"))
    version_id = _as_uuid(payload.get("file_version_id"))
    expected = str(payload.get("sha256") or "")
    if not file_id or not version_id or len(expected) != 64:
        raise InputConflict("invalid_payload")
    version = load_ready_version(conn, job["tenant_id"], file_id, version_id)
    if version["sha256"] != expected:
        raise InputConflict("sha256_mismatch")
    body = S3Objects(settings).get(version["object_key"])
    if sha256_bytes(body) != expected:
        raise InputConflict("object_mutated")
    findings = findings_from_bytes(
        body=body,
        filename=str(payload.get("original_filename") or version["original_filename"]),
        sha256=expected,
        file_version_id=version_id,
        product_rule_version_id=str(payload.get("product_rule_version_id") or ""),
        engine_rule_id=str(payload.get("engine_rule_id") or ""),
        document_type=str(payload.get("document_type") or ""),
        rule_profile=str(payload.get("rule_profile") or "v2"),
        rules=payload.get("rules") if isinstance(payload.get("rules"), list) else [],
    )
    result = {
        "file_version_id": version_id,
        "sha256": expected,
        "product_rule_version_id": payload.get("product_rule_version_id"),
        "engine_rule_id": payload.get("engine_rule_id"),
        "findings": findings,
        "corpus": "NOT_PROVEN",
    }
    updated = conn.execute(
        """
        UPDATE engine.jobs
        SET status = 'succeeded',
            result = %s,
            lease_until = NULL,
            updated_at = now()
        WHERE id = %s AND lease_owner = %s AND attempt = %s AND status = 'leased'
        """,
        (Jsonb(result), job["id"], worker, job["attempt"]),
    )
    if updated.rowcount != 1:
        return
    conn.execute(
        """
        UPDATE engine.attempts
        SET finished_at = now(), outcome = 'succeeded', diagnostic = %s
        WHERE job_id = %s AND attempt = %s AND worker_id = %s
        """,
        (Jsonb({"findings": len(findings)}), job["id"], job["attempt"], worker),
    )


def fail_claimed_job(conn, job: dict, worker: str, code: str) -> None:
    updated = conn.execute(
        """
        UPDATE engine.jobs
        SET status = 'failed',
            result = %s,
            lease_until = NULL,
            updated_at = now()
        WHERE id = %s AND lease_owner = %s AND attempt = %s AND status = 'leased'
        """,
        (Jsonb({"error": code, "corpus": "NOT_PROVEN"}), job["id"], worker, job["attempt"]),
    )
    if updated.rowcount != 1:
        return
    conn.execute(
        """
        UPDATE engine.attempts
        SET finished_at = now(), outcome = 'failed', diagnostic = %s
        WHERE job_id = %s AND attempt = %s AND worker_id = %s
        """,
        (Jsonb({"error": code}), job["id"], job["attempt"], worker),
    )
