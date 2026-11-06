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
def mcp_app(auth_store_factory, core_system):
    from api import create_app
    from product.fixtures import APP_ID

    settings = Settings(mcp_enabled=True, feishu_app_id=APP_ID, usage_charge_enabled=False, git_sha="a" * 40)
    store = auth_store_factory(settings)
    store.seed_isolated_dev()
    core_system.install(store)
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
def v4_objects():
    """Use suite-owned Silo without changing host networking while browsers run."""
    from product.storage import S3Objects, ensure_bucket

    endpoint = os.environ.get("PPAP_TEST_S3_ENDPOINT")
    if not endpoint:
        raise RuntimeError("Real S3 tests require PPAP_TEST_S3_ENDPOINT; run python -m scripts.ci_shards integration or start the services documented in tests/README.md")
    # Each worker keeps its own object namespace, just as it owns its database.
    # UUID also isolates repeated direct pytest sessions against an existing Silo.
    settings = Settings(s3_endpoint=endpoint, s3_bucket=f"ppap-v4-{_XDIST_WORKER or 'local'}-{uuid.uuid4().hex[:12]}")
    ensure_bucket(settings)
    return S3Objects(settings)


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


@pytest.fixture
def core_system(monkeypatch, tmp_path):
    """Explicit product/Core fixture, substituting RPC and guest OS boundaries only.

    Admission, immutable Suite bytes, original lookup, Controller authorization,
    Core execution, evidence mapping, and leased persistence stay production code.
    This is not a claim of socket, container, provider, or process isolation.
    """
    import json
    import subprocess
    import time

    from agent.client import DeepClient
    from agent.core_program import CoreRuntime, run_audit_manifest
    from agent.precheck_program import execute_precheck, run_precheck_manifest
    from agent.protocol import PrecheckEnvelope
    from agent.run import Controller, ExecutionContext
    from agent.source import authorize_execution, object_file_source
    from audit.checker_core import runtime_revision
    from audit.standard_suites import library_revision
    from product.fixtures import RULE_A
    from product.store.suite_releases import SuiteReleaseStore, current_selection
    from product.store.suites import SuiteStore
    from worker.precheck_controller import precheck_handler

    class ControlledCore:
        def __init__(self):
            self.calls = 0
            self.audit_jobs = []
            self.guest_process = False

        def ready(self, operation="audit"):
            return True

        def run(self, job, inputs, work, deadline, cancelled):
            assert not cancelled.is_set()
            self.calls += 1
            if job.operation == "execute-precheck":
                run_precheck_manifest(inputs, work)
                return
            self.audit_jobs.append(job)
            grants = tmp_path / f"grants-{self.calls}"
            grants.mkdir()
            if job.allow_model_calls:
                identity = json.loads((inputs / "manifest.json").read_text())["snapshot"]["model_identity"]
                (grants / "model.json").write_text(
                    json.dumps(
                        {
                            "base_url": "http://127.0.0.1:1/v1",
                            "token": "controlled-no-provider-credential-123456",
                            **identity,
                            "wall_seconds": 100,
                            "max_turns": 20,
                        }
                    )
                )
            if self.guest_process:
                # Production enters the guest on its own process's main thread.
                # Browser workers run in a background thread; inlining the guest
                # there makes SIGALRM clause deadlines fail before every check.
                # Preserve the real entrypoint/deadlines, without claiming the
                # local test process provides container or privilege isolation.
                env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "VIRTUAL_ENV"}}
                env["PYTHONPATH"] = os.pathsep.join(sys.path)
                subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import sys; from pathlib import Path; from agent.execution.guest import run_entrypoint; run_entrypoint(*map(Path, sys.argv[1:]))",
                        str(inputs),
                        str(work),
                        str(grants),
                    ],
                    check=True,
                    timeout=max(0.001, deadline - time.monotonic()),
                    env=env,
                    capture_output=True,
                )
            else:
                run_audit_manifest(inputs, work, grants)

        def execute_precheck(self, payload, snapshot, sources, programs, context):
            return execute_precheck(self, payload, snapshot, sources, programs, context)

        def install(self, store, *, controlled_model=False, guest_process=False):
            if guest_process and controlled_model:
                raise ValueError("Controlled model transport patches cannot cross the guest process boundary")
            self.guest_process = guest_process

            class ControlledRuntime(CoreRuntime):
                def execute_snapshot(self, *args, **kwargs):
                    # Model capability is opt-in only when the test provides a
                    # controlled provider boundary, never a default credential.
                    kwargs["allow_model_calls"] = controlled_model and kwargs.get("allow_model_calls", True)
                    return super().execute_snapshot(*args, **kwargs)

            settings = store.settings
            settings.deep_controller_socket = "/controlled-product-core-rpc"
            worker = build_product_store(settings, objects=store.objects, role="worker")
            suites = SuiteStore(settings, store.objects, role="worker")
            runtime = ControlledRuntime(self, lambda spec: suites.read_snapshot(spec.round_id, spec.tenant_id), suites.resolve_suite)
            self.controller = Controller(
                runtime,
                object_file_source(settings, connect=connect, objects=store.objects),
                executor_revision=runtime_revision(settings.deep_execution_revision(), library_revision()),
                authorize=authorize_execution(settings, connect=connect, open_store=lambda _: worker),
                precheck_handler=precheck_handler(worker, self),
            )

            def execute(_client, spec, *, cancelled=None, on_progress=None):
                context = ExecutionContext()
                if cancelled is not None:
                    context.cancelled = cancelled
                if on_progress is not None:
                    context.on_progress = on_progress
                return self.controller.handle(spec, context)

            def precheck(_client, request, *, cancelled=None):
                context = ExecutionContext()
                if cancelled is not None:
                    context.cancelled = cancelled
                return self.controller.precheck_handler(PrecheckEnvelope.model_validate(request), context)

            monkeypatch.setattr(DeepClient, "execute", execute)
            monkeypatch.setattr(DeepClient, "precheck", precheck)
            return self

        def adopt(self, store, rule_id, code, binding=None, *, subject="alice"):
            """Explicitly adopt this test's authored, unverified implementation."""
            principal = store.principal_for_fixture(subject)
            releases = SuiteReleaseStore(store.settings, store.objects, role="api")
            with store._conn() as conn:
                body = conn.execute("SELECT body FROM product.rule_versions WHERE id=%s", (rule_id,)).fetchone()[0]
                selected = current_selection(conn, principal.tenant_id, body["suite_key"])
            resolved = releases.suites.resolve_suite(selected["revision_id"], principal.tenant_id)
            rules = json.loads(resolved.files["rules.json"])
            rules["checker_binding"] = binding if binding is not None else {str(row["id"]): "checker:check" for row in rules["rules"]}
            candidate = releases.save_candidate(principal, selected["revision_id"], {"rules.json": json.dumps(rules, ensure_ascii=False), "checker.py": code})
            return releases.publish(principal, body["suite_key"], candidate["id"], selected["head_revision"], str(uuid.uuid4()), force_unverified=True)

        @staticmethod
        def adopt_standard(store, principal, *keys):
            """Use real standard source under an explicit unverified adoption."""
            from audit.standard_suites import standard_suites

            baselines = {suite.suite_key: suite for suite in standard_suites()}
            suites = SuiteStore(store.settings, store.objects, role="migrator")
            releases = SuiteReleaseStore(store.settings, store.objects, role="api")
            installed = {}

            def adopt(key):
                if key in installed:
                    return
                baseline = baselines[key]
                for related in baseline.related_suite_keys:
                    adopt(related)
                revision = suites.create_revision(tenant_id=None, suite_key=key, files=dict(baseline.files), created_by=None, related_suite_keys=baseline.related_suite_keys)
                installed[key] = revision
                releases.publish(principal, key, revision.id, 0, str(uuid.uuid4()), force_unverified=True)

            for key in keys:
                adopt(key)
            return installed

        @staticmethod
        def workbook(value="source"):
            from io import BytesIO

            from openpyxl import Workbook

            book = Workbook()
            book.active["A1"] = value
            stream = BytesIO()
            book.save(stream)
            book.close()
            return stream.getvalue()

        def adopt_presence(self, store, rule_id=RULE_A):
            return self.adopt(
                store,
                rule_id,
                """import json
from io import BytesIO
from openpyxl import load_workbook
from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource

def check(context):
    book = load_workbook(BytesIO(context.sources[context.audit_file.audit_id]), read_only=True)
    try:
        sheet = book.active
        present = bool(sheet["A1"].value)
        locator = json.dumps({"kind": "xlsx_cell", "sheet": sheet.title, "cell": "A1"})
        return AuditResultItem(classification="资料", item="原件资料已经就绪", priority="P0",
            status="PASS" if present else "NG", suggestions="已核对冻结原件A1。", clause_id=context.clause_id, checks_performed=1,
            evidence=AuditEvidence(required="structure", used="structure", source_format="xlsx", prepared_format="xlsx",
                sources=(EvidenceSource(kind="structure", locator=locator),)))
    finally:
        book.close()
""",
            )

    return ControlledCore()
