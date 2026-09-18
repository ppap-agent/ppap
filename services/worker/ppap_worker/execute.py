"""Claimed job execution against a ready file version. Lazy-imports the engine."""
from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

from ppap_core.files import READY, sha256_bytes
from ppap_core.identify import classify_file
from ppap_core.rounds import ROUND_ACCEPTED, ROUND_FAILED, ROUND_RUNNING
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


def mark_round_running(conn, tenant_id: str, payload: dict) -> None:
    round_id = _as_uuid(payload.get("audit_round_id"))
    if not round_id:
        return
    conn.execute(
        """
        UPDATE product.audit_rounds
        SET status = %s, updated_at = now()
        WHERE id = %s AND tenant_id = %s AND status IN (%s, %s, %s)
        """,
        (ROUND_RUNNING, round_id, tenant_id, ROUND_ACCEPTED, ROUND_FAILED, ROUND_RUNNING),
    )


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
    result_id = str(uuid.uuid4())
    round_id = _as_uuid(payload.get("audit_round_id"))
    with conn.transaction():
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
        if round_id:
            conn.execute(
                """
                INSERT INTO product.round_results
                    (id, round_id, tenant_id, job_id, attempt, file_version_id, sha256,
                     product_rule_version_id, engine_rule_id, findings)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    result_id, round_id, job["tenant_id"], job["id"], job["attempt"], version_id, expected,
                    payload.get("product_rule_version_id"), payload.get("engine_rule_id"), Jsonb(findings),
                ),
            )
            _refresh_round_status(conn, job["tenant_id"], round_id, result_id)


def fail_claimed_job(conn, job: dict, worker: str, code: str) -> None:
    payload = job["payload"] if isinstance(job.get("payload"), dict) else {}
    round_id = _as_uuid(payload.get("audit_round_id"))
    with conn.transaction():
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
        if round_id:
            _refresh_round_status(conn, job["tenant_id"], round_id, None)


def _refresh_round_status(conn, tenant_id: str, round_id: str, result_id: str | None) -> None:
    rows = conn.execute(
        "SELECT status FROM engine.jobs WHERE audit_round_id = %s AND tenant_id = %s AND kind = 'audit.execute'",
        (round_id, tenant_id),
    ).fetchall()
    statuses = {row[0] for row in rows}
    if not statuses:
        status = "accepted"
    elif statuses <= {"succeeded"}:
        status = "succeeded"
    elif "failed" in statuses and not (statuses & {"queued", "leased"}):
        status = "failed"
    elif "leased" in statuses or "queued" in statuses:
        status = "running" if "leased" in statuses else "accepted"
    else:
        status = "running"
    conn.execute(
        """
        UPDATE product.audit_rounds
        SET status = %s,
            current_result_id = COALESCE(%s, current_result_id),
            updated_at = now()
        WHERE id = %s AND tenant_id = %s
        """,
        (status, result_id, round_id, tenant_id),
    )


def identify_claimed_job(settings: Settings, conn, job: dict, worker: str) -> None:
    payload = job["payload"] if isinstance(job["payload"], dict) else {}
    file_id = _as_uuid(payload.get("file_id"))
    version_id = _as_uuid(payload.get("file_version_id"))
    item_id = _as_uuid(payload.get("item_id"))
    expected = str(payload.get("sha256") or "")
    if not file_id or not version_id or not item_id or len(expected) != 64:
        raise InputConflict("invalid_payload")
    version = load_ready_version(conn, job["tenant_id"], file_id, version_id)
    if version["sha256"] != expected:
        raise InputConflict("sha256_mismatch")
    body = S3Objects(settings).get(version["object_key"])
    if sha256_bytes(body) != expected:
        raise InputConflict("object_mutated")
    identification = classify_file(str(payload.get("original_filename") or version["original_filename"]), body)
    evidence = dict(identification.evidence)
    evidence["file_version_id"] = version_id
    evidence["sha256"] = expected
    evidence["role"] = payload.get("role")
    status = identification.status
    matched = identification.matched_document_type
    project_id = str(payload.get("project_id") or "").strip() or None
    if project_id:
        count = conn.execute(
            "SELECT COUNT(*) FROM product.rule_versions WHERE tenant_id = %s AND project_id = %s",
            (job["tenant_id"], project_id),
        ).fetchone()[0]
        if int(count) == 0:
            status = "unknown_project"
            matched = identification.matched_document_type
            evidence["reason"] = "unknown_project"
            evidence["proposed_product_rule_version_id"] = None
            evidence["proposed_engine_rule_id"] = None
        elif identification.proposed_engine_rule_id:
            row = conn.execute(
                """
                SELECT id, content_sha256, content
                FROM product.rule_versions
                WHERE tenant_id = %s AND project_id = %s AND engine_rule_id = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (job["tenant_id"], project_id, identification.proposed_engine_rule_id),
            ).fetchone()
            if row:
                content = row[2] if isinstance(row[2], dict) else {}
                checks = content.get("checks") if isinstance(content.get("checks"), list) else []
                evidence["proposed_product_rule_version_id"] = str(row[0])
                evidence["proposed_engine_rule_id"] = identification.proposed_engine_rule_id
                evidence["proposed_content_sha256"] = row[1]
                evidence["proposed_check_count"] = len(checks)
            else:
                evidence["proposed_product_rule_version_id"] = None
                evidence["proposed_engine_rule_id"] = identification.proposed_engine_rule_id
                evidence["no_rule_for_type"] = True
    else:
        evidence["proposed_product_rule_version_id"] = None
        evidence["proposed_engine_rule_id"] = identification.proposed_engine_rule_id
        evidence["default_project"] = False
    result = {
        "item_id": item_id,
        "file_version_id": version_id,
        "sha256": expected,
        "identification_status": status,
        "matched_document_type": matched,
        "product_rule_version_id": payload.get("preserve_product_rule_version_id"),
        "engine_rule_id": None,
        "rule_profile": None,
        "evidence": evidence,
    }
    updated_item = conn.execute(
        """
        UPDATE product.draft_items
        SET identification_status = %s,
            matched_document_type = %s,
            match_evidence = %s,
            identification_job_id = %s,
            updated_at = now()
        WHERE id = %s AND tenant_id = %s
        """,
        (status, matched, Jsonb(evidence), job["id"], item_id, job["tenant_id"]),
    )
    if updated_item.rowcount != 1:
        raise InputConflict("item_not_found")
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
        (Jsonb({"identification_status": status}), job["id"], job["attempt"], worker),
    )
