"""Fixed template ACL commands and receipts, stored in the existing job table."""

from contextlib import contextmanager

from psycopg.types.json import Jsonb


class TemplatePermissionChangeStore:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def operation(self, principal, request_id):
        key = "template-permission:" + request_id
        with self.store._conn() as guard:
            locked = guard.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()[0]
            try:
                saved = guard.execute("SELECT tenant_id, kind, payload, result FROM engine.jobs WHERE id = %s", (request_id,)).fetchone()
                if saved and (
                    str(saved[0]) != principal.tenant_id
                    or saved[1] != "template_permission_change"
                    or saved[2]["created_by"] != principal.user_id
                    or saved[2]["app_id"] != principal.app_id
                    or saved[2]["organization_id"] != principal.tenant_key
                ):
                    raise KeyError("missing")
                yield ({"payload": saved[2], "result": saved[3]} if saved else None), guard if locked else None
            finally:
                if locked and not guard.closed:
                    guard.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    @contextmanager
    def folder(self, guard, folder_id):
        key = "template-permission-folder:" + folder_id
        locked = guard.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()[0]
        try:
            yield locked
        finally:
            if locked and not guard.closed:
                guard.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    def accept(self, guard, principal, command):
        payload = {"request": command, "created_by": principal.user_id, "app_id": principal.app_id, "organization_id": principal.tenant_key}
        result = {"phase": "preparing", "state": "pending", "result_revision": None, "reason": None}
        guard.execute(
            "INSERT INTO engine.jobs (id, tenant_id, kind, payload, status, result) VALUES (%s, %s, 'template_permission_change', %s, 'succeeded', %s)",
            (command["request_id"], principal.tenant_id, Jsonb(payload), Jsonb(result)),
        )
        return {"payload": payload, "result": result}

    def save(self, guard, row):
        # The same physical connection owns the advisory locks and every write.
        guard.execute("UPDATE engine.jobs SET result = %s, updated_at = now() WHERE id = %s AND kind = 'template_permission_change'", (Jsonb(row["result"]), row["payload"]["request"]["request_id"]))

    def recover(self, guard, row):
        phase = row["result"]["phase"]
        if phase in {"preparing", "dispatching"}:
            row["result"].update(
                phase="rejected" if phase == "preparing" else "unknown",
                state="rejected" if phase == "preparing" else "unknown",
                reason="修改准备已中断，未向飞书提交。" if phase == "preparing" else "未取得原修改回执，请勿重复修改。",
            )
            self.save(guard, row)

    def unresolved(self, principal, folder_id, exclude=None, *, guard=None):
        def read(conn):
            return conn.execute(
                """SELECT payload, result FROM engine.jobs WHERE tenant_id = %s AND kind = 'template_permission_change'
                AND payload->'request'->'folder'->>'folder_id' = %s AND result->>'state' IN ('pending', 'unknown')
                AND (%s::uuid IS NULL OR id <> %s::uuid) ORDER BY created_at LIMIT 1""",
                (principal.tenant_id, folder_id, exclude, exclude),
            ).fetchone()

        if guard is not None:
            row = read(guard)
        else:
            with self.store._conn() as conn:
                row = read(conn)
        if not row:
            return None, False
        owned = row[0]["created_by"] == principal.user_id and row[0]["app_id"] == principal.app_id and row[0]["organization_id"] == principal.tenant_key
        return (self.public({"payload": row[0], "result": row[1]}) if owned else None), True

    @staticmethod
    def public(row):
        return {"request": row["payload"]["request"], **{key: row["result"][key] for key in ("state", "result_revision", "reason")}}
