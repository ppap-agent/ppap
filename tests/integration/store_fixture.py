from __future__ import annotations

import uuid

from psycopg import sql
from psycopg.rows import dict_row

from ppap_api.store import AuthStore
from ppap_core.db import connect
from ppap_core.files import ObjectExists, ObjectMissing
from ppap_core.settings import Settings


class MemoryObjects:
    """Small in-memory fake for the object-storage boundary only."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def put_exclusive(self, key: str, body: bytes, content_type: str) -> None:
        if key in self.data:
            raise ObjectExists()
        self.data[key] = body

    def get(self, key: str) -> bytes:
        if key not in self.data:
            raise ObjectMissing()
        return self.data[key]

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


def reset_product_database(settings: Settings) -> None:
    """Reset mutable product state while preserving migration/baseline authority."""
    with connect(settings, "migrator") as conn:
        tables = conn.execute(
            """
            SELECT schemaname, tablename
            FROM pg_tables
            WHERE schemaname IN ('product', 'engine')
            ORDER BY schemaname, tablename
            """
        ).fetchall()
        targets = [
            sql.Identifier(schema, table)
            for schema, table in tables
            if not (
                schema == "product"
                and table in {"schema_migrations", "trial_cutover_baselines"}
            )
        ]
        if targets:
            conn.execute(
                sql.SQL("TRUNCATE TABLE {} CASCADE").format(sql.SQL(", ").join(targets))
            )
        conn.execute(
            "DELETE FROM product.trial_cutover_baselines WHERE tenant_key LIKE 'isolated-dev-%'"
        )


def auth_store(
    settings: Settings | None = None,
    objects=None,
    *,
    reset: bool = True,
    role: str = "migrator",
) -> AuthStore:
    """Return the production AuthStore against the prepared test PostgreSQL."""
    settings = settings or Settings()
    if reset:
        reset_product_database(settings)
    return AuthStore(settings, objects or MemoryObjects(), role=role)


def seed_usage_event(
    store: AuthStore,
    principal,
    ai_item_id: str,
    consumed_points: int,
    is_free_trial: bool,
    external_status: str,
    *,
    trace_id: str | None = None,
    round_id: str | None = None,
) -> dict:
    """Insert explicit fixture data without reimplementing usage business rules."""
    event_id = str(uuid.uuid4())
    with store._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO product.usage_events
                (id, tenant_id, user_id, ai_item_id, consumed_points, is_free_trial,
                 external_status, trace_id, round_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                event_id,
                principal.tenant_id,
                principal.user_id,
                ai_item_id,
                int(consumed_points),
                bool(is_free_trial),
                external_status,
                trace_id,
                round_id,
            ),
        )
        row = cur.fetchone()
    return store.usage_event_public(row)


def set_trial_baseline(
    store: AuthStore,
    tenant_key: str,
    ai_item_id: str,
    consumed_points: int,
) -> None:
    with store._conn() as conn:
        conn.execute(
            """
            INSERT INTO product.trial_cutover_baselines
                (provider, tenant_key, ai_item_id, consumed_points)
            VALUES ('feishu', %s, %s, %s)
            ON CONFLICT (provider, tenant_key, ai_item_id)
            DO UPDATE SET consumed_points = EXCLUDED.consumed_points
            """,
            (tenant_key, ai_item_id, int(consumed_points)),
        )
