"""Product integration pytest fixtures."""

from __future__ import annotations

import os
import uuid

import pytest
from ppap_application import build_product_store
from ppap_core.db import connect
from ppap_core.files import ObjectExists, ObjectMissing
from ppap_core.settings import ISOLATED_DEV_SECRET, Settings
from ppap_product_store import AuthStore
from psycopg import sql
from psycopg.rows import dict_row

_CI = bool(os.environ.get("CI"))


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
    with connect(settings, "migrator") as conn:
        tables = conn.execute(
            """
            SELECT schemaname, tablename
            FROM pg_tables
            WHERE schemaname IN ('product', 'engine')
            ORDER BY schemaname, tablename
            """
        ).fetchall()
        targets = [sql.Identifier(schema, table) for schema, table in tables if not (schema == "product" and table in {"schema_migrations", "trial_cutover_baselines"})]
        if targets:
            conn.execute(sql.SQL("TRUNCATE TABLE {} CASCADE").format(sql.SQL(", ").join(targets)))
        conn.execute("DELETE FROM product.trial_cutover_baselines WHERE tenant_key LIKE 'isolated-dev-%'")


def make_auth_store(
    settings: Settings | None = None,
    objects=None,
    *,
    reset: bool = True,
    role: str = "migrator",
) -> AuthStore:
    if (_CI or os.environ.get("CI")) and not os.environ.get("PGPORT"):
        raise RuntimeError("CI database fixture requires an explicit PGPORT; refusing default database")
    settings = settings or Settings()
    settings.pg_host = "127.0.0.1"
    settings.pg_port = int(os.environ.get("PGPORT", "5432"))
    settings.pg_database = "ppap"
    settings.pg_migrator_user = "ppap_migrator"
    settings.pg_migrator_password = ISOLATED_DEV_SECRET
    if reset:
        reset_product_database(settings)
    return build_product_store(settings, objects=objects or MemoryObjects(), role=role)


def insert_usage_event(
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
    event_id = str(uuid.uuid4())
    with store._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        if trace_id:
            cur.execute(
                """SELECT * FROM product.usage_events
                   WHERE tenant_id = %s AND trace_id = %s
                   LIMIT 1""",
                (principal.tenant_id, trace_id),
            )
            existing = cur.fetchone()
            if existing is not None:
                return store.usage_event_public(existing)
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


@pytest.fixture
def auth_store_factory():
    return make_auth_store


@pytest.fixture
def seed_usage_event_factory():
    return insert_usage_event
