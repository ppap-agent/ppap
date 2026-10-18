"""One existing engine record owns a frozen share and its recipient attempts."""

import hashlib
import uuid
from collections.abc import Mapping
from contextlib import contextmanager

from psycopg.types.json import Jsonb


def target_key(target: Mapping) -> str:
    return target["kind"] + ":" + target["id"]


def new_share_attempt(target: dict, request_id: str, attempt_id: str) -> dict:
    provider_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ppap-report-share:{request_id}:{target_key(target)}:{attempt_id}"))
    return {"target": target, "attempt_id": attempt_id, "provider_uuid": provider_uuid, "phase": "queued", "reason": None, "message_id": None}


class ReportShareStore:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def operation(self, principal, request_id):
        key = "report-share:" + request_id
        with self.store._conn() as guard:
            locked = guard.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()[0]
            try:
                row = guard.execute("SELECT tenant_id, kind, payload, result FROM engine.jobs WHERE id = %s", (request_id,)).fetchone()
                if row and (str(row[0]) != principal.tenant_id or row[1] != "report_share_request" or row[2]["created_by"] != principal.user_id):
                    raise KeyError("missing")
                yield ({"payload": row[2], "result": row[3]} if row else None), guard if locked else None
            finally:
                if locked and not guard.closed:
                    guard.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    def accept(self, guard, principal, command: dict, targets: list[dict]) -> dict:
        with guard.transaction():
            sources = []
            for reference in command["reports"]:
                task = self.store._recognition_public(guard, principal, reference["task_id"])
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
            payload = {"request": command, "created_by": principal.user_id, "app_id": principal.app_id, "organization_id": principal.tenant_key, "sources": sources, "archive": archive}
            result = {"attempts": {target_key(target): [new_share_attempt(target, command["request_id"], command["request_id"])] for target in targets}}
            guard.execute(
                "INSERT INTO engine.jobs (id, tenant_id, kind, payload, status, result) VALUES (%s, %s, 'report_share_request', %s, 'succeeded', %s)",
                (command["request_id"], principal.tenant_id, Jsonb(payload), Jsonb(result)),
            )
        return {"payload": payload, "result": result}

    def save(self, guard, row):
        # Every transition uses the same physical connection that owns the lock.
        # Losing it cannot authorize a dispatch using a replacement connection.
        guard.execute("UPDATE engine.jobs SET result = %s, updated_at = now() WHERE id = %s AND kind = 'report_share_request'", (Jsonb(row["result"]), row["payload"]["request"]["request_id"]))

    def recover(self, guard, row):
        changed = False
        for attempts in row["result"]["attempts"].values():
            latest = attempts[-1]
            if latest["phase"] in {"queued", "preparing"}:
                latest.update(phase="failed", reason="发送准备已中断，尚未向接收对象发送消息。")
                changed = True
            elif latest["phase"] == "dispatching":
                latest.update(phase="unknown", reason="发送结果尚未确认，请勿重新发送。")
                changed = True
        if changed:
            self.save(guard, row)

    def public(self, row, selected=None):
        deliveries = []
        histories = row["result"]["attempts"]
        if selected is not None and (len(selected) != len(histories) or {target_key(item["target"]) for item in selected} != set(histories)):
            raise ValueError("invalid_share_attempts")
        for key, attempts in histories.items():
            attempt_id = next(item["attempt_id"] for item in selected if target_key(item["target"]) == key) if selected is not None else attempts[-1]["attempt_id"]
            attempt = next((item for item in attempts if item["attempt_id"] == attempt_id), None)
            if not attempt:
                raise KeyError("missing")
            phase = attempt["phase"]
            state = phase if phase in {"sent", "failed", "unknown"} else "unknown" if phase == "dispatching" else "pending"
            deliveries.append(
                {
                    "target": attempt["target"],
                    "attempt_id": attempt_id,
                    "state": state,
                    "reason": attempt["reason"],
                    "retry_allowed": phase == "failed" and attempt is attempts[-1] and attempt["target"]["eligibility"] == "eligible",
                }
            )
        command = row["payload"]["request"]
        return {"request": command, "receipt": {"request_id": command["request_id"], "state": "accepted", "reason": None}, "deliveries": deliveries}

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

    def shared(self, principal, request_id):
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT payload FROM engine.jobs WHERE id = %s AND tenant_id = %s AND kind = 'report_share_request' AND status = 'succeeded'", (request_id, principal.tenant_id)
            ).fetchone()
        if not row or row[0]["app_id"] != principal.app_id or row[0]["organization_id"] != principal.tenant_key:
            raise KeyError("missing")
        return row[0]

    def read_bytes(self, source):
        body = self.store.objects.get(source["object_key"])
        if len(body) != source["artifact"]["byte_size"] or hashlib.sha256(body).hexdigest() != source["sha256"]:
            raise ValueError("share_artifact_conflict")
        return body
