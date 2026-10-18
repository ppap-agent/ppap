"""Durable template-feedback attempts, scoped to their submitting user."""

import uuid
from contextlib import contextmanager

from ppap_core.files import FileVersion
from ppap_core.identity import Resource, can_read
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def feedback_receipt(row: dict) -> dict:
    received = row["phase"] == "received"
    return {
        "request": row["payload"]["request"],
        "attempt_id": str(row["attempt_id"]),
        "state": {"preparing": "pending", "dispatching": "unknown"}.get(row["phase"], row["phase"]),
        "submission_id": row["submission_id"] if received else None,
        "received_file_version_ids": row["payload"]["request"]["file_version_ids"] if received else [],
        "received_at": row["received_at"].isoformat() if received else None,
        "retry_allowed": row["phase"] == "failed" and not row.get("superseded"),
        "reason": row["reason"],
    }


class TemplateFeedbackStore:
    def __init__(self, store):
        self.store = store

    def history(self, principal, page: int) -> dict:
        with self.store._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT count(DISTINCT request_id) AS total FROM product.template_feedback WHERE tenant_id = %s AND created_by = %s", (principal.tenant_id, principal.user_id))
            total = cur.fetchone()["total"]
            cur.execute(
                """SELECT * FROM (
                    SELECT DISTINCT ON (request_id) * FROM product.template_feedback
                    WHERE tenant_id = %s AND created_by = %s ORDER BY request_id, attempt_number DESC
                ) latest ORDER BY created_at DESC, request_id DESC LIMIT 10 OFFSET %s""",
                (principal.tenant_id, principal.user_id, (page - 1) * 10),
            )
            rows = cur.fetchall()
            version_ids = list({file["id"] for row in rows for file in row["payload"]["files"]})
            cur.execute(
                """SELECT v.*, r.kind, r.tenant_id AS resource_tenant_id,
                    ARRAY(SELECT g.capability FROM product.resource_grants g WHERE g.resource_id = r.id AND g.user_id = %s) AS grants
                FROM product.file_versions v JOIN product.resources r ON r.id = v.file_id
                WHERE v.id = ANY(%s::uuid[]) AND v.tenant_id = %s""",
                (principal.user_id, version_ids, principal.tenant_id),
            )
            versions = {str(item["id"]): item for item in cur.fetchall()}
        entries = []
        for row in rows:
            files, available = [], True
            for frozen in row["payload"]["files"]:
                version = versions.get(frozen["id"])
                resource = Resource(str(version["file_id"]), str(version["resource_tenant_id"]), version["kind"]) if version else None
                if not version or not resource or resource.kind != "file" or not can_read(principal, resource, set(version["grants"])) or version["status"] != "ready":
                    available = False
                    break
                public = FileVersion(
                    str(version["id"]),
                    str(version["file_id"]),
                    str(version["tenant_id"]),
                    version["original_filename"],
                    version["content_type"],
                    version["byte_size"],
                    version["object_key"],
                    version["status"],
                    version["sha256"],
                ).public()
                if any(public[name] != frozen[name] for name in ("id", "file_id", "original_filename", "content_type", "byte_size", "sha256")):
                    available = False
                    break
                files.append(public)
            entries.append(
                {
                    "receipt": feedback_receipt(row),
                    "files": files if available else [],
                    "files_available": available,
                    "reason": None if available else "附件访问已受限，请联系支持核对原提报。",
                    "created_at": row["created_at"].isoformat(),
                }
            )
        return {"page": page, "page_size": 10, "total": total, "entries": entries}

    @contextmanager
    def operation(self, principal, request_id: str, attempt_id: str, request=None, destination=None):
        scope = (principal.tenant_id, principal.user_id, request_id)
        key = "template-feedback:" + ":".join(scope)
        # Session lock spans external I/O, not a transaction. All state writes
        # use this same connection: loss of the guard cannot authorize a write.
        with self.store._conn() as guard:
            locked = guard.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()[0]
            try:
                with guard.cursor(row_factory=dict_row) as cur:
                    cur.execute("SELECT * FROM product.template_feedback WHERE tenant_id = %s AND created_by = %s AND request_id = %s ORDER BY attempt_number DESC", scope)
                    attempts = cur.fetchall()
                    row = next((item for item in attempts if str(item["attempt_id"]) == attempt_id), None)
                    if request is not None:
                        if attempts and attempts[0]["payload"]["request"] != request:
                            raise ValueError("feedback_request_conflict")
                        if not row and locked:
                            if attempts and attempts[0]["phase"] != "failed":
                                raise ValueError("feedback_prior_attempt_unconfirmed")
                            if attempts and attempts[0]["payload"]["destination"] != destination and attempts[0]["payload"]["tokens"]:
                                raise ValueError("feedback_destination_changed")
                            files = []
                            for version_id in request["file_version_ids"]:
                                cur.execute("SELECT * FROM product.file_versions WHERE id = %s AND tenant_id = %s AND status = 'ready'", (version_id, principal.tenant_id))
                                file = cur.fetchone()
                                resource = self.store.resource(str(file["file_id"])) if file else None
                                if not file or not resource or not can_read(principal, resource, self.store.grants(str(file["file_id"]), principal.user_id)):
                                    raise KeyError("missing")
                                if (
                                    file["byte_size"] <= 0
                                    or file["byte_size"] > 200 * 1024 * 1024
                                    or file["original_filename"].rsplit(".", 1)[-1].lower()
                                    not in {"zip", "xlsx", "xls", "xlsm", "doc", "docx", "png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff"}
                                ):
                                    raise ValueError("feedback_attachment_invalid")
                                files.append(
                                    {
                                        name: str(file[name]) if name in {"id", "file_id"} else file[name]
                                        for name in ("id", "file_id", "original_filename", "content_type", "byte_size", "sha256", "object_key")
                                    }
                                )
                            payload = {"request": request, "destination": destination, "files": files, "operation_id": str(uuid.uuid4()), "tokens": {}}
                            if attempts and attempts[0]["payload"]["destination"] == destination:
                                payload.update({name: attempts[0]["payload"][name] for name in ("operation_id", "tokens")})
                            cur.execute(
                                """INSERT INTO product.template_feedback(tenant_id, created_by, request_id, attempt_id, attempt_number, phase, payload)
                                VALUES (%s, %s, %s, %s, %s, 'preparing', %s) RETURNING *""",
                                (*scope, attempt_id, len(attempts) + 1, Jsonb(payload)),
                            )
                            row = cur.fetchone()
                    if row and attempts and str(attempts[0]["attempt_id"]) != attempt_id:
                        row["superseded"] = True
                    yield row, guard if locked else None
            finally:
                if locked and not guard.closed:
                    guard.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    def save(self, guard, row: dict, phase: str, *, submission_id=None, reason=None) -> dict:
        with guard.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """UPDATE product.template_feedback SET phase = %s, payload = %s, submission_id = %s, reason = %s,
                received_at = CASE WHEN %s = 'received' THEN COALESCE(received_at, now()) ELSE received_at END
                WHERE tenant_id = %s AND created_by = %s AND request_id = %s AND attempt_id = %s RETURNING *""",
                (phase, Jsonb(row["payload"]), submission_id, reason, phase, row["tenant_id"], row["created_by"], row["request_id"], row["attempt_id"]),
            )
            return cur.fetchone()
