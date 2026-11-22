"""Durable audit-task state. One row per task; every change is a compare-and-swap on its revision."""

from __future__ import annotations

import uuid
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from product.identity import Principal
from product.mcp import McpError, fingerprint


class WorkflowStore:
    def __init__(self, product):
        self.product = product

    @contextmanager
    def connection(self):
        with self.product._conn() as conn:
            conn.row_factory = dict_row
            with conn.transaction():
                yield conn

    def create(self, principal: Principal, task_id: str, request: dict, state: dict) -> dict:
        """Create the task this request asks for, or return the one it already created. The same ID with another
        request is a conflict, never a second task."""
        ident, digest = str(uuid.UUID(task_id)), fingerprint(request)
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO product.audit_workflows(id, tenant_id, created_by, request_fingerprint, source_ref, state)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""",
                (ident, principal.tenant_id, principal.user_id, digest, state["source"]["token"], Jsonb(state)),
            )
            row = conn.execute("SELECT * FROM product.audit_workflows WHERE id=%s", (ident,)).fetchone()
        if str(row["tenant_id"]) != principal.tenant_id or str(row["created_by"]) != principal.user_id:
            raise McpError("access_denied", 403)
        if row["request_fingerprint"] != digest:
            raise McpError("request_conflict", 409)
        return self.public(row)

    def load(self, principal: Principal, task_id: str) -> dict | None:
        """The task for a member of its enterprise; only its creator may change it (`mine`)."""
        try:
            ident = str(uuid.UUID(task_id))
        except ValueError:
            raise McpError("task_not_found", 404) from None
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM product.audit_workflows WHERE id=%s AND tenant_id=%s", (ident, principal.tenant_id)).fetchone()
        return None if row is None else {**self.public(row), "mine": str(row["created_by"]) == principal.user_id}

    def save(self, principal: Principal, task_id: str, revision: int, state: dict) -> bool:
        """Replace the state if nobody changed it since `revision`. False means: re-read and decide again."""
        with self.connection() as conn:
            done = conn.execute(
                """UPDATE product.audit_workflows SET state=%s, revision=revision+1, updated_at=now()
                   WHERE id=%s AND tenant_id=%s AND created_by=%s AND revision=%s""",
                (Jsonb(state), task_id, principal.tenant_id, principal.user_id, revision),
            )
            return done.rowcount == 1

    def open_shared(self, principal: Principal, task_id: str, state: dict) -> None:
        """The enterprise's one task for a source (a rulebook version being clarified): created by whoever asks first."""
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO product.audit_workflows(id, tenant_id, created_by, request_fingerprint, source_ref, state)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""",
                (task_id, principal.tenant_id, principal.user_id, fingerprint(state["source"]), state["source"]["token"], Jsonb(state)),
            )

    def save_shared(self, principal: Principal, task_id: str, revision: int, state: dict) -> bool:
        """Replace a shared task's state if nobody changed it since `revision`. Who may change what is the caller's check."""
        with self.connection() as conn:
            done = conn.execute(
                "UPDATE product.audit_workflows SET state=%s, revision=revision+1, updated_at=now() WHERE id=%s AND tenant_id=%s AND revision=%s",
                (Jsonb(state), task_id, principal.tenant_id, revision),
            )
            return done.rowcount == 1

    def by_source(self, principal: Principal, source_ref: str, limit: int = 5) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT id, created_at FROM product.audit_workflows WHERE tenant_id=%s AND created_by=%s AND source_ref=%s ORDER BY created_at DESC LIMIT %s",
                (principal.tenant_id, principal.user_id, source_ref, limit),
            ).fetchall()
        return [{"task_id": str(row["id"]), "created_at": row["created_at"].isoformat()} for row in rows]

    @staticmethod
    def public(row: dict) -> dict:
        return {"id": str(row["id"]), "revision": row["revision"], "state": row["state"], "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat()}
