"""Product integration pytest fixtures."""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from ppap_application import build_product_store
from ppap_core.db import connect
from ppap_core.files import ObjectExists, ObjectMissing, ObjectTooLarge
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

    def get(self, key: str, *, max_bytes: int | None = None) -> bytes:
        if key not in self.data:
            raise ObjectMissing()
        body = self.data[key]
        if max_bytes is not None and len(body) > max_bytes:
            raise ObjectTooLarge()
        return body

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
def mcp_app(auth_store_factory):
    from ppap_api import create_app
    from ppap_core.fixtures import APP_ID

    settings = Settings(mcp_enabled=True, feishu_app_id=APP_ID, usage_charge_enabled=False)
    store = auth_store_factory(settings)
    store.seed_isolated_dev()
    store = build_product_store(settings, objects=store.objects, role="api")
    return create_app(settings, auth_store=store), store


@pytest.fixture
def seed_usage_event_factory():
    return insert_usage_event


# A fixture-only model boundary shared by integration and browser acceptance.
# Load by location because pytest importlib mode intentionally excludes repo-root imports.
_feedback_spec = importlib.util.spec_from_file_location("ppap_feedback_test_model", Path(__file__).with_name("deep_feedback_model.py"))
_feedback_model = importlib.util.module_from_spec(_feedback_spec)
_feedback_spec.loader.exec_module(_feedback_model)
CANDIDATE_ID = _feedback_model.CANDIDATE_ID
EXPLANATION = _feedback_model.EXPLANATION
QUESTION_ID = _feedback_model.QUESTION_ID
RULES = _feedback_model.RULES
FeedbackModel = _feedback_model.FeedbackModel
source_bytes = _feedback_model.source_bytes


def change_payload(view, uploaded, *, intent="save_and_analyze"):
    return {
        "operation_id": str(uuid.uuid4()),
        "expected_revision": view["revision"],
        "question_id": QUESTION_ID,
        "expected_question_fingerprint": view["clarifications"][0]["fingerprint"],
        "candidate_id": CANDIDATE_ID,
        "kind": "interpretation",
        "content": EXPLANATION,
        "scope": {"document_ids": [uploaded["CP"]["id"]], "rule_ids": ["R1", "R2"]},
        "intent": intent,
        "confirmation_source": "synthetic",
    }
