"""Meego plugin host: connect tickets, identity bindings and Bearer host sessions.

A binding ties one Meego identity (plugin, Meego tenant, user_key) to one
existing Feishu provider authorization. It is created only after the person
confirms it in a PPAP browser page that already carries their PPAP session.
Host sessions are ordinary product.sessions rows with channel 'meego'; they are
accepted only as Bearer tokens and stop working when the binding is revoked.
"""

from __future__ import annotations

import hmac
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from psycopg.rows import dict_row

from product.identity import Principal, Session, new_token, token_hash
from product.meego import WorkItemRef

TICKET_TTL = timedelta(minutes=10)
HOST_SESSION_TTL = timedelta(hours=2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MeegoConnectError(ValueError):
    pass


class MeegoStore:
    def __init__(self, product):
        self.product = product

    @contextmanager
    def connection(self):
        with self.product._conn() as conn:
            conn.row_factory = dict_row
            with conn.transaction():
                yield conn

    # Connect tickets -----------------------------------------------------

    def create_ticket(self, plugin_id: str, meego_tenant_key: str, user_key: str, meego_name: str, feishu_tenant_key: str) -> tuple[str, datetime]:
        raw = new_token()
        expires_at = _now() + TICKET_TTL
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO product.meego_connect_tickets
                   (ticket_hash, plugin_id, meego_tenant_key, user_key, meego_name, feishu_tenant_key, expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (token_hash(raw), plugin_id, meego_tenant_key, user_key, meego_name[:200], feishu_tenant_key, expires_at),
            )
        return raw, expires_at

    def _open_ticket(self, conn, raw: str, *, lock: bool = False) -> dict:
        row = conn.execute(
            "SELECT * FROM product.meego_connect_tickets WHERE ticket_hash=%s" + (" FOR UPDATE" if lock else ""),
            (token_hash(raw),),
        ).fetchone()
        if not row:
            raise MeegoConnectError("connect_ticket_invalid")
        if row["consumed_at"] is not None:
            raise MeegoConnectError("connect_ticket_used")
        if row["expires_at"] <= _now():
            raise MeegoConnectError("connect_ticket_expired")
        return row

    def connect_view(self, raw: str, browser: str, principal: Principal) -> dict:
        """Check the ticket against the signed-in PPAP person and issue a one-time confirm token."""
        csrf = new_token()
        with self.connection() as conn:
            row = self._open_ticket(conn, raw, lock=True)
            if row["feishu_tenant_key"] != principal.tenant_key:
                raise MeegoConnectError("connect_tenant_mismatch")
            conn.execute(
                "UPDATE product.meego_connect_tickets SET csrf_hash=%s, browser_hash=%s WHERE ticket_hash=%s",
                (token_hash(csrf), token_hash(browser), row["ticket_hash"]),
            )
        return {"meego_name": row["meego_name"], "csrf": csrf}

    def confirm(self, raw: str, browser: str, csrf: str, session: Session) -> str:
        principal = session.principal
        with self.connection() as conn:
            row = self._open_ticket(conn, raw, lock=True)
            if not row["csrf_hash"] or not hmac.compare_digest(row["csrf_hash"], token_hash(csrf)) or not hmac.compare_digest(row["browser_hash"] or "", token_hash(browser)):
                raise MeegoConnectError("connect_confirmation_invalid")
            if row["feishu_tenant_key"] != principal.tenant_key:
                raise MeegoConnectError("connect_tenant_mismatch")
            if not session.provider_authorization_id:
                raise MeegoConnectError("connect_login_required")
            identity = (row["plugin_id"], row["meego_tenant_key"], row["user_key"])
            previous = conn.execute(
                """UPDATE product.meego_bindings SET revoked_at=now()
                   WHERE plugin_id=%s AND meego_tenant_key=%s AND user_key=%s AND revoked_at IS NULL RETURNING id""",
                identity,
            ).fetchall()
            for old in previous:
                conn.execute("UPDATE product.sessions SET revoked_at=now() WHERE meego_binding_id=%s AND revoked_at IS NULL", (old["id"],))
            binding_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO product.meego_bindings
                   (id, plugin_id, meego_tenant_key, user_key, meego_name, user_id, tenant_id, provider_authorization_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (binding_id, *identity, row["meego_name"], principal.user_id, principal.tenant_id, session.provider_authorization_id),
            )
            conn.execute(
                "UPDATE product.meego_connect_tickets SET consumed_at=now(), binding_id=%s WHERE ticket_hash=%s",
                (binding_id, row["ticket_hash"]),
            )
        return binding_id

    # Bindings and host sessions -----------------------------------------

    def active_binding(self, plugin_id: str, meego_tenant_key: str, user_key: str) -> dict | None:
        with self.connection() as conn:
            return conn.execute(
                """SELECT b.*, a.refresh_expires_at, a.access_expires_at, COALESCE(a.refresh_token, '') <> '' AS refreshable
                   FROM product.meego_bindings b
                   JOIN product.provider_authorizations a ON a.id=b.provider_authorization_id
                   WHERE b.plugin_id=%s AND b.meego_tenant_key=%s AND b.user_key=%s AND b.revoked_at IS NULL""",
                (plugin_id, meego_tenant_key, user_key),
            ).fetchone()

    def create_host_session(self, binding: dict) -> tuple[str, datetime]:
        raw = new_token()
        expires_at = _now() + HOST_SESSION_TTL
        bound = binding.get("refresh_expires_at") if binding.get("refreshable") else binding.get("access_expires_at")
        if bound is not None and bound < expires_at:
            expires_at = bound
        if expires_at <= _now():
            raise MeegoConnectError("connect_login_required")
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO product.sessions (token_hash, user_id, tenant_id, expires_at, provider_authorization_id, channel, meego_binding_id)
                   SELECT %s, b.user_id, b.tenant_id, %s, b.provider_authorization_id, 'meego', b.id
                   FROM product.meego_bindings b WHERE b.id=%s AND b.revoked_at IS NULL""",
                (token_hash(raw), expires_at, binding["id"]),
            )
        return raw, expires_at

    def load_host_session(self, raw: str) -> tuple[Session, dict] | None:
        with self.connection() as conn:
            row = conn.execute(
                """SELECT s.user_id, s.tenant_id, s.expires_at, a.access_token, a.refresh_token,
                          a.access_expires_at, a.refresh_expires_at, s.provider_authorization_id,
                          i.provider, i.app_id, i.tenant_key, i.subject, m.role, i.display_name,
                          b.id AS binding_id, b.user_key, b.meego_tenant_key, b.plugin_id, b.meego_name
                   FROM product.sessions s
                   JOIN product.meego_bindings b ON b.id=s.meego_binding_id AND b.revoked_at IS NULL
                        AND b.user_id=s.user_id AND b.tenant_id=s.tenant_id
                   JOIN product.tenants t ON t.id=s.tenant_id
                   JOIN product.provider_authorizations a ON a.id=s.provider_authorization_id
                        AND a.user_id=s.user_id AND a.tenant_id=s.tenant_id
                   JOIN product.identities i ON i.user_id=s.user_id AND i.provider=t.provider AND i.tenant_key=t.external_key
                        AND i.provider=a.provider AND i.app_id=a.app_id AND i.subject=a.subject
                   JOIN product.memberships m ON m.user_id=s.user_id AND m.tenant_id=s.tenant_id
                   WHERE s.token_hash=%s AND s.channel='meego' AND s.revoked_at IS NULL AND s.expires_at > now()
                   LIMIT 1""",
                (token_hash(raw),),
            ).fetchone()
        if not row:
            return None
        principal = Principal(str(row["user_id"]), str(row["tenant_id"]), row["provider"], row["app_id"], row["tenant_key"], row["subject"], row["role"], row["display_name"])
        session = Session(
            principal,
            row["expires_at"],
            row["access_token"] or "",
            row["refresh_token"] or "",
            row["access_expires_at"],
            row["refresh_expires_at"],
            str(row["provider_authorization_id"]),
        )
        binding = {
            "id": str(row["binding_id"]),
            "user_key": row["user_key"],
            "meego_tenant_key": row["meego_tenant_key"],
            "plugin_id": row["plugin_id"],
            "meego_name": row["meego_name"],
        }
        return session, binding

    def revoke_binding(self, binding_id: str, user_id: str) -> bool:
        with self.connection() as conn:
            row = conn.execute(
                "UPDATE product.meego_bindings SET revoked_at=now() WHERE id=%s AND user_id=%s AND revoked_at IS NULL RETURNING id",
                (binding_id, user_id),
            ).fetchone()
            conn.execute("UPDATE product.sessions SET revoked_at=now() WHERE meego_binding_id=%s AND revoked_at IS NULL", (binding_id,))
        return row is not None

    # AI nodes ------------------------------------------------------------

    def record_ai_node(self, item: WorkItemRef, state_key: str, status: str, *, outside: bool = False) -> None:
        """Keep one AI node's latest status. A repeated event writes the same row again.

        Completing a node also reports its AI status turning done; arriving late, that report
        never takes a completed node back to done. ``outside`` marks a completion PPAP did not
        make; any later status (the flow back at the node) clears it.
        """
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO product.meego_ai_nodes (project_key, work_item_id, state_key, work_item_type_key, status, completed_outside)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (project_key, work_item_id, state_key)
                   DO UPDATE SET status=CASE WHEN product.meego_ai_nodes.status='completed' AND EXCLUDED.status='done'
                                             THEN 'completed' ELSE EXCLUDED.status END,
                                 completed_outside=CASE WHEN product.meego_ai_nodes.status='completed' AND EXCLUDED.status='done'
                                                        THEN product.meego_ai_nodes.completed_outside ELSE EXCLUDED.completed_outside END,
                                 work_item_type_key=EXCLUDED.work_item_type_key, updated_at=now()""",
                (item.project_key, int(item.work_item_id), state_key, item.work_item_type_key, status, outside and status == "completed"),
            )

    def ai_nodes(self, item: WorkItemRef) -> list[dict]:
        """The PPAP AI nodes of one work item, most recently changed first."""
        with self.connection() as conn:
            return conn.execute(
                """SELECT state_key, status, completed_outside, updated_at FROM product.meego_ai_nodes
                   WHERE project_key=%s AND work_item_id=%s ORDER BY updated_at DESC""",
                (item.project_key, int(item.work_item_id)),
            ).fetchall()

    # Releases --------------------------------------------------------------

    def release_round(self, principal: Principal, round_id: str, item: WorkItemRef, reason: str) -> dict:
        """Record one person's release of a round's PPAP node. A repeated request returns the first release."""
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO product.meego_node_releases (round_id, tenant_id, object_ref, released_by, released_by_name, reason)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (round_id) DO NOTHING""",
                (round_id, principal.tenant_id, item.object_ref(), principal.user_id, principal.display_name[:200], reason),
            )
            return self._release(conn, principal.tenant_id, round_id) or {}

    def round_release(self, tenant_id: str, round_id: str) -> dict | None:
        with self.connection() as conn:
            return self._release(conn, tenant_id, round_id)

    def round_releases(self, tenant_id: str, round_ids: list[str]) -> dict[str, dict]:
        if not round_ids:
            return {}
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT round_id, reason, created_at, released_by_name FROM product.meego_node_releases
                   WHERE tenant_id=%s AND round_id = ANY(%s::uuid[])""",
                (tenant_id, round_ids),
            ).fetchall()
        return {str(row["round_id"]): row for row in rows}

    def _release(self, conn, tenant_id: str, round_id: str) -> dict | None:
        return conn.execute(
            """SELECT round_id, object_ref, reason, created_at, released_by, released_by_name
               FROM product.meego_node_releases WHERE round_id=%s AND tenant_id=%s""",
            (round_id, tenant_id),
        ).fetchone()

    # Work-item targets ---------------------------------------------------

    def set_task_target(self, principal: Principal, task_id: str, item: WorkItemRef) -> None:
        """Attach the work item as the task's writeback target; acceptance copies it to the round."""
        self.product.set_task_writeback(principal, task_id, {"provider": "meego", "object_kind": "work_item", "object_ref": item.object_ref()})
