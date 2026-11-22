"""Product integration pytest fixtures."""

from __future__ import annotations

import os
import sys
import threading
import traceback
import uuid
from pathlib import Path

import pytest
from agent.execution.guest_model import ModelGrant
from product import build_product_store
from product.db import connect
from product.fixtures import create_test_database, test_database_name
from product.settings import ISOLATED_DEV_SECRET, Settings
from product.store import AuthStore
from psycopg import sql
from psycopg.rows import dict_row

# Personal models have no built-in endpoint; tests approve their controlled one.
os.environ.setdefault("PPAP_TENANT_MODEL_ALLOWED_BASE_URLS", "https://models.example.test")

# Tests reset the whole schema, so each test process owns a database it creates and migrates
# itself; the isolated-dev `ppap` database is never connected to, cloned or truncated.
os.environ["PPAP_PG_DATABASE"] = test_database_name()
_CREATED: set[str] = set()
_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER")


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


def insert_usage_event(store: AuthStore, principal, ai_item_id: str, consumed_points: int, is_free_trial: bool, external_status: str) -> dict:
    with store._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO product.usage_events (id, tenant_id, user_id, ai_item_id, consumed_points, is_free_trial, external_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (str(uuid.uuid4()), principal.tenant_id, principal.user_id, ai_item_id, int(consumed_points), bool(is_free_trial), external_status),
        )
        return store.usage_event_public(cur.fetchone())


@pytest.fixture
def auth_store_factory(memory_objects_type):
    def make(settings: Settings | None = None, objects=None, *, reset: bool = True, role: str = "migrator") -> AuthStore:
        if os.environ.get("CI") and not os.environ.get("PGPORT"):
            raise RuntimeError("CI database fixture requires an explicit PGPORT; refusing default database")
        settings = settings or Settings()
        settings.pg_host = "127.0.0.1"
        settings.pg_port = int(os.environ.get("PGPORT", "5432"))
        settings.pg_database = test_database_name()
        if settings.pg_database not in _CREATED:
            create_test_database(settings.pg_host, settings.pg_port, Path(__file__).resolve().parents[3] / "migrations")
            _CREATED.add(settings.pg_database)
        settings.pg_migrator_user = "ppap_migrator"
        settings.pg_migrator_password = ISOLATED_DEV_SECRET
        if reset:
            reset_product_database(settings)
        return build_product_store(settings, objects=objects or memory_objects_type(), role=role)

    return make


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


@pytest.fixture(scope="session")
def v4_objects():
    """Use suite-owned Silo without changing host networking while browsers run."""
    from botocore.exceptions import ClientError
    from product.storage import S3Objects, s3_client

    endpoint = os.environ.get("PPAP_TEST_S3_ENDPOINT")
    if not endpoint:
        raise RuntimeError("Real S3 tests require PPAP_TEST_S3_ENDPOINT; run python -m scripts.ci_shards integration or start the services documented in tests/README.md")
    # Each worker keeps its own object namespace, just as it owns its database.
    # UUID also isolates repeated direct pytest sessions against an existing Silo.
    settings = Settings(s3_endpoint=endpoint, s3_bucket=f"ppap-v4-{_XDIST_WORKER or 'local'}-{uuid.uuid4().hex[:12]}")
    client = s3_client(settings)
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket)
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
    from agent.run import Controller, ExecutionContext
    from agent.source import authorize_execution, object_file_source
    from audit.checker_core import runtime_revision
    from audit.standard_suites import library_revision
    from product.fixtures import RULE_A
    from product.store.suite_releases import SuiteReleaseStore, current_selection
    from product.store.suites import SuiteStore

    class ControlledCore:
        def __init__(self):
            self.calls = 0
            self.audit_jobs = []
            self.guest_process = False

        def ready(self, operation="audit"):
            return True

        def run(self, job, inputs, work, deadline, cancelled, on_progress=None, on_activity=None):
            assert not cancelled.is_set()
            self.calls += 1
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
                            **ModelGrant.fields_for(identity),
                            "wall_seconds": 100,
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

        def install(self, store, *, guest_process=False):
            self.guest_process = guest_process

            class ControlledRuntime(CoreRuntime):
                def execute_snapshot(self, *args, **kwargs):
                    # Integration tests never reach a default model credential.
                    kwargs["allow_model_calls"] = False
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
                # As in production: the Controller's own configuration, captured when it starts,
                # with the model identity frozen at acceptance.
                executor_revision_for_request=lambda spec, started=settings.model_copy(): runtime_revision(started.deep_execution_revision(spec.model_identity), library_revision()),
                authorize=authorize_execution(settings, connect=connect, open_store=lambda _: worker),
            )

            def execute(_client, spec, *, cancelled=None, on_progress=None, on_activity=None):
                context = ExecutionContext()
                if cancelled is not None:
                    context.cancelled = cancelled
                if on_progress is not None:
                    context.on_progress = on_progress
                if on_activity is not None:
                    context.on_activity = on_activity
                return self.controller.handle(spec, context)

            monkeypatch.setattr(DeepClient, "execute", execute)
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
            candidate = releases.suites.create_revision(
                tenant_id=principal.tenant_id,
                suite_key=resolved.revision.suite_key,
                files={"rules.json": json.dumps(rules, ensure_ascii=False).encode(), "checker.py": code.encode()},
                created_by=principal.user_id,
                base_revision_id=resolved.revision.id,
                related_suite_keys=resolved.revision.related_suite_keys,
            )
            return releases.publish(principal, body["suite_key"], candidate.id, selected["head_revision"], str(uuid.uuid4()), force_unverified=True)

        @staticmethod
        def adopt_standard(store, principal, *keys):
            """Adopt the directory Checker for a new audit. Historical carriers are not a new-audit source."""
            from audit.standard_suites import predefined_directory_suites

            baselines = {suite.suite_key: suite for suite in predefined_directory_suites()}
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
