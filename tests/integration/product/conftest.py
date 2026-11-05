"""Product integration pytest fixtures."""

from __future__ import annotations

import os
import sys
import threading
import traceback
import uuid

import psycopg
import pytest
from product import build_product_store
from product.db import connect
from product.files import ObjectExists, ObjectMissing, ObjectTooLarge
from product.settings import ISOLATED_DEV_SECRET, Settings
from product.store import AuthStore
from psycopg import sql
from psycopg.rows import dict_row

_CI = bool(os.environ.get("CI"))
_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER")
if _XDIST_WORKER:
    # Tests reset the whole schema, so each xdist worker owns one database in the shared container.
    os.environ["PPAP_PG_DATABASE"] = f"ppap_{_XDIST_WORKER}"


def pytest_sessionstart(session: pytest.Session) -> None:
    """Clone the migrated database for each worker before any of them connects to it."""
    workers = session.config.getoption("numprocesses", None)
    if _XDIST_WORKER or not workers or not os.environ.get("PGPORT"):
        return
    with psycopg.connect(host="127.0.0.1", port=int(os.environ["PGPORT"]), dbname="postgres", user="ppap", password=ISOLATED_DEV_SECRET, autocommit=True) as conn:
        for index in range(workers):
            database = sql.Identifier(f"ppap_gw{index}")
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(database))
            conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE ppap").format(database))


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
    settings.pg_database = f"ppap_{_XDIST_WORKER}" if _XDIST_WORKER else "ppap"
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
    from api import create_app
    from product.fixtures import APP_ID

    settings = Settings(mcp_enabled=True, feishu_app_id=APP_ID, usage_charge_enabled=False)
    store = auth_store_factory(settings)
    store.seed_isolated_dev()
    store = build_product_store(settings, objects=store.objects, role="api")
    return create_app(settings, auth_store=store), store


@pytest.fixture
def seed_usage_event_factory():
    return insert_usage_event


EXPLANATION = "本控制计划中的作业单元编号表示工序编号；按原始编号逐字对应，不合并不同编号。"
QUESTION_ID = "cp-operation-code"
CANDIDATE_ID = "operation-code"


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


@pytest.fixture(scope="session")
def v4_objects(tmp_path_factory):
    """Real existing Silo/S3 boundary for V4 routing and browser acceptance."""
    import subprocess
    from pathlib import Path

    from product.storage import S3Objects, ensure_bucket

    root = Path(__file__).resolve().parents[3]
    override = tmp_path_factory.mktemp("v4-object-service") / "ports.yaml"
    override.write_text('services:\n  silo:\n    ports:\n      - "127.0.0.1:0:9000"\n', encoding="utf-8")
    project = "ppap-v4-objects-" + uuid.uuid4().hex[:12]
    compose = ["docker", "compose", "-p", project, "-f", str(root / "deploy/isolated-dev/compose.yaml"), "-f", str(override)]
    try:
        started = subprocess.run([*compose, "up", "-d", "--wait", "--wait-timeout", "120", "--no-build", "silo"], capture_output=True, text=True, timeout=180)
        assert started.returncode == 0, "V4 S3 service did not start: " + started.stderr[-2500:]
        binding = subprocess.run([*compose, "port", "silo", "9000"], capture_output=True, text=True, check=True, timeout=15).stdout.strip()
        assert binding.startswith("127.0.0.1:") and binding.rsplit(":", 1)[1].isdigit(), binding
        settings = Settings(s3_endpoint="http://" + binding, s3_bucket="ppap-v4-tests")
        ensure_bucket(settings)
        yield S3Objects(settings)
    finally:
        subprocess.run([*compose, "down", "--volumes", "--remove-orphans"], capture_output=True, timeout=60)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """A browser test's failure says what the worker and dev server were doing at that moment."""
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed or not hasattr(item, "web_log"):
        return
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    stacks = (f"--- {names.get(ident, ident)}\n{''.join(traceback.format_stack(frame))}" for ident, frame in sys._current_frames().items() if ident != threading.get_ident())
    report.sections.append(("Thread stacks at failure", "\n".join(stacks)))
    report.sections.append(("Web server log", item.web_log.read_text(errors="replace")[-4000:]))
