"""API owner pytest fixtures."""

from __future__ import annotations

import os
import uuid

import pytest
from product import build_product_store
from product.db import connect
from product.files import ObjectExists, ObjectMissing
from product.fixtures import create_test_database, test_database_name
from product.settings import ISOLATED_DEV_SECRET, Settings
from product.store import AuthStore
from psycopg import sql
from psycopg.rows import dict_row

# Personal models have no built-in endpoint; tests approve their controlled one.
os.environ.setdefault("PPAP_TENANT_MODEL_ALLOWED_BASE_URLS", "https://models.example.test")

_CI = bool(os.environ.get("CI"))
# Tests reset the whole schema, so each test process owns a database it creates and migrates
# itself; the isolated-dev `ppap` database is never connected to, cloned or truncated.
os.environ["PPAP_PG_DATABASE"] = test_database_name()
_CREATED: set[str] = set()


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
    if not settings.pg_database.startswith("ppap_test_"):
        raise RuntimeError(f"refusing to truncate non-test database {settings.pg_database!r}")
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
    settings.pg_database = test_database_name()
    if settings.pg_database not in _CREATED:
        create_test_database(settings.pg_host, settings.pg_port)
        _CREATED.add(settings.pg_database)
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
                (provider, tenant_key, ai_item_id, consumed_points, source, recorded_at)
            VALUES ('feishu', %s, %s, %s, 'test fixture', now())
            ON CONFLICT (provider, tenant_key, ai_item_id)
            DO UPDATE SET consumed_points = EXCLUDED.consumed_points,
                          source = EXCLUDED.source,
                          recorded_at = EXCLUDED.recorded_at
            """,
            (tenant_key, ai_item_id, int(consumed_points)),
        )


@pytest.fixture
def auth_store_factory():
    return make_auth_store


@pytest.fixture
def seed_usage_event_factory():
    return insert_usage_event


@pytest.fixture
def set_trial_baseline_factory():
    return set_trial_baseline
