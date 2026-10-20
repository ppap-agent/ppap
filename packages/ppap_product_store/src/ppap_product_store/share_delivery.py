"""Persistent recipient attempts for report delivery."""

import uuid
from collections.abc import Mapping
from contextlib import contextmanager

from psycopg.types.json import Jsonb


def target_key(target: Mapping) -> str:
    return target["kind"] + ":" + target["id"]


def new_share_attempt(target: dict, request_id: str, attempt_id: str) -> dict:
    # Preserve the original namespace for existing report retries.
    provider_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ppap-report-share:{request_id}:{target_key(target)}:{attempt_id}"))
    return {"target": target, "attempt_id": attempt_id, "provider_uuid": provider_uuid, "phase": "queued", "reason": None, "message_id": None}


class ShareDeliveryStore:
    def __init__(self, store, kind):
        if kind != "report_share_request":
            raise ValueError("invalid_share_kind")
        self.store, self.kind = store, kind

    @contextmanager
    def operation(self, principal, request_id):
        key = self.kind.removesuffix("_request").replace("_", "-") + ":" + request_id
        with self.store._conn() as guard:
            locked = guard.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()[0]
            try:
                row = guard.execute("SELECT tenant_id, kind, payload, result FROM engine.jobs WHERE id = %s", (request_id,)).fetchone()
                if row and (str(row[0]) != principal.tenant_id or row[1] != self.kind or row[2]["created_by"] != principal.user_id):
                    raise KeyError("missing")
                yield ({"payload": row[2], "result": row[3]} if row else None), guard if locked else None
            finally:
                if locked and not guard.closed:
                    guard.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    def record(self, guard, principal, command, targets, snapshot):
        payload = {**snapshot, "request": command, "created_by": principal.user_id, "app_id": principal.app_id, "organization_id": principal.tenant_key}
        result = {"attempts": {target_key(target): [new_share_attempt(target, command["request_id"], command["request_id"])] for target in targets}}
        guard.execute(
            "INSERT INTO engine.jobs (id, tenant_id, kind, payload, status, result) VALUES (%s, %s, %s, %s, 'succeeded', %s)",
            (command["request_id"], principal.tenant_id, self.kind, Jsonb(payload), Jsonb(result)),
        )
        return {"payload": payload, "result": result}

    def save(self, guard, row):
        # The physical connection owns both the lock and every dispatch fence.
        guard.execute("UPDATE engine.jobs SET result = %s, updated_at = now() WHERE id = %s AND kind = %s", (Jsonb(row["result"]), row["payload"]["request"]["request_id"], self.kind))

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
        for target in row["payload"]["request"]["targets"]:
            key = target_key(target)
            attempts = histories[key]
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

    def shared(self, principal, request_id):
        with self.store._conn() as conn:
            row = conn.execute("SELECT payload FROM engine.jobs WHERE id = %s AND tenant_id = %s AND kind = %s AND status = 'succeeded'", (request_id, principal.tenant_id, self.kind)).fetchone()
        if not row or row[0]["app_id"] != principal.app_id or row[0]["organization_id"] != principal.tenant_key:
            raise KeyError("missing")
        return row[0]
