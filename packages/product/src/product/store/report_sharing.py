"""One existing engine record owns a frozen share and its recipient attempts."""

import hashlib

from psycopg.types.json import Jsonb

from product.mcp import ApprovedCall
from product.store.share_delivery import ShareDeliveryStore


class ReportShareStore(ShareDeliveryStore):
    def __init__(self, store):
        super().__init__(store, "report_share_request")

    def accept(self, guard, principal, command: dict, targets: list[dict], *, approval: ApprovedCall | None = None) -> dict:
        with guard.transaction():
            sources = []
            for reference in command["reports"]:
                task = self.store._recognition_public(guard, principal, reference["task_id"])
                if approval is not None:
                    approval.require_source(f"/audit-tasks/{reference['task_id']}", task)
                source = self.store._report_source(guard, principal, task, reference)
                file = next(row for row in task["files"] if row["file"]["id"] == reference["file_version_id"])
                sources.append({**source, "name": file["file"]["original_filename"], "conclusion": file["audit"]["conclusion"]})
            content = command["content"]
            archive = None
            if content["kind"] == "pdf":
                if len(sources) != 1 or content["artifact"] != sources[0]["artifact"] or content["package_id"] is not None:
                    raise ValueError("share_artifact_conflict")
            elif content["kind"] == "zip":
                row = guard.execute(
                    """SELECT id, payload FROM engine.jobs WHERE tenant_id = %s AND kind = 'report_zip_request'
                    AND payload->>'created_by' = %s AND payload->>'package_id' = %s ORDER BY created_at DESC LIMIT 1""",
                    (principal.tenant_id, principal.user_id, content["package_id"]),
                ).fetchone()
                if not row:
                    raise KeyError("missing")
                task = self.store._recognition_public(guard, principal, row[1]["task_id"])
                package = self.store._report_package_public(guard, principal, task, str(row[0]), row[1])
                if (
                    package["state"] != "ready"
                    or package["artifact"] != content["artifact"]
                    or sorted(package["reports"], key=lambda ref: ref["report_id"]) != sorted(command["reports"], key=lambda ref: ref["report_id"])
                ):
                    raise ValueError("share_artifact_conflict")
                saved = guard.execute("SELECT result FROM engine.jobs WHERE id = %s AND tenant_id = %s AND kind = 'report_zip'", (content["package_id"], principal.tenant_id)).fetchone()
                archive = {"artifact": package["artifact"], "object_key": saved[0]["object_key"], "sha256": saved[0]["sha256"]}
            elif content["artifact"] is not None or content["package_id"] is not None:
                raise ValueError("share_artifact_conflict")
            if content["kind"] != "link" and content["artifact"]["byte_size"] > 30 * 1024 * 1024:
                raise ValueError("share_file_too_large")
            return self.record(guard, principal, command, targets, {"sources": sources, "archive": archive})

    def latest_for_task(self, conn, principal, task):
        row = conn.execute(
            """SELECT payload, result FROM engine.jobs WHERE tenant_id = %s AND kind = 'report_share_request'
            AND payload->>'created_by' = %s AND payload->'request'->'reports' @> %s
            ORDER BY created_at DESC, id DESC LIMIT 1""",
            (principal.tenant_id, principal.user_id, Jsonb([{"task_id": task["id"]}])),
        ).fetchone()
        if not row:
            return None
        for reference in row[0]["request"]["reports"]:
            file = next((file for file in task["files"] if file["file"]["id"] == reference["file_version_id"]), None)
            if reference["task_id"] != task["id"] or reference["round_id"] != task["round_id"] or not file or not file.get("audit") or file["audit"]["result_attempt"] != reference["result_attempt"]:
                return None
        return self.public({"payload": row[0], "result": row[1]})

    def read_bytes(self, source):
        body = self.store.objects.get(source["object_key"])
        if len(body) != source["artifact"]["byte_size"] or hashlib.sha256(body).hexdigest() != source["sha256"]:
            raise ValueError("share_artifact_conflict")
        return body
