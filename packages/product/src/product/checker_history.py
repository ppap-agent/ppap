"""One-time, non-executable history identities on existing Round inputs.

No runtime, model, queue, publication, billing, or review writes belong here.
The importer needs migrator credentials; readback uses current object grants.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from product.identity import can_read
from product.suites import canonical_json, content_digest


class HistoryImportError(ValueError):
    """An allowlisted, secret-free diagnostic suitable for the CLI report."""


def _json_value(value):
    return json.loads(json.dumps(value, default=str))


def history_source(conn, tenant_id: str, round_id: str) -> dict:
    """Read frozen source facts only, never today's rule_versions body."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT id,tenant_id,created_by,source_draft_id,predecessor_id,execution,result_attempt
               FROM product.audit_rounds WHERE id=%s AND tenant_id=%s""",
            (round_id, tenant_id),
        )
        head = cur.fetchone()
        if head is None:
            raise HistoryImportError("history_round_missing")
        cur.execute(
            """SELECT i.file_version_id,i.role,i.rule_version_id,i.rule_title,i.rule_body,i.rule_execution,
                      f.file_id,f.tenant_id,f.original_filename,f.sha256,f.status,f.content_type,f.object_key,rv.tenant_id AS rule_tenant_id
               FROM product.round_inputs i JOIN product.file_versions f ON f.id=i.file_version_id
               LEFT JOIN product.rule_versions rv ON rv.id=i.rule_version_id
               WHERE i.round_id=%s ORDER BY i.file_version_id""",
            (round_id,),
        )
        inputs = cur.fetchall()
        cur.execute(
            """SELECT j.id,j.tenant_id,j.status,j.attempt,j.payload,a.outcome,a.finished_at
               FROM engine.jobs j LEFT JOIN engine.attempts a ON a.job_id=j.id AND a.attempt=%s
               WHERE j.audit_round_id=%s AND j.kind='audit' ORDER BY j.id""",
            (head["result_attempt"], round_id),
        )
        jobs = cur.fetchall()
    # Store only execution identity fields, not arbitrary payloads or diagnostics.
    for job in jobs:
        payload = job.pop("payload") or {}
        job["execution_source"] = {name: payload[name] for name in ("executor", "executor_revision", "protocol_revision") if name in payload}
    return _json_value({"round": head, "inputs": inputs, "jobs": jobs})


def source_digest(source: dict) -> str:
    return content_digest(canonical_json(source))


def _validate_source(source: dict) -> None:
    head, inputs, jobs = source["round"], source["inputs"], source["jobs"]
    if head["execution"] not in {"completed", "failed"} or any(job["status"] in {"queued", "leased"} for job in jobs):
        raise HistoryImportError("history_round_not_terminal")
    if not inputs or not any(item["role"] == "target" for item in inputs):
        raise HistoryImportError("history_inputs_missing")
    if any(item["tenant_id"] != head["tenant_id"] for item in [*inputs, *jobs]):
        raise HistoryImportError("history_tenant_mismatch")
    if any(item["status"] != "ready" or len(item["sha256"] or "") != 64 for item in inputs):
        raise HistoryImportError("history_file_identity_missing")
    for item in inputs:
        if item["role"] == "target" and (not item["rule_version_id"] or item["rule_tenant_id"] != head["tenant_id"] or not isinstance(item["rule_body"], dict)):
            raise HistoryImportError("history_rule_missing")


def _verify_originals(objects, source: dict) -> None:
    for item in source["inputs"]:
        if content_digest(objects.get(item["object_key"], max_bytes=32 * 1024 * 1024)) != item["sha256"]:
            raise HistoryImportError("history_original_digest_mismatch")


def _identity(source: dict, item: dict) -> dict:
    return {
        "schema": "checker-history-v1",
        "tenant_id": source["round"]["tenant_id"],
        "round_id": source["round"]["id"],
        "file_version_id": item["file_version_id"],
        "source_digest": source_digest(source),
        "file_sha256": item["sha256"],
        "rule_digest": content_digest(canonical_json(item["rule_body"])) if item["role"] == "target" else None,
        "verification": "unverified",
        "execution_source": [job["execution_source"] for job in source["jobs"]],
        # Legacy Rounds never kept provable checker bytes.
        "code_identity": "unavailable",
    }


def _read_existing(conn, source: dict) -> dict[str, dict | None]:
    return {
        str(row[0]): row[1]
        for row in conn.execute(
            "SELECT file_version_id,checker_history FROM product.round_inputs WHERE round_id=%s",
            (source["round"]["id"],),
        ).fetchall()
    }


def _verify_existing(objects, source: dict, saved: dict[str, dict | None]) -> bool:
    if not any(saved.values()):
        return False
    if not all(saved.values()):
        raise HistoryImportError("history_partial_identity_conflict")
    _verify_originals(objects, source)
    for item in source["inputs"]:
        identity = saved[item["file_version_id"]]
        if identity is None or identity["source_digest"] != source_digest(source) or identity["file_sha256"] != item["sha256"]:
            raise HistoryImportError("history_source_changed")
        if identity != _identity(source, item):
            raise HistoryImportError("history_identity_mismatch")
    return True


def import_history(store, *, tenant_id: str, apply: bool = False, expected_digest: str | None = None, round_ids: list[str] | None = None) -> dict:
    """Plan then commit each original Round atomically; never hide partial failure.

    Re-running the same command resumes via the immutable per-input identities.
    """
    tenant_id = str(UUID(tenant_id))
    selected = sorted({str(UUID(value)) for value in round_ids}) if round_ids is not None else None
    if store.role != "migrator":
        raise PermissionError("history_migrator_required")
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if not conn.execute("SELECT 1 FROM product.tenants WHERE id=%s", (tenant_id,)).fetchone():
            raise HistoryImportError("history_tenant_missing")
        ids = [
            str(row[0])
            for row in conn.execute(
                """SELECT r.id FROM product.audit_rounds r WHERE r.tenant_id=%s
               AND (%s::uuid[] IS NULL OR r.id=ANY(%s::uuid[]))
               AND NOT EXISTS (SELECT 1 FROM product.checker_execution_snapshots s WHERE s.round_id=r.id) ORDER BY r.id""",
                (tenant_id, selected, selected),
            ).fetchall()
        ]
        if selected is not None and sorted(ids) != selected:
            raise HistoryImportError("history_selection_missing_or_not_legacy")
        sources = [history_source(conn, tenant_id, ident) for ident in ids]
    plans = []
    for source in sources:
        try:
            _validate_source(source)
            _verify_originals(store.objects, source)
            plans.append({"source_digest": source_digest(source)})
        except HistoryImportError as exc:
            plans.append({"source_digest": source_digest(source), "error": str(exc)})
        except Exception:
            plans.append({"source_digest": source_digest(source), "error": "history_read_or_write_failed"})
    digest = content_digest(canonical_json({"tenant_id": tenant_id, "sources": plans}))
    if apply and expected_digest != digest:
        raise HistoryImportError("history_plan_changed_or_unconfirmed")
    results: list[dict[str, Any]] = []
    for source, plan in zip(sources, plans):
        ident = source["round"]["id"]
        try:
            results.append(_import_round(store, tenant_id, source, plan, apply))
        except HistoryImportError as exc:
            results.append({"round_id": ident, "state": "blocked", "reason": str(exc)})
        except Exception:
            # Driver/storage exceptions can carry URLs, source text or
            # credentials. Do not expose them in an operator report.
            results.append({"round_id": ident, "state": "blocked", "reason": "history_read_or_write_failed"})
    return {"schema": "checker-history-import-v1", "tenant_id": tenant_id, "plan_digest": digest, "writes": apply, "complete": all(row["state"] != "blocked" for row in results), "rounds": results}


def _import_round(store, tenant_id, source, plan, apply):
    ident = source["round"]["id"]
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" + (" READ ONLY" if not apply else ""))
        if apply:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("checker-history:" + ident,))
            conn.execute("SELECT id FROM product.audit_rounds WHERE id=%s FOR UPDATE", (ident,))
            conn.execute("SELECT file_version_id FROM product.round_inputs WHERE round_id=%s FOR UPDATE", (ident,))
        current = history_source(conn, tenant_id, ident)
        if source_digest(current) != source_digest(source):
            raise HistoryImportError("history_source_changed")
        _validate_source(current)
        if "error" in plan:
            raise HistoryImportError(plan["error"])
        if _verify_existing(store.objects, current, _read_existing(conn, current)):
            return {"round_id": ident, "state": "verified", "source_digest": source_digest(current)}
        _verify_originals(store.objects, current)
        if apply:
            for item in current["inputs"]:
                conn.execute(
                    "UPDATE product.round_inputs SET checker_history=%s WHERE round_id=%s AND file_version_id=%s AND checker_history IS NULL",
                    (Jsonb(_identity(current, item)), ident, item["file_version_id"]),
                )
            _verify_existing(store.objects, current, _read_existing(conn, current))
        return {"round_id": ident, "state": "imported" if apply else "ready", "source_digest": source_digest(current)}


def read_history(store, principal, round_id: str) -> list[dict]:
    """Current membership, Round and every original-file grant remain required."""
    round_id = str(UUID(round_id))
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if not conn.execute("SELECT 1 FROM product.memberships WHERE tenant_id=%s AND user_id=%s AND role='member'", (principal.tenant_id, principal.user_id)).fetchone():
            raise KeyError("missing")
        resource = store._resource(conn, round_id)
        if resource is None or resource.kind != "audit_round" or not can_read(principal, resource, store._grants(conn, round_id, principal.user_id)):
            raise KeyError("missing")
        source = history_source(conn, principal.tenant_id, round_id)
        for item in source["inputs"]:
            resource = store._resource(conn, item["file_id"])
            if resource is None or not can_read(principal, resource, store._grants(conn, item["file_id"], principal.user_id)):
                raise KeyError("missing")
        saved = _read_existing(conn, source)
        if not _verify_existing(store.objects, source, saved):
            return []
        return [identity for identity in saved.values() if identity]
