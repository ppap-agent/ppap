"""Per-target rules and acceptance concurrency against actual PostgreSQL."""

import os
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest


class AuditDatabaseCase(unittest.TestCase):
    previous_schema = False

    @pytest.fixture(autouse=True)
    def _schema_support(self, schema_tools, memory_objects_type, sample_rule_body_factory):
        self.schema = schema_tools
        self.memory_objects_type = memory_objects_type
        self.rule_body = sample_rule_body_factory

    def setUp(self):
        from product import build_product_store
        from product.settings import Settings

        self.schema.require_postgres()
        self.database = "ppap_bindings_" + uuid.uuid4().hex[:12]
        # Cloning the migrated database is far cheaper than replaying every migration per test.
        template = "" if self.previous_schema else " TEMPLATE ppap"
        self.schema.require(
            self.schema.psql(self.schema.bootstrap_user(), self.schema.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {self.database} OWNER {self.schema.migrator_user()}{template}"),
            "create isolated audit DB",
        )  # noqa: E501
        self.addCleanup(
            lambda: self.schema.require(
                self.schema.psql(self.schema.bootstrap_user(), self.schema.bootstrap_password(), "postgres", "-c", f"DROP DATABASE {self.database} WITH (FORCE)"), "drop isolated audit DB"
            )
        )  # noqa: E501
        if self.previous_schema:
            for migration in self.schema.migration_files():
                if migration.name >= "0010":
                    break
                self.schema.require(self.schema.psql(self.schema.migrator_user(), self.schema.migrator_password(), self.database, "-f", str(migration)), "apply prior audit schema")
        self.settings = Settings(
            usage_charge_enabled=False,
            git_sha="a" * 40,
            pg_database=self.database,
            pg_host=os.environ.get("PGHOST", "127.0.0.1"),
            pg_port=int(os.environ.get("PGPORT", "5432")),
            pg_migrator_user=self.schema.migrator_user(),
            pg_migrator_password=self.schema.migrator_password(),
        )  # noqa: E501
        self.store = build_product_store(self.settings, objects=self.memory_objects_type())
        if not self.previous_schema:
            # Other owners may have seeded the template DB with object references.
            # This test has a fresh object store, so clone schema, not their data.
            from product.db import connect
            from psycopg import sql

            with connect(self.settings, "migrator") as conn:
                tables = conn.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname IN ('product','engine')").fetchall()
                names = [sql.Identifier(schema, table) for schema, table in tables if table not in {"schema_migrations", "trial_cutover_baselines"}]
                conn.execute(sql.SQL("TRUNCATE TABLE {} CASCADE").format(sql.SQL(", ").join(names)))
        self.store.seed_isolated_dev()
        self.alice = self.store.principal_for_fixture("alice")
        publish = self.store.publish_rule

        def publish_with_program(principal, *args, **kwargs):
            # Uploaded rules reach audits only with an adopted program; give each an unbound one.
            rule = publish(principal, *args, **kwargs)
            self.adopt_unbound_program(principal, rule)
            return rule

        self.store.publish_rule = publish_with_program

    def adopt_unbound_program(self, principal, rule):
        from product.db import connect
        from product.store.suite_releases import SuiteReleaseStore
        from product.store.suites import programless_suite_keys

        body = rule.get("body") or rule
        key = body.get("suite_key")
        if not isinstance(key, str) or not key.startswith("custom:"):
            return
        with connect(self.settings, "api") as conn:
            if not programless_suite_keys(conn, principal.tenant_id, [key]):
                return
            head = conn.execute("SELECT current_revision_id, revision FROM product.tenant_suite_heads WHERE tenant_id=%s AND suite_key=%s", (principal.tenant_id, key)).fetchone()
        releases = SuiteReleaseStore(self.settings, self.store.objects, role="api")
        program = releases.suites.create_revision(
            tenant_id=principal.tenant_id,
            suite_key=key,
            # Binds no clause and runs no code, like the starter; it is only not the starter.
            files={"checker.py": b'"""Unbound test program."""\n'},
            created_by=principal.user_id,
            base_revision_id=str(head[0]),
            related_suite_keys=(),
        )
        releases.publish(principal, key, program.id, head[1], str(uuid.uuid4()), True)

    def upload(self, name):
        version = self.store.create_staging(self.alice, name, "application/octet-stream", 1)
        self.store.put_blob(version, b"x")
        return self.store.finalize_version(version)

    def draft(self, items):
        draft = self.store.create_draft(self.alice)
        self.store.save_draft(self.alice, draft["id"], items)
        return draft["id"]


class NativeEntryDatabaseTests(AuditDatabaseCase):
    def test_native_entry_keeps_the_frozen_task_after_authenticated_edition_selection(self):
        from urllib.parse import parse_qs, urlsplit

        from api import create_app
        from fastapi.testclient import TestClient
        from product.identity import SESSION_COOKIE

        self.settings.feishu_app_id = "cli_test"
        self.settings.feishu_app_secret = "test-secret"
        client = TestClient(create_app(settings=self.settings, auth_store=self.store))
        target = "http://127.0.0.1:4173/audit?task=frozen-task&report=frozen-file"
        response = client.get("/enter-current", params={"return_to": target}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        resume = parse_qs(urlsplit(response.headers["location"]).query)["return_to"][0]
        self.assertEqual(urlsplit(resume).path, "/enter-current")
        self.assertEqual(parse_qs(urlsplit(resume).query)["return_to"], [target])
        client.cookies.set(SESSION_COOKIE, self.store.create_session(self.alice))
        response = client.get("/enter-current", params={"return_to": target}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], target)
        self.assertEqual(response.cookies.get("ppap_edition"), "current")
        foreign = client.get("/enter-current?return_to=https://foreign.example/audit", follow_redirects=False)
        self.assertEqual(foreign.headers["location"], "http://127.0.0.1:4173/tasks/new")
        loop = client.get("/enter-current", params={"return_to": "http://127.0.0.1:4173/enter-current"}, follow_redirects=False)
        self.assertEqual(loop.headers["location"], "http://127.0.0.1:4173/tasks/new")
        self.settings.current_tenant_allowlist = "unrelated-tenant"
        self.assertEqual(client.get("/enter-current", params={"return_to": target}, follow_redirects=False).status_code, 404)


class AuditBindingDatabaseTests(AuditDatabaseCase):
    def test_unsupported_worker_job_is_failed_not_successful_delivery(self):
        from product.db import connect
        from product.store import AuthStore
        from worker import process_claimed

        job_id = str(uuid.uuid4())
        with connect(self.settings, "migrator") as conn:
            conn.execute(
                "INSERT INTO engine.jobs (id, tenant_id, kind, status) VALUES (%s, %s, 'unimplemented_delivery', 'queued')",
                (job_id, self.alice.tenant_id),
            )
        worker = AuthStore(self.settings, role="worker")
        claimed = worker.claim_job("unsupported-job-test")
        self.assertEqual(claimed.id, job_id)
        result = process_claimed(worker, claimed, "unsupported-job-test")
        self.assertEqual(result, {"error": "unsupported_job_kind", "note": "unsupported_job_kind"})
        with connect(self.settings, "migrator") as conn:
            row = conn.execute(
                "SELECT j.status, a.outcome, a.diagnostic FROM engine.jobs j JOIN engine.attempts a ON a.job_id = j.id WHERE j.id = %s",
                (job_id,),
            ).fetchone()
        self.assertEqual(row[0:2], ("failed", "failed"))
        self.assertEqual(row[2]["note"], "unsupported_job_kind")
        self.assertIsNone(worker.claim_job("unsupported-job-test"))

    def test_support_reads_current_session_and_does_not_create_audit_or_usage_records(self):
        from api import create_app
        from fastapi.testclient import TestClient
        from product.identity import SESSION_COOKIE

        self.settings.support_phone = "+00 000 000"
        client = TestClient(create_app(settings=self.settings, auth_store=self.store))
        self.assertEqual(client.get("/support").status_code, 401)
        token = self.store.create_session(self.alice)
        client.cookies.set(SESSION_COOKIE, token)
        before = [client.get(path).json() for path in ("/rounds", "/usage", "/drafts")]
        info = client.get("/support")
        self.assertEqual(info.status_code, 200)
        self.assertEqual(info.json()["phone"], "+00 000 000")
        self.assertEqual(info.json()["state"], "configured")
        self.assertEqual([client.get(path).json() for path in ("/rounds", "/usage", "/drafts")], before)
        self.store.revoke_session(token)
        self.assertEqual(client.get("/support").status_code, 401)

    def test_worker_persists_one_unreadable_file_error_without_losing_other_frozen_file_results(self):
        import json
        from io import BytesIO
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        from agent.core_program import CoreRuntime, run_audit_manifest
        from agent.run import Controller, ExecutionContext
        from agent.source import authorize_execution, object_file_source
        from api.audit_routes import register_audit_routes
        from api.web_contract import Round
        from audit.storage_errors import ObjectTooLarge
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from openpyxl import Workbook
        from product import build_product_store
        from product.db import connect
        from product.store.suite_releases import SuiteReleaseStore
        from product.store.suites import SuiteStore
        from pydantic import TypeAdapter
        from worker import process_claimed

        workbook = Workbook()
        workbook.active.append(["控制计划", "☑批量生产"])
        content = BytesIO()
        workbook.save(content)
        files = []
        for name in ("missing.xlsx", "healthy.xlsx", "last.xlsx"):
            file = self.store.create_staging(self.alice, name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", len(content.getvalue()))
            self.store.put_blob(file, content.getvalue())
            files.append(self.store.finalize_version(file))
        rule = self.store.publish_rule(self.alice, "客户自定义要求", self.rule_body(), files[-1].id)
        releases = SuiteReleaseStore(self.settings, self.store.objects)
        base = releases.suites.resolve_suite(rule["body"]["suite_revision_id"], self.alice.tenant_id)
        rules = json.loads(base.files["rules.json"])
        rules["checker_binding"] = {"1": "checker:check"}
        code = """import json
from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource
from openpyxl import load_workbook

def check(context):
    assert context.audit_file.file_metadata['original_filename'] != 'missing.xlsx'
    workbook = load_workbook(context.audit_file.file_url)
    try:
        value = workbook.active['A1'].value
        sheet = workbook.active.title
    finally:
        workbook.close()
    location = json.dumps({'kind': 'xlsx_cell', 'sheet': sheet, 'cell': 'A1', 'file_version_id': context.audit_file.audit_id})
    evidence = AuditEvidence(required='structure', used='structure', source_format='xlsx', prepared_format='xlsx',
                             sources=(EvidenceSource(kind='structure', locator=location, excerpt=str(value)),))
    return AuditResultItem(classification='基本信息', item='标题检查', priority='P0', status='PASS' if value == '控制计划' else 'NG',
                           suggestions='已读取本轮冻结原件A1', clause_id=context.clause_id, checks_performed=1, evidence=evidence)
"""
        candidate = releases.save_candidate(self.alice, base.revision.id, {"rules.json": json.dumps(rules, ensure_ascii=False), "checker.py": code})
        releases.publish(self.alice, base.revision.suite_key, candidate["id"], releases.active(self.alice, base.revision.suite_key)["revision"], str(uuid.uuid4()), force_unverified=True)
        draft = self.draft([{"file_version_id": file.id, "role": "target", "rule_version_id": rule["id"]} for file in files])
        accepted = self.store.accept_draft(self.alice, draft, "file-isolation")
        self.store.objects.delete(files[0].object_key)
        worker = build_product_store(self.settings, objects=self.store.objects, role="worker")
        claimed = worker.claim_job("file-isolation-test")
        self.assertIsNotNone(claimed)
        suites = SuiteStore(self.settings, self.store.objects, role="worker")
        frozen = suites.read_snapshot(accepted["id"], self.alice.tenant_id)
        self.assertTrue(all(target.verification == "unverified" for target in frozen.targets))
        calls = []

        class BoundedObjects:
            def get(_, key, *, max_bytes):
                data = self.store.objects.get(key)
                if len(data) > max_bytes:
                    raise ObjectTooLarge()
                return data

        class ControlledGuest:
            # Real host/guest manifest and Core execution; this test substitutes
            # transport only and does not claim UID/container isolation.
            def run(_, job, inputs, work, deadline, cancelled):
                calls.append(job)
                with TemporaryDirectory() as root:
                    credentials = Path(root)
                    (credentials / "model.json").write_text(
                        json.dumps(
                            {
                                # The grant names the model; the tenant's key source stays on the host.
                                **{key: value for key, value in dict(frozen.model_identity).items() if key in {"model", "text_model", "vision_model", "provider", "revision", "deployment"}},
                                "base_url": "http://127.0.0.1:1/v1",
                                "token": "controlled-local-capability-1234567890",
                                "wall_seconds": 100,
                            }
                        )
                    )
                    run_audit_manifest(inputs, work, credentials)

        controller = Controller(
            CoreRuntime(ControlledGuest(), lambda spec: suites.read_snapshot(spec.round_id, spec.tenant_id), suites.resolve_suite),
            object_file_source(self.settings, connect=connect, objects=BoundedObjects()),
            executor_revision=frozen.runtime_revision,
            authorize=authorize_execution(self.settings, connect=connect, open_store=lambda _: worker),
        )
        self.settings.deep_controller_socket = "/controlled-test-transport"

        def execute(_, spec, *, cancelled, on_progress):
            return controller.handle(spec, ExecutionContext(cancelled=cancelled, on_progress=on_progress))

        with patch("agent.client.DeepClient.execute", execute):
            process_claimed(worker, claimed, "file-isolation-test")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].unavailable_sources, (files[0].id,))
        self.assertEqual(suites.read_snapshot(accepted["id"], self.alice.tenant_id).to_dict(), frozen.to_dict())

        app = FastAPI()
        register_audit_routes(app, self.store, lambda request: self.alice, Mock())
        client = TestClient(app)
        response = client.get(f"/rounds/{accepted['id']}?read_view=true")
        self.assertEqual(response.status_code, 200)
        observed = response.json()
        TypeAdapter(Round).validate_python(observed)
        self.assertEqual(observed["inputs"], accepted["inputs"])
        self.assertEqual((observed["execution"], observed["result_attempt"]), ("completed", 1))
        audits = {row["file"]["id"]: row["audit"] for row in observed["read_view"]["files"]}
        self.assertEqual([audits[file.id]["execution"] for file in files], ["review_failed", "completed", "completed"])
        self.assertEqual(audits[files[0].id]["counts"]["error_count"], 1)
        self.assertEqual([audits[file.id]["counts"]["not_covered_count"] for file in files[1:]], [0, 0])
        self.assertEqual([audits[file.id]["counts"]["pass_count"] for file in files[1:]], [1, 1])
        self.assertCountEqual([row["status"] for row in observed["findings"]], ["ERR", "PASS", "PASS"])
        self.assertEqual(audits[files[0].id]["report"]["state"], "not_eligible")
        self.assertEqual(client.get(f"/rounds/{accepted['id']}?read_view=true").json(), observed)
        self.assertFalse(worker.complete_job(claimed, "file-isolation-test", {"findings": [], "coverage": {}}, "succeeded"))
        self.assertEqual(client.get(f"/rounds/{accepted['id']}?read_view=true").json(), observed)

    def test_manual_coverage_review_round_trip_preserves_machine_facts_and_rejects_stale_or_outside_input(self):
        from api.audit_routes import register_audit_routes
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from product import build_product_store

        target, reference = self.upload("review.xlsx"), self.upload("context.pdf")
        rule = self.store.publish_rule(self.alice, "人工核对要求", self.rule_body(), reference.id)
        draft = self.draft([{"file_version_id": target.id, "role": "target", "rule_version_id": rule["id"]}, {"file_version_id": reference.id, "role": "reference"}])
        round = self.store.accept_draft(self.alice, draft, "manual-review")
        worker = build_product_store(self.settings, objects=self.store.objects, role="worker")
        claimed = worker.claim_job("review-test")
        self.assertIsNotNone(claimed)
        coverage = {"rule_items": [{"file_version_id": target.id, "id": "1", "coverage": "unsupported", "reason": "没有标准检查器"}]}
        self.assertTrue(worker.complete_job(claimed, "review-test", {"findings": [], "coverage": coverage}))
        principal = [self.alice]
        app = FastAPI()
        register_audit_routes(app, self.store, lambda request: principal[0], Mock())
        client = TestClient(app)
        path = f"/rounds/{round['id']}"
        before = client.get(path).json()
        body = {
            "item_key": f"coverage:{target.id}:1",
            "judgment": "manual-satisfied",
            "rationale": "人工核对原件",
            "next_step": "保留机器观察",
            "version": 0,
            "citations": [{"file_version_id": reference.id, "location": "第 2 页", "excerpt": "每班一次"}],
        }
        for invalid in ({**body, "citations": []}, {**body, "rationale": ""}, {**body, "citations": [{**body["citations"][0], "file_version_id": str(uuid.uuid4())}]}):
            self.assertEqual(client.put(path + "/reviews", json=invalid).status_code, 400)
        saved = client.put(path + "/reviews", json=body)
        self.assertEqual(saved.status_code, 200)
        observed = client.get(path).json()
        review = observed["coverage"]["rule_items"][0]["review"]
        observed["coverage"]["rule_items"][0]["review"] = None
        self.assertEqual(review, {key: value for key, value in {**body, "version": 1}.items() if key != "item_key"})
        self.assertEqual(observed, before)
        self.assertEqual(client.put(path + "/reviews", json=body).status_code, 409)
        principal[0] = self.store.principal_for_fixture("carol")
        self.assertEqual(client.put(path + "/reviews", json={**body, "version": 1}).status_code, 403)

    def test_round_reader_uses_frozen_versions_without_creating_a_task_or_changing_the_round(self):
        from api.audit_routes import register_audit_routes
        from api.web_contract import Round
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from product.fixtures import RULE_A
        from pydantic import TypeAdapter

        target, reference = self.upload("historical.txt"), self.upload("reference.txt")
        draft = self.draft([{"file_version_id": target.id, "role": "target", "rule_version_id": RULE_A}, {"file_version_id": reference.id, "role": "reference"}])
        round = self.store.accept_draft(self.alice, draft, "historical-reader")
        principal = [self.alice]
        app = FastAPI()
        register_audit_routes(app, self.store, lambda request: principal[0], Mock())
        client = TestClient(app)
        path = f"/rounds/{round['id']}"
        before = client.get(path).json()
        tasks_before = self.store.list_audit_tasks(self.alice)
        response = client.get(path + "?read_view=true")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        TypeAdapter(Round).validate_python(body)
        view = body.pop("read_view")
        self.assertEqual(body, before)
        self.assertEqual({row["file"]["id"] for row in view["files"]}, {target.id, reference.id})
        self.assertEqual(next(row["file"] for row in view["files"] if row["role"] == "target"), target.public())
        self.assertIsNone(next(row["audit"] for row in view["files"] if row["role"] == "reference"))
        replacement = self.store.create_staging(self.alice, "historical.txt", "application/octet-stream", 1, file_id=target.file_id)
        self.store.put_blob(replacement, b"y")
        self.store.finalize_version(replacement)
        self.assertEqual(client.get(path + "?read_view=true").json()["read_view"], view)
        self.assertEqual(self.store.list_audit_tasks(self.alice), tasks_before)
        principal[0] = self.store.principal_for_fixture("bob")
        self.assertEqual(client.get(path + "?read_view=true").status_code, 200)
        principal[0] = self.store.principal_for_fixture("carol")
        self.assertEqual(client.get(path + "?read_view=true").status_code, 403)

    def test_resumable_drafts_keep_origin_and_respect_grants(self):
        from product.fixtures import RULE_A

        file = self.upload("draft.txt")
        items = [{"file_version_id": file.id, "role": "target", "rule_version_id": RULE_A}]
        draft = self.draft(items)
        first = self.store.accept_draft(self.alice, draft, "origin")
        next_draft = self.store.create_draft(self.alice, first["id"])["id"]
        self.store.save_draft(self.alice, next_draft, items)
        rows = self.store.list_drafts(self.alice)
        self.assertEqual([row["id"] for row in rows], [next_draft])
        self.assertEqual(rows[0]["predecessor_id"], first["id"])
        self.assertTrue(rows[0]["updated_at"])
        bob = self.store.principal_for_fixture("bob")
        self.assertEqual(self.store.list_drafts(bob), [])
        bob_draft = self.store.create_draft(bob, first["id"])
        self.assertEqual(bob_draft["predecessor_id"], first["id"])
        self.assertEqual(self.store.list_drafts(bob), [])
        accepted = self.store.accept_draft(self.alice, next_draft, "resumed")
        self.assertEqual(accepted["predecessor_id"], first["id"])
        self.assertEqual(self.store.list_drafts(self.alice), [])

    def test_each_target_keeps_its_rule_snapshot_through_replay_and_explicit_resubmit(self):
        from product.fixtures import RULE_A

        first, second, reference = [self.upload(name) for name in ("a.xlsx", "b.xlsx", "context.pdf")]
        rule = self.store.publish_rule(self.alice, "客户规则", self.rule_body(), reference.id)
        items = [
            {"file_version_id": first.id, "role": "target", "rule_version_id": RULE_A},
            {"file_version_id": second.id, "role": "target", "rule_version_id": rule["id"]},
            {"file_version_id": reference.id, "role": "reference"},
        ]  # noqa: E501
        draft = self.draft(items)
        accepted = self.store.accept_draft(self.alice, draft, "per-input")
        by_id = {item["file_version_id"]: item for item in accepted["inputs"]}
        self.assertEqual(by_id[first.id]["rule"]["id"], RULE_A)
        expected_body = {**self.rule_body(), "title": "客户规则", "suite_key": rule["body"]["suite_key"], "checker_binding": {}, "appendix": []}
        self.assertEqual(by_id[second.id]["rule"]["body"], expected_body)
        self.assertIsNone(by_id[reference.id]["rule"])
        self.assertNotIn("rule", accepted)
        self.assertEqual(self.store.accept_draft(self.alice, draft, "per-input")["id"], accepted["id"])
        changed = self.rule_body()
        changed["rules"][0]["rule_to_check"] = "新版检查要求"
        newer = self.store.publish_rule(self.alice, "新版客户规则", changed, reference.id, rule["id"])
        next_items = [{**item, "rule_version_id": newer["id"]} if item["file_version_id"] == second.id else item for item in items]
        next_round = self.store.resubmit_round(self.alice, accepted["id"], "explicit-resubmit", next_items)
        self.assertNotEqual(next_round["id"], accepted["id"])
        self.assertEqual(next_round["predecessor_id"], accepted["id"])
        self.assertEqual(self.store.get_round(accepted["id"])["inputs"], accepted["inputs"])
        # Uploading a new rule is not adoption, even for an explicit re-audit.
        self.assertEqual(next(item for item in next_round["inputs"] if item["file_version_id"] == second.id)["rule"]["body"], expected_body)
        from product.store.suite_releases import SuiteReleaseStore

        releases = SuiteReleaseStore(self.store.settings, self.store.objects, role="api")
        active = releases.active(self.alice, rule["body"]["suite_key"])
        releases.publish(self.alice, rule["body"]["suite_key"], newer["body"]["suite_revision_id"], active["revision"], str(uuid.uuid4()), force_unverified=True)
        adopted_round = self.store.resubmit_round(self.alice, next_round["id"], "explicit-adopted-resubmit", next_items)
        expected_new = {**changed, "title": "新版客户规则", "suite_key": rule["body"]["suite_key"], "checker_binding": {}, "appendix": []}
        self.assertEqual(next(item for item in adopted_round["inputs"] if item["file_version_id"] == second.id)["rule"]["body"], expected_new)
        self.assertEqual(self.store.get_round(accepted["id"])["inputs"], accepted["inputs"])
        self.assertEqual(self.store.get_round(next_round["id"])["inputs"], next_round["inputs"])

    def test_same_key_concurrent_acceptance_and_conflicts_check_bindings_roles_and_round_access(self):
        from product.fixtures import RULE_A

        first, reference = self.upload("a.xlsx"), self.upload("context.xlsx")
        items = [{"file_version_id": first.id, "role": "target", "rule_version_id": RULE_A}, {"file_version_id": reference.id, "role": "reference"}]
        for same_draft in (True, False):
            with self.subTest(same_draft=same_draft):
                first_draft = self.draft(items)
                second_draft = first_draft if same_draft else self.draft(list(reversed(items)))
                key = str(uuid.uuid4())
                start = threading.Barrier(2)

                def accept(draft):
                    start.wait(timeout=5)
                    return self.store.accept_draft(self.alice, draft, key)

                with ThreadPoolExecutor(max_workers=2) as pool:
                    # Both requests must reach a database lock before either can finish.
                    with self.store._conn() as locker:
                        with locker.transaction():
                            locker.execute("SELECT id FROM product.audit_drafts WHERE id = ANY(%s::uuid[]) FOR UPDATE", ([first_draft, second_draft],)).fetchall()
                            futures = [pool.submit(accept, draft) for draft in (first_draft, second_draft)]
                            deadline = time.monotonic() + 5
                            blocked = 0
                            while time.monotonic() < deadline:
                                with self.store._conn() as monitor:
                                    blocked = monitor.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = %s AND wait_event_type = 'Lock'", (self.database,)).fetchone()[0]
                                if blocked == 2:
                                    break
                                time.sleep(0.02)
                            self.assertEqual(blocked, 2)
                    accepted = [future.result(timeout=5) for future in futures]
                self.assertEqual(accepted[0]["id"], accepted[1]["id"])
                with self.store._conn() as conn:
                    self.assertEqual(conn.execute("SELECT count(*) FROM product.audit_rounds WHERE idempotency_key = %s", (key,)).fetchone()[0], 1)
                    self.assertEqual(conn.execute("SELECT count(*) FROM engine.jobs WHERE audit_round_id = %s", (accepted[0]["id"],)).fetchone()[0], 1)

        changed_roles = [{"file_version_id": first.id, "role": "reference"}, {"file_version_id": reference.id, "role": "target", "rule_version_id": RULE_A}]
        with self.assertRaisesRegex(ValueError, "idempotency_conflict"):
            self.store.accept_draft(self.alice, self.draft(changed_roles), key)
        rule = self.store.publish_rule(self.alice, "客户规则", self.rule_body(), reference.id)
        changed_rule = [{**items[0], "rule_version_id": rule["id"]}, items[1]]
        with self.assertRaisesRegex(ValueError, "idempotency_conflict"):
            self.store.accept_draft(self.alice, self.draft(changed_rule), key)
        bob = self.store.principal_for_fixture("bob")
        with self.store._conn() as conn:
            for version in (first, reference):
                conn.execute("INSERT INTO product.resource_grants (resource_id, user_id, capability) VALUES (%s, %s, 'read')", (version.file_id, bob.user_id))
        other_draft = self.store.create_draft(bob)["id"]
        self.store.save_draft(bob, other_draft, items)
        replay = self.store.accept_draft(bob, other_draft, key)
        self.assertEqual(replay["id"], accepted[0]["id"])

    def test_missing_target_rule_reference_rule_and_foreign_rule_are_rejected(self):
        from product.fixtures import RULE_A, RULE_B

        first = self.upload("a.xlsx")
        draft = self.draft([{"file_version_id": first.id, "role": "target"}])
        with self.assertRaisesRegex(ValueError, "rule_required"):
            self.store.accept_draft(self.alice, draft, "missing-binding")
        with self.assertRaisesRegex(ValueError, "reference_rule_forbidden"):
            self.draft([{"file_version_id": first.id, "role": "reference", "rule_version_id": RULE_A}])
        with self.assertRaisesRegex(ValueError, "unknown_rule"):
            self.draft([{"file_version_id": first.id, "role": "target", "rule_version_id": RULE_B}])
        with self.store._conn() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0], 0)


class AuditBindingUpgradeTests(AuditDatabaseCase):
    previous_schema = True

    def test_upgrade_copies_frozen_head_not_current_rule_and_preserves_results(self):
        from audit.audits import request_fingerprint
        from product.files import sha256_bytes, version_object_key
        from product.fixtures import RULE_A
        from psycopg.types.json import Jsonb

        target, reference = str(uuid.uuid4()), str(uuid.uuid4())
        draft, round_id = str(uuid.uuid4()), str(uuid.uuid4())
        frozen = {"kind": "presence", "snapshot": "accepted before upgrade"}
        with self.store._conn() as conn:
            # Seed the previous schema directly; current upload code requires current columns.
            for version_id, name in ((target, "target.xlsx"), (reference, "reference.xlsx")):
                file_id = str(uuid.uuid4())
                object_key = version_object_key(self.alice.tenant_id, file_id, version_id)
                self.store.objects.put_exclusive(object_key, b"x", "application/octet-stream")
                conn.execute("INSERT INTO product.resources (id, tenant_id, kind) VALUES (%s, %s, 'file')", (file_id, self.alice.tenant_id))
                conn.execute("INSERT INTO product.files (id, tenant_id, created_by, original_filename) VALUES (%s, %s, %s, %s)", (file_id, self.alice.tenant_id, self.alice.user_id, name))
                conn.execute(
                    """INSERT INTO product.file_versions (id, file_id, tenant_id, original_filename, content_type, byte_size, sha256, object_key, status, finalized_at)
                    VALUES (%s, %s, %s, %s, 'application/octet-stream', 1, %s, %s, 'ready', now())""",
                    (version_id, file_id, self.alice.tenant_id, name, sha256_bytes(b"x"), object_key),
                )
                conn.execute("INSERT INTO product.resource_grants VALUES (%s, %s, 'read')", (file_id, self.alice.user_id))
            for ident, kind in ((draft, "audit_draft"), (round_id, "audit_round")):
                conn.execute("INSERT INTO product.resources (id, tenant_id, kind) VALUES (%s,%s,%s)", (ident, self.alice.tenant_id, kind))
                conn.execute("INSERT INTO product.resource_grants VALUES (%s,%s,'read')", (ident, self.alice.user_id))
            conn.execute("INSERT INTO product.audit_drafts (id,tenant_id,created_by,rule_version_id,status) VALUES (%s,%s,%s,%s,'accepted')", (draft, self.alice.tenant_id, self.alice.user_id, RULE_A))
            conn.execute(
                "INSERT INTO product.audit_rounds (id,tenant_id,created_by,source_draft_id,rule_version_id,rule_title,rule_body,idempotency_key,request_fingerprint,execution) VALUES (%s,%s,%s,%s,%s,'Frozen title',%s,'old-key','old-fingerprint','completed')",  # noqa: E501
                (round_id, self.alice.tenant_id, self.alice.user_id, draft, RULE_A, Jsonb(frozen)),
            )  # noqa: E501
            for version_id, role in ((target, "target"), (reference, "reference")):
                conn.execute("INSERT INTO product.draft_items VALUES (%s,%s,%s)", (draft, version_id, role))
                conn.execute("INSERT INTO product.round_inputs VALUES (%s,%s,%s)", (round_id, version_id, role))
            conn.execute("INSERT INTO product.findings (id,round_id,item_key,classification,item,status) VALUES (%s,%s,'check','A','Original result','NG')", (str(uuid.uuid4()), round_id))
            conn.execute("INSERT INTO product.reviews (round_id,item_key,user_id,judgment,version) VALUES (%s,'check',%s,'保留人工判断',1)", (round_id, self.alice.user_id))
        for _ in range(2):
            self.schema.require(
                self.schema.psql(self.schema.migrator_user(), self.schema.migrator_password(), self.database, "-f", str(self.schema.MIGRATIONS / "0010_input_rule_bindings.sql")),
                "upgrade input rule snapshots",
            )  # noqa: E501
        self.schema.require(self.schema.apply(self.database), "upgrade to current before using current store")
        loaded = self.store.get_round(round_id)
        by_id = {row["file_version_id"]: row for row in loaded["inputs"]}
        self.assertEqual(by_id[target]["rule"], {"id": RULE_A, "title": "Frozen title", "body": frozen})
        self.assertIsNone(by_id[reference]["rule"])
        self.assertEqual(loaded["findings"][0]["status"], "NG")
        self.assertEqual(loaded["findings"][0]["review"]["judgment"], "保留人工判断")
        self.assertEqual(self.store.accept_draft(self.alice, draft, "old-key")["id"], round_id)
        with self.store._conn() as conn:
            fingerprint = conn.execute("SELECT request_fingerprint FROM product.audit_rounds WHERE id=%s", (round_id,)).fetchone()[0]
        self.assertEqual(fingerprint, request_fingerprint([{"file_version_id": target, "role": "target", "rule_version_id": RULE_A}, {"file_version_id": reference, "role": "reference"}]))


class AuditDisplayDatabaseTests(AuditDatabaseCase):
    @staticmethod
    def card_action(body):
        from product.feishu import CardAction

        event = body["event"]
        return CardAction(event["operator"]["tenant_key"], event["operator"]["open_id"], event["action"]["value"]["notification_id"], event["context"]["open_message_id"])

    def process_card_confirmation(self, store, *, usage_policy=None):
        claimed = store.claim_task_confirmation("card-confirmer")
        self.assertIsNotNone(claimed)
        return store.process_task_confirmation(claimed, "card-confirmer", usage_policy=usage_policy)

    def prepared_card_confirmation(self, *, sent=True):
        from api import create_app
        from fastapi.testclient import TestClient
        from product import build_product_store
        from product.fixtures import APP_ID

        self.settings.feishu_app_id = APP_ID
        self.settings.feishu_verification_token = "callback-test-verification"
        files, task, _ = self.prepared_display_task(confirm=False, per_file_rules=True)
        api_store = build_product_store(self.settings, objects=self.store.objects, role="api")
        intent, recipient = api_store.claim_task_notification("card-notifier")
        delivery = api_store.start_task_notification(intent, "card-notifier", recipient)
        self.assertEqual(delivery["card"]["schema"], "2.0")
        value = delivery["card"]["body"]["elements"][1]["behaviors"][0]["value"]
        self.assertEqual(value, {"action": "confirm_audit_task", "notification_id": intent.id})
        receipt = {"state": "sent", "message_id": "om_confirmation", "reason": None} if sent else {"state": "unknown", "message_id": None, "reason": "send_receipt_unknown"}
        self.assertTrue(api_store.finish_task_notification(intent, "card-notifier", receipt))
        body = {
            "schema": "2.0",
            "header": {"event_type": "card.action.trigger", "token": "callback-test-verification", "app_id": APP_ID},
            "event": {"operator": {"tenant_key": self.alice.tenant_key, "open_id": self.alice.subject}, "action": {"tag": "button", "value": value}, "context": {"open_message_id": "om_confirmation"}},
        }
        client = TestClient(create_app(settings=self.settings, auth_store=api_store))
        self.addCleanup(client.close)
        return files, task, api_store, client, body

    def test_feishu_card_confirms_the_exact_task_and_replays_without_creating_another_round(self):
        from api import create_app
        from fastapi.testclient import TestClient

        files, task, api_store, client, body = self.prepared_card_confirmation()
        response = client.post("/integrations/feishu/events", json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["toast"]["type"], "info")
        pending = api_store.get_audit_task(self.alice, task["id"])
        self.assertEqual(pending["acceptance"]["state"], "pending")
        self.assertIsNone(pending["round_id"])
        receipt = self.process_card_confirmation(api_store)
        self.assertEqual(receipt["state"], "accepted")
        accepted = api_store.get_audit_task(self.alice, task["id"])
        self.assertEqual(accepted["state"], "reviewing")
        inputs = api_store.get_round(accepted["round_id"])["inputs"]
        self.assertEqual({row["file_version_id"]: row["rule"]["id"] for row in inputs}, {row["file"]["id"]: row["match"]["rule"]["id"] for row in task["files"]})
        self.assertEqual(len(inputs), len(files))
        restored = TestClient(create_app(settings=self.settings, auth_store=api_store))
        self.addCleanup(restored.close)
        replay = restored.post("/integrations/feishu/events", json=body)
        self.assertEqual(replay.json()["toast"]["type"], "success")
        self.assertEqual(api_store.get_audit_task(self.alice, task["id"])["round_id"], accepted["round_id"])
        self.assertEqual(len(api_store.list_rounds(self.alice)), 1)
        self.assertEqual(api_store.list_usage_events(self.alice), [])

    def test_feishu_card_rejects_forged_or_forwarded_callbacks_and_current_permission_revocation(self):
        from copy import deepcopy

        from product.db import connect

        _, task, api_store, client, body = self.prepared_card_confirmation()
        bob, carol = (self.store.principal_for_fixture(name) for name in ("bob", "carol"))
        for header, value, status in (("token", "forged-verification", 401), ("app_id", "other-app", 403)):
            response = client.post("/integrations/feishu/events", json={**body, "header": {**body["header"], header: value}})
            self.assertEqual(response.status_code, status)
        for path, value in (
            (["operator", "open_id"], bob.subject),
            (["operator"], {"tenant_key": carol.tenant_key, "open_id": carol.subject}),
            (["context", "open_message_id"], "om_forwarded"),
            (["action", "value", "notification_id"], str(uuid.uuid4())),
        ):
            forged = deepcopy(body)
            target = forged["event"]
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            response = client.post("/integrations/feishu/events", json=forged)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["toast"]["type"], "error")
        self.assertIsNone(api_store.get_audit_task(self.alice, task["id"])["round_id"])
        source_id = task["files"][0]["file"]["file_id"]
        with connect(self.settings, "migrator") as conn:
            conn.execute("DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s", (source_id, self.alice.user_id))
        self.assertEqual(client.post("/integrations/feishu/events", json=body).json()["toast"]["type"], "error")
        with connect(self.settings, "migrator") as conn:
            conn.execute("INSERT INTO product.resource_grants VALUES (%s, %s, 'read')", (source_id, self.alice.user_id))
            conn.execute("DELETE FROM product.memberships WHERE user_id = %s AND tenant_id = %s", (self.alice.user_id, self.alice.tenant_id))
        self.assertEqual(client.post("/integrations/feishu/events", json=body).json()["toast"]["type"], "error")
        with connect(self.settings, "migrator") as conn:
            conn.execute("INSERT INTO product.memberships (user_id, tenant_id, role) VALUES (%s, %s, 'member')", (self.alice.user_id, self.alice.tenant_id))
            conn.execute("DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s", (task["id"], self.alice.user_id))
        denied = client.post("/integrations/feishu/events", json=body)
        self.assertEqual(denied.json()["toast"]["type"], "error")
        self.assertEqual(api_store.list_rounds(self.alice), [])
        self.assertEqual(api_store.list_usage_events(self.alice), [])

    def test_feishu_card_never_confirms_a_newer_rule_or_input_revision(self):
        files, task, api_store, client, body = self.prepared_card_confirmation()
        rule_id = task["files"][0]["match"]["rule"]["id"]
        self.store.publish_rule(self.alice, "新规则版本", self.rule_body(), files[0].id, predecessor_id=rule_id)
        expired = client.post("/integrations/feishu/events", json=body)
        self.assertEqual(expired.json()["toast"]["type"], "info")
        self.assertEqual(self.process_card_confirmation(api_store)["state"], "rejected")
        self.assertEqual(api_store.get_audit_task(self.alice, task["id"])["input_revision"], 0)
        revised = api_store.create_audit_task(
            self.alice,
            {
                "request_id": str(uuid.uuid4()),
                "task_id": task["id"],
                "expected_revision": 0,
                "customer_preference": None,
                "files": [{"file_version_id": row["file"]["id"], "validation_id": row["validation_id"], "selection": row["selection"], "removal_reason": None} for row in task["files"]],
            },
        )
        self.assertEqual(revised["input_revision"], 1)
        stale = client.post("/integrations/feishu/events", json=body)
        self.assertEqual(stale.json()["toast"]["type"], "warning")
        self.assertIsNone(api_store.claim_task_confirmation("rejected-is-not-restarted"))
        self.assertIsNone(api_store.get_audit_task(self.alice, task["id"])["round_id"])
        self.assertEqual(api_store.list_rounds(self.alice), [])

    def test_feishu_card_and_web_confirmation_compete_for_one_round(self):
        from api import create_app
        from fastapi.testclient import TestClient
        from product.identity import SESSION_COOKIE

        _, task, api_store, client, body = self.prepared_card_confirmation()
        web = TestClient(create_app(settings=self.settings, auth_store=api_store))
        self.addCleanup(web.close)
        web.cookies.set(SESSION_COOKIE, api_store.create_session(self.alice))
        preview = web.get(f"/audit-tasks/{task['id']}")
        self.assertEqual(preview.status_code, 200)
        basis = preview.json()["confirmation_basis_digest"]
        barrier = threading.Barrier(2)

        def card_confirm():
            barrier.wait(timeout=10)
            return client.post("/integrations/feishu/events", json=body)

        def web_confirm():
            barrier.wait(timeout=10)
            return web.post(
                f"/audit-tasks/{task['id']}/confirm",
                json={"request_id": str(uuid.uuid4()), "expected_revision": 0, "expected_confirmation_basis_digest": basis},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            card, browser = pool.submit(card_confirm), pool.submit(web_confirm)
            card, browser = card.result(timeout=20), browser.result(timeout=20)
        self.assertEqual(card.status_code, 200)
        self.assertIn(card.json()["toast"]["type"], ("info", "success"))
        self.assertIn(browser.status_code, (201, 409))
        claimed = api_store.claim_task_confirmation("competing-card")
        if claimed:
            self.assertEqual(api_store.process_task_confirmation(claimed, "competing-card")["state"], "accepted")
        current = api_store.get_audit_task(self.alice, task["id"])
        self.assertEqual(current["state"], "reviewing")
        self.assertEqual([row["id"] for row in api_store.list_rounds(self.alice)], [current["round_id"]])
        self.assertEqual(api_store.list_usage_events(self.alice), [])

    def test_feishu_card_with_unknown_delivery_does_not_create_an_acceptance_or_replay_the_send(self):
        _, task, api_store, client, body = self.prepared_card_confirmation(sent=False)
        response = client.post("/integrations/feishu/events", json=body)
        self.assertEqual(response.json()["toast"]["type"], "error")
        self.assertIsNone(api_store.get_audit_task(self.alice, task["id"])["round_id"])
        self.assertIsNone(api_store.claim_task_notification("must-not-resend"))

    def test_card_confirmation_queue_survives_store_recreation_and_fences_an_expired_claim(self):
        from product import build_product_store
        from product.db import connect

        _, task, store, _, body = self.prepared_card_confirmation()
        action = self.card_action(body)
        pending = store.enqueue_task_confirmation(action)
        self.assertEqual(pending["state"], "pending")
        self.assertIsNone(build_product_store(self.settings, objects=store.objects, role="worker").claim_job("audit-does-not-consume-confirmations"))
        recreated = build_product_store(self.settings, objects=store.objects, role="api")
        self.assertEqual(recreated.enqueue_task_confirmation(action), pending)
        self.assertEqual(recreated.get_audit_task(self.alice, task["id"])["acceptance"], pending)
        self.assertEqual(recreated.get_audit_task(self.alice, task["id"], request_id=pending["request_id"])["acceptance"], pending)
        stale = recreated.claim_task_confirmation("lost-process")
        with connect(self.settings, "migrator") as conn:
            conn.execute("UPDATE engine.jobs SET lease_until = now() - interval '1 second' WHERE id = %s", (stale.id,))
        current = recreated.claim_task_confirmation("recovered-process")
        self.assertEqual(current.id, stale.id)
        self.assertEqual(current.attempt, stale.attempt + 1)
        self.assertIsNone(recreated.process_task_confirmation(stale, "lost-process"))
        self.assertEqual(recreated.process_task_confirmation(current, "recovered-process")["state"], "accepted")
        self.assertEqual(recreated.enqueue_task_confirmation(action)["state"], "accepted")
        self.assertIsNone(recreated.process_task_confirmation(stale, "lost-process"))
        self.assertIsNone(recreated.claim_task_confirmation("no-duplicate"))
        loaded = recreated.get_audit_task(self.alice, task["id"], request_id=str(uuid.uuid4()))
        self.assertEqual(loaded["acceptance"]["state"], "pending")
        self.assertEqual(recreated.get_audit_task(self.alice, task["id"], request_id=pending["request_id"])["acceptance"]["state"], "accepted")
        self.assertEqual(len(recreated.list_rounds(self.alice)), 1)
        self.assertEqual(recreated.list_usage_events(self.alice), [])

    def test_concurrent_card_clicks_share_one_durable_confirmation_or_report_database_unknown(self):
        import psycopg
        from product.db import connect

        _, task, store, _, body = self.prepared_card_confirmation()
        action = self.card_action(body)
        barrier = threading.Barrier(2)

        def enqueue():
            barrier.wait(timeout=5)
            try:
                return store.enqueue_task_confirmation(action)
            except psycopg.Error:
                # A bounded database wait can be unknown, never an auth refusal.
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(enqueue), pool.submit(enqueue)
            receipts = [first.result(timeout=5), second.result(timeout=5)]
        known = [receipt for receipt in receipts if receipt is not None]
        self.assertTrue(known)
        pending = store.enqueue_task_confirmation(action)
        self.assertEqual(pending["state"], "pending")
        self.assertEqual(pending["reason"], "已收到卡片确认，正在核对用量，尚未受理。")
        for receipt in known:
            self.assertEqual(receipt, pending)
        with connect(self.settings, "migrator") as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM engine.jobs WHERE kind = 'task_confirmation' AND payload->>'task_id' = %s", (task["id"],)).fetchone()[0], 1)
        self.assertIsNone(store.get_audit_task(self.alice, task["id"])["round_id"])

    def test_slow_quota_does_not_hold_card_callback_and_repeated_clicks_do_not_duplicate_usage(self):
        from unittest.mock import patch

        from api.notifications import dispatch_task_confirmation
        from product.feishu_usage import PartnerAiItem

        _, task, store, client, body = self.prepared_card_confirmation()
        self.settings.usage_charge_enabled = True
        self.settings.feishu_ai_item_id = "card-quota-test"
        self.settings.feishu_app_secret = "test-secret"
        entered, release = threading.Event(), threading.Event()

        def slow_quota(_actor):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("quota test did not release")
            return PartnerAiItem(True, 100, True)

        first = client.post("/integrations/feishu/events", json=body)
        self.assertEqual(first.json()["toast"]["type"], "info")
        with (
            patch("api.notifications.tenant_access_token", return_value="test-token"),
            patch("api.notifications.query_tenant_ai_item", side_effect=lambda *_args, **_kwargs: slow_quota(None)),
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                processing = pool.submit(dispatch_task_confirmation, store, Mock(), self.settings, "slow-card")
                try:
                    self.assertTrue(entered.wait(5))
                    callback = pool.submit(client.post, "/integrations/feishu/events", json=body)
                    self.assertEqual(callback.result(timeout=3).json()["toast"]["type"], "info")
                    self.assertFalse(processing.done())
                    self.assertIsNone(store.get_audit_task(self.alice, task["id"])["round_id"])
                finally:
                    release.set()
                self.assertTrue(processing.result(timeout=10))
        self.assertEqual(store.enqueue_task_confirmation(self.card_action(body))["state"], "accepted")
        self.assertEqual(len(store.list_rounds(self.alice)), 1)
        events = store.list_usage_events(self.alice)
        self.assertEqual(sum(event["consumed_points"] for event in events), len(task["files"]) * 500)
        self.assertIsNone(store.claim_task_confirmation("no-second-charge"))

    def test_explicit_quota_rejection_is_durable_and_does_not_requeue_on_another_click(self):
        from product.feishu_usage import PartnerAiItem

        _, task, store, _, body = self.prepared_card_confirmation()
        self.settings.usage_charge_enabled = True
        self.settings.feishu_ai_item_id = "card-quota-test"
        self.settings.feishu_free_trial_quota_points = 0
        action = self.card_action(body)
        pending = store.enqueue_task_confirmation(action)
        receipt = self.process_card_confirmation(store, usage_policy=lambda _actor: PartnerAiItem(True, 0, True))
        self.assertEqual(receipt["state"], "rejected")
        self.assertTrue(receipt["reason"])
        self.assertEqual(store.enqueue_task_confirmation(action), receipt)
        self.assertEqual(store.get_audit_task(self.alice, task["id"], request_id=pending["request_id"])["acceptance"], receipt)
        self.assertIsNone(store.get_audit_task(self.alice, task["id"])["round_id"])
        self.assertIsNone(store.claim_task_confirmation("no-automatic-reconfirmation"))
        self.assertEqual(store.list_usage_events(self.alice), [])

    def test_card_after_web_acceptance_keeps_its_own_pending_receipt_until_validated_processing(self):
        _, task, store, _, body = self.prepared_card_confirmation()
        accepted = store.confirm_audit_task(self.alice, task["id"], str(uuid.uuid4()), task["input_revision"])
        self.settings.usage_charge_enabled = True
        self.settings.feishu_ai_item_id = "card-quota-test"
        action = self.card_action(body)
        pending = store.enqueue_task_confirmation(action)
        self.assertEqual(pending["state"], "pending")
        self.assertEqual(store.enqueue_task_confirmation(action), pending)
        self.assertEqual(store.get_audit_task(self.alice, task["id"])["acceptance"]["state"], "accepted")
        self.assertEqual(store.get_audit_task(self.alice, task["id"], request_id=pending["request_id"])["acceptance"], pending)
        receipt = self.process_card_confirmation(store, usage_policy=lambda _actor: self.fail("An existing Round must not query quota again"))
        self.assertEqual(receipt["state"], "accepted")
        self.assertEqual(store.enqueue_task_confirmation(action), receipt)
        current = store.get_audit_task(self.alice, task["id"], request_id=pending["request_id"])
        self.assertEqual(current["acceptance"], receipt)
        self.assertEqual(current["round_id"], accepted["round_id"])
        self.assertEqual(len(store.list_rounds(self.alice)), 1)
        self.assertEqual(store.list_usage_events(self.alice), [])

    def test_card_confirmation_rechecks_resource_permission_after_queue_commit(self):
        from product.db import connect

        files, task, store, _, body = self.prepared_card_confirmation()
        store.enqueue_task_confirmation(self.card_action(body))
        with connect(self.settings, "migrator") as conn:
            conn.execute("DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s", (files[0].file_id, self.alice.user_id))
        receipt = self.process_card_confirmation(store)
        self.assertEqual(receipt["state"], "rejected")
        self.assertTrue(receipt["reason"])
        with connect(self.settings, "migrator") as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM product.audit_rounds WHERE source_draft_id = %s", (task["id"],)).fetchone()[0], 0)
        self.assertEqual(store.list_usage_events(self.alice), [])

    def test_unexpected_confirmation_failure_preserves_the_original_pending_request_for_recovery(self):
        _, task, store, _, body = self.prepared_card_confirmation()
        self.settings.usage_charge_enabled = True
        self.settings.feishu_ai_item_id = "card-quota-test"
        pending = store.enqueue_task_confirmation(self.card_action(body))
        claimed = store.claim_task_confirmation("interrupted-card")

        def unavailable(_actor):
            raise RuntimeError("unconfirmed external read")

        with self.assertRaises(RuntimeError):
            store.process_task_confirmation(claimed, "interrupted-card", usage_policy=unavailable)
        self.assertEqual(store.get_audit_task(self.alice, task["id"])["acceptance"], pending)
        self.assertIsNone(store.get_audit_task(self.alice, task["id"])["round_id"])
        self.assertEqual(store.list_usage_events(self.alice), [])

    def test_lost_confirmation_commit_reply_reads_back_the_same_accepted_request(self):
        from contextlib import contextmanager
        from unittest.mock import patch

        import psycopg
        from product import build_product_store

        _, task, store, _, body = self.prepared_card_confirmation()
        pending = store.enqueue_task_confirmation(self.card_action(body))
        claimed = store.claim_task_confirmation("commit-reply-lost")
        connection = store._conn

        @contextmanager
        def lose_reply_after_commit():
            # Real PostgreSQL commits the admission and receipt together. Only
            # delivery of that known commit reply to this caller is interrupted.
            with connection() as conn:
                yield conn
            raise psycopg.OperationalError("test-only lost commit reply")

        with patch.object(store, "_conn", side_effect=lose_reply_after_commit):
            with self.assertRaises(psycopg.OperationalError):
                store.process_task_confirmation(claimed, "commit-reply-lost")
        restored = build_product_store(self.settings, objects=store.objects, role="api")
        loaded = restored.get_audit_task(self.alice, task["id"], request_id=pending["request_id"])
        self.assertEqual(loaded["acceptance"], {**pending, "state": "accepted", "reason": None})
        self.assertIsNone(restored.process_task_confirmation(claimed, "commit-reply-lost"))
        self.assertEqual(restored.enqueue_task_confirmation(self.card_action(body)), loaded["acceptance"])
        self.assertEqual(len(restored.list_rounds(self.alice)), 1)
        self.assertEqual(restored.list_usage_events(self.alice), [])

    def test_distinct_zip_collections_keep_separate_intents_and_unknown_does_not_block_the_other(self):
        from urllib.parse import parse_qs, urlsplit

        from product import build_product_store
        from product.fixtures import APP_ID
        from worker import process_claimed

        self.settings.feishu_app_id = APP_ID
        _, task, worker, _, _ = self.prepared_query_task(documents=(("first.xlsx", "A_ONLY_017"), ("second.xlsx", "B_ONLY_029"), ("third.xlsx", "C_ONLY_031")))
        for _ in range(3):
            process_claimed(worker, worker.claim_job("zip-pdfs"), "zip-pdfs")
        api_store = build_product_store(self.settings, objects=self.store.objects, role="api")
        for _ in range(3):
            job, recipient = api_store.claim_task_notification("notifier")
            if api_store.start_task_notification(job, "notifier", recipient):
                api_store.finish_task_notification(job, "notifier", {"state": "sent", "message_id": "om_prior", "reason": None})
        current = self.display_client.get(f"/audit-tasks/{task['id']}").json()
        references = [
            {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": row["file"]["id"], "result_attempt": 1, "report_id": row["audit"]["report"]["report_id"]}
            for row in current["files"]
        ]
        commands = [{"request_id": str(uuid.uuid4()), "reports": references[:2]}, {"request_id": str(uuid.uuid4()), "reports": references[1:]}]
        packages = [self.display_client.post("/audit-report-packages", json=body).json() for body in commands]
        self.assertNotEqual(packages[0]["package_id"], packages[1]["package_id"])
        for _ in range(2):
            process_claimed(worker, worker.claim_job("zip-packs"), "zip-packs")
        claims = []
        for index in range(2):
            intent, recipient = api_store.claim_task_notification("zip-notifier")
            claims.append(intent)
            delivery = api_store.start_task_notification(intent, "zip-notifier", recipient)
            package_index = next(i for i, package in enumerate(packages) if package["package_id"] == intent.payload["package_id"])
            link = delivery["card"]["body"]["elements"][1]["behaviors"][0]["default_url"]
            target = parse_qs(urlsplit(link).query)["return_to"][0]
            self.assertEqual(parse_qs(urlsplit(target).query)["packageRequest"], [commands[package_index]["request_id"]])
            self.assertTrue(
                api_store.finish_task_notification(intent, "zip-notifier", {"state": "unknown" if index == 0 else "sent", "message_id": None if index == 0 else "om_second", "reason": None})
            )
        self.assertIsNone(api_store.claim_task_notification("notifier-again"))
        self.assertTrue(api_store.finish_task_notification(claims[0], "zip-notifier", {"state": "sent", "message_id": "om_late", "reason": None}))
        self.assertIsNone(api_store.claim_task_notification("notifier-last"))

    def test_ready_zip_notifies_once_even_when_completion_notification_is_off(self):
        from urllib.parse import parse_qs, urlsplit

        from product import build_product_store
        from product.fixtures import APP_ID
        from worker import process_claimed

        self.settings.feishu_app_id = APP_ID
        task, worker, completed, command, package = self.prepared_report_package()
        api_store = build_product_store(self.settings, objects=self.store.objects, role="api")
        api_store.save_task_notification(self.alice, task["id"], {"enabled": False, "expected_revision": 0})
        for stage in ("confirmation", "started", "completed"):
            old, recipient = api_store.claim_task_notification("notifier")
            self.assertEqual(old.payload["stage"], stage)
            delivery = api_store.start_task_notification(old, "notifier", recipient)
            if delivery:
                api_store.finish_task_notification(old, "notifier", {"state": "sent", "message_id": "om_" + stage, "reason": None})
        zip_job = worker.claim_job("zip-notify")
        self.assertEqual(zip_job.kind, "report_zip")
        self.assertTrue(worker.complete_job(zip_job, "zip-notify", {"error": "report_package_artifact_failed"}, "failed"))
        self.assertIsNone(api_store.claim_task_notification("notifier-before-recovery"))
        recovered = self.display_client.post(
            f"/audit-report-packages/{command['request_id']}/retries", json={"request_id": str(uuid.uuid4()), "package_id": package["package_id"], "expected_attempt": zip_job.attempt}
        )
        self.assertEqual(recovered.status_code, 202)
        zip_job = worker.claim_job("zip-notify")
        self.assertEqual(zip_job.attempt, 2)
        process_claimed(worker, zip_job, "zip-notify")
        ready = self.display_client.get(f"/audit-report-packages/{command['request_id']}").json()
        self.assertEqual(ready["state"], "ready")
        claimed = api_store.claim_task_notification("zip-notifier")
        self.assertIsNotNone(claimed, "a ready ZIP needs its own durable notification intent")
        intent, recipient = claimed
        self.assertEqual((intent.payload["stage"], intent.payload["package_id"]), ("zip_ready", package["package_id"]))
        delivery = api_store.start_task_notification(intent, "zip-notifier", recipient)
        self.assertEqual(delivery["card"]["header"]["title"]["content"], "报告 ZIP 已就绪")
        link = delivery["card"]["body"]["elements"][1]["behaviors"][0]["default_url"]
        target = parse_qs(urlsplit(link).query)["return_to"][0]
        self.assertEqual(parse_qs(urlsplit(target).query)["packageRequest"], [command["request_id"]])
        self.assertTrue(api_store.finish_task_notification(intent, "zip-notifier", {"state": "sent", "message_id": "om_zip", "reason": None}))
        self.assertFalse(worker.complete_job(zip_job, "zip-notify", {}, "succeeded"))
        self.assertEqual(self.display_client.post("/audit-report-packages", json=command).json()["package_id"], package["package_id"])
        self.assertIsNone(api_store.claim_task_notification("notifier-again"))
        self.assertEqual(self.display_client.get(f"/audit-tasks/{task['id']}").json()["files"], completed["files"])

    def test_missing_feishu_credentials_do_not_consume_the_notification_intent(self):
        import httpx
        from api.notifications import dispatch_task_notification
        from product import build_product_store
        from product.fixtures import APP_ID

        self.settings.feishu_app_id = APP_ID
        self.settings.feishu_app_secret = ""
        self.settings.feishu_notifications_enabled = True
        self.prepared_display_task(confirm=False)
        api_store = build_product_store(self.settings, objects=self.store.objects, role="api")
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as http:
            self.assertFalse(dispatch_task_notification(api_store, http, self.settings, "notifier"))
        job, _ = api_store.claim_task_notification("configured-notifier")
        self.assertEqual((job.payload["stage"], job.attempt), ("confirmation", 1))

    def test_simultaneous_pdf_endings_queue_one_truthful_completion_and_close_the_switch_at_dispatch(self):
        from product import build_product_store
        from product.fixtures import APP_ID
        from worker import process_claimed

        self.settings.feishu_app_id = APP_ID
        _, task, worker, _, _ = self.prepared_query_task(documents=(("first.xlsx", "A_ONLY_017"), ("second.xlsx", "B_ONLY_029")))
        api_store = build_product_store(self.settings, objects=self.store.objects, role="api")
        for stage in ("confirmation", "started"):
            job, recipient = api_store.claim_task_notification("notifier")
            self.assertEqual(job.payload["stage"], stage)
            delivery = api_store.start_task_notification(job, "notifier", recipient)
            if delivery:
                self.assertTrue(api_store.finish_task_notification(job, "notifier", {"state": "sent", "message_id": "om_" + stage, "reason": None}))
        self.assertEqual(api_store.get_task_notification(self.alice, task["id"])["delivery"]["state"], "not_due")
        first, second = worker.claim_job("pdf-first"), worker.claim_job("pdf-second")
        self.assertEqual((first.kind, second.kind), ("report_pdf", "report_pdf"))
        barrier = threading.Barrier(2)

        def finish_first():
            barrier.wait(timeout=10)
            process_claimed(worker, first, "pdf-first")

        def fail_second():
            barrier.wait(timeout=10)
            return worker.complete_job(second, "pdf-second", {"error": "report_pdf_failed"}, "failed")

        with ThreadPoolExecutor(max_workers=2) as pool:
            completed, failed = pool.submit(finish_first), pool.submit(fail_second)
            completed.result(timeout=30)
            self.assertTrue(failed.result(timeout=30))
        self.assertEqual(api_store.get_audit_task(self.alice, task["id"])["state"], "completed")
        intent, recipient = api_store.claim_task_notification("notifier")
        self.assertEqual(intent.payload["stage"], "completed")
        delivery = api_store.start_task_notification(intent, "notifier", recipient)
        self.assertIn("**报告已就绪 1 份**<br>报告生成失败 1 份 · 审核异常 0 份", delivery["card"]["body"]["elements"][0]["content"])
        with self.assertRaisesRegex(ValueError, "notification_already_dispatched"):
            api_store.save_task_notification(self.alice, task["id"], {"enabled": False, "expected_revision": 0})
        self.assertIsNone(api_store.claim_task_notification("another-notifier"))
        with self.assertRaisesRegex(ValueError, "invalid_notification_receipt"):
            api_store.finish_task_notification(intent, "notifier", {"state": "sent", "message_id": 7})
        self.assertTrue(api_store.finish_task_notification(intent, "notifier", {"state": "sent", "message_id": "om_complete", "reason": None}))
        self.assertEqual(api_store.get_task_notification(self.alice, task["id"])["delivery"]["state"], "sent")
        self.assertIsNone(api_store.claim_task_notification("third-notifier"))

    def test_notification_rechecks_source_access_before_dispatch(self):
        from product import build_product_store
        from product.db import connect
        from product.fixtures import APP_ID

        self.settings.feishu_app_id = APP_ID
        files, task, _ = self.prepared_display_task(confirm=False)
        api_store = build_product_store(self.settings, objects=self.store.objects, role="api")
        claimed, recipient = api_store.claim_task_notification("notifier")
        with connect(self.settings, "migrator") as conn:
            # The first file is also the rule source, which the tenant shares; revoke a plain upload.
            conn.execute("DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s", (files[1].file_id, self.alice.user_id))
        self.assertIsNone(api_store.start_task_notification(claimed, "notifier", recipient))
        self.assertIsNone(api_store.claim_task_notification("notifier-again"))
        self.assertEqual(api_store.get_task_notification(self.alice, task["id"])["delivery"]["state"], "not_due")

    def test_api_notifier_uses_the_creator_and_never_replays_a_lost_provider_reply(self):
        import json

        import httpx
        from api.notifications import dispatch_task_notification
        from product import build_product_store
        from product.fixtures import APP_ID

        self.settings.feishu_app_id = APP_ID
        self.settings.feishu_app_secret = "notification-test-secret"
        self.settings.feishu_notifications_enabled = True
        _, task, _ = self.prepared_display_task(confirm=False)
        api_store = build_product_store(self.settings, objects=self.store.objects, role="api")
        api_store.save_app_ticket(APP_ID, "notification-test-ticket")
        sent = []

        def provider(request):
            if request.url.path.endswith("app_access_token"):
                return httpx.Response(200, json={"code": 0, "app_access_token": "test-app-token"})
            if request.url.path.endswith("tenant_access_token"):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "test-tenant-token"})
            self.assertEqual(request.url.path, "/open-apis/im/v1/messages")
            sent.append(json.loads(request.content))
            raise httpx.ReadTimeout("provider reply lost", request=request)

        with httpx.Client(transport=httpx.MockTransport(provider)) as http:
            self.assertTrue(dispatch_task_notification(api_store, http, self.settings, "api-notifier"))
            self.assertFalse(dispatch_task_notification(api_store, http, self.settings, "api-notifier"))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["receive_id"], self.alice.subject)
        self.assertEqual(sent[0]["msg_type"], "interactive")
        card = json.loads(sent[0]["content"])
        self.assertEqual(card["header"]["title"]["content"], "规则匹配结果待确认")
        self.assertEqual(card["schema"], "2.0")
        self.assertIn(task["id"], card["body"]["elements"][1]["behaviors"][0]["default_url"])
        self.assertIsNone(api_store.get_audit_task(self.alice, task["id"])["round_id"])

    def test_scoped_notifier_sends_only_the_requested_task_and_leaves_history_claimable(self):
        import json

        import httpx
        from api.notifications import dispatch_task_notification
        from product import build_product_store
        from product.fixtures import APP_ID

        self.settings.feishu_app_id = APP_ID
        self.settings.feishu_app_secret = "notification-test-secret"
        self.settings.feishu_notifications_enabled = True
        _, historical, _ = self.prepared_display_task(confirm=False)
        _, current, _ = self.prepared_display_task(confirm=False)
        api_store = build_product_store(self.settings, objects=self.store.objects, role="api")
        api_store.save_app_ticket(APP_ID, "notification-test-ticket")
        sent = []

        def provider(request):
            if request.url.path.endswith("app_access_token"):
                return httpx.Response(200, json={"code": 0, "app_access_token": "test-app-token"})
            if request.url.path.endswith("tenant_access_token"):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "test-tenant-token"})
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_scoped"}})

        with httpx.Client(transport=httpx.MockTransport(provider)) as http:
            self.assertFalse(dispatch_task_notification(api_store, http, self.settings, "scoped-notifier", task_id=str(uuid.uuid4())))
            self.assertTrue(dispatch_task_notification(api_store, http, self.settings, "scoped-notifier", task_id=current["id"]))
            self.assertFalse(dispatch_task_notification(api_store, http, self.settings, "scoped-notifier", task_id=current["id"]))
        self.assertEqual(len(sent), 1)
        self.assertIn(current["id"], sent[0]["content"])
        self.assertNotIn(historical["id"], sent[0]["content"])
        old, _ = api_store.claim_task_notification("historical-notifier", task_id=historical["id"])
        self.assertEqual(old.payload["task_id"], historical["id"])
        self.assertEqual(old.attempt, 1, "the scoped sender must not claim historical work")

    def test_notification_takeover_fences_old_sender_and_unknown_is_never_reclaimed(self):
        from product import build_product_store
        from product.db import connect
        from product.fixtures import APP_ID

        self.settings.feishu_app_id = APP_ID
        _, task, _ = self.prepared_display_task(confirm=False)
        api_store = build_product_store(self.settings, objects=self.store.objects, role="api")
        old, recipient = api_store.claim_task_notification("sender-old")
        with connect(self.settings, "migrator") as conn:
            conn.execute("UPDATE engine.jobs SET lease_until = now() - interval '1 second' WHERE id = %s", (old.id,))
        current, recipient = api_store.claim_task_notification("sender-current")
        self.assertGreater(current.attempt, old.attempt)
        self.assertIsNone(api_store.start_task_notification(old, "sender-old", recipient))
        self.assertIsNotNone(api_store.start_task_notification(current, "sender-current", recipient))
        self.assertFalse(api_store.finish_task_notification(old, "sender-old", {"state": "sent", "message_id": "om_old", "reason": None}))
        with connect(self.settings, "migrator") as conn:
            conn.execute("UPDATE engine.jobs SET lease_until = now() - interval '1 second' WHERE id = %s", (current.id,))
        self.assertIsNone(api_store.claim_task_notification("sender-third"))
        self.assertTrue(api_store.finish_task_notification(current, "sender-current", {"state": "sent", "message_id": "om_current", "reason": None}))
        self.assertIsNone(api_store.claim_task_notification("sender-fourth"))
        self.assertTrue(api_store.get_task_notification(self.alice, task["id"])["enabled"])

    def test_completion_switch_does_not_suppress_started_notification_or_allow_unknown_replay(self):
        from product import build_product_store
        from product.fixtures import APP_ID

        self.settings.feishu_app_id = APP_ID
        _, task, worker = self.prepared_display_task(complete_audit=False)
        api_store = build_product_store(self.settings, objects=self.store.objects, role="api")
        api_store.save_task_notification(self.alice, task["id"], {"enabled": False, "expected_revision": 0})
        old, recipient = api_store.claim_task_notification("notifier-a")
        self.assertEqual(old.payload["stage"], "confirmation")
        self.assertIsNone(api_store.start_task_notification(old, "notifier-a", recipient))
        started, recipient = api_store.claim_task_notification("notifier-a")
        self.assertEqual(started.payload["stage"], "started")
        delivery = api_store.start_task_notification(started, "notifier-a", recipient)
        self.assertEqual(delivery["card"]["header"]["title"]["content"], "审核已开始")
        self.assertTrue(api_store.finish_task_notification(started, "notifier-a", {"state": "unknown", "reason": "receipt_unknown", "message_id": None}))
        self.assertIsNone(api_store.claim_task_notification("notifier-b"))
        audit = worker.claim_job("audit-worker")
        self.assertEqual(audit.kind, "audit")
        self.assertTrue(
            worker.complete_job(
                audit,
                "audit-worker",
                {"findings": [], "coverage": {"rule_items": [{"file_version_id": file["file"]["id"], "id": "1", "coverage": "unsupported"} for file in task["files"]]}},
                "succeeded",
            )
        )
        completed, recipient = api_store.claim_task_notification("notifier-b")
        self.assertEqual(completed.payload["stage"], "completed")
        self.assertIsNone(api_store.start_task_notification(completed, "notifier-b", recipient))
        preference = api_store.get_task_notification(self.alice, task["id"])
        self.assertEqual(preference["delivery"]["state"], "suppressed")
        self.assertFalse(preference["can_change"])
        self.assertIsNone(api_store.claim_task_notification("notifier-c"))

    def test_completion_notification_setting_is_private_persistent_and_revisioned(self):
        from api.audit_routes import register_audit_routes
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from product import build_product_store

        _, task, _ = self.prepared_display_task(complete_audit=False)
        api_store = build_product_store(self.settings, objects=self.store.objects, role="api")
        app = FastAPI()
        register_audit_routes(app, api_store, lambda request: self.display_principal, Mock())
        client = TestClient(app)
        path = f"/audit-tasks/{task['id']}/notification"
        initial = client.get(path)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual((initial.json()["enabled"], initial.json()["revision"]), (True, 0))
        saved = client.put(path, json={"enabled": False, "expected_revision": 0})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual((saved.json()["enabled"], saved.json()["revision"]), (False, 1))
        refreshed = client.get(f"/audit-tasks/{task['id']}").json()
        self.assertEqual((refreshed["notification_enabled"], refreshed["notification_revision"]), (False, 1))
        self.assertEqual(client.put(path, json={"enabled": True, "expected_revision": 0}).status_code, 409)
        self.assertEqual(client.put(path, json={"enabled": "false", "expected_revision": 1}).status_code, 400)
        self.assertEqual(client.get(path).json()["revision"], 1)
        self.display_principal = self.store.principal_for_fixture("bob")
        self.assertEqual(client.get(path).status_code, 403)
        self.assertEqual(client.put(path, json={"enabled": True, "expected_revision": 1}).status_code, 403)
        self.display_principal = self.store.principal_for_fixture("carol")
        self.assertEqual(client.get(path).status_code, 403)

    def test_archive_keeps_provider_identity_and_exact_history_reference(self):
        from api.web_contract import AuditArchivePage, AuditHistoryDetail
        from product.feishu_drive import ImportedMaterial
        from pydantic import TypeAdapter

        def imported(token):
            version = self.store.receive_drive_upload(
                self.alice,
                str(uuid.uuid4()),
                token,
                "file",
                lambda: ImportedMaterial(filename="same.xlsx", content_type="application/octet-stream", data=b"x", file_token=token, doc_type="file"),
            )
            return self.store.finalize_version(version)

        first, other = imported("document-one"), imported("document-two")
        _, old_task, _ = self.prepared_display_task(files=[first, other])
        newer = imported("document-one")
        _, new_task, _ = self.prepared_display_task(files=[newer, self.upload("same.xlsx")])
        response = self.display_client.get("/audit-archive")
        self.assertEqual(response.status_code, 200)
        page = response.json()
        TypeAdapter(AuditArchivePage).validate_python(page)
        self.assertEqual(page["total"], 2)
        doc = next(row for row in page["documents"] if row["latest_activity"]["reference"]["task_id"] == new_task["id"])
        history_path = f"/audit-archive/{doc['document_id']}/history"
        history = self.display_client.get(history_path).json()
        self.assertEqual([entry["sequence"] for entry in history["entries"]], [2, 1])
        old = history["entries"][-1]["reference"]
        self.assertEqual(old["task_id"], old_task["id"])
        detail = self.display_client.get(history_path + "/result", params=old)
        self.assertEqual(detail.status_code, 200)
        TypeAdapter(AuditHistoryDetail).validate_python(detail.json())
        self.assertEqual(detail.json()["file"]["file"]["id"], first.id)
        self.assertEqual(self.display_client.get(history_path + "/result", params={**old, "file_version_id": newer.id}).status_code, 403)
        self.assertEqual(self.display_client.get(history_path + "/result", params={**old, "result_attempt": 99}).status_code, 403)
        self.assertEqual(self.display_client.get("/audit-archive", params={"search": "missing"}).json()["total"], 0)
        self.assertEqual(self.display_client.get("/audit-archive", params={"page": 2}).json()["documents"], [])
        self.assertEqual(self.display_client.get("/audit-archive", params={"customer": "测试客户"}).json()["total"], 2)
        self.assertEqual(self.display_client.get("/audit-archive", params={"from_date": "2099-01-01"}).json()["total"], 0)
        self.assertEqual(self.display_client.get("/audit-archive", params={"from_date": "2026-10-20", "through_date": "2026-10-01"}).status_code, 400)
        category = page["categories"][0]
        self.assertEqual(category["count"], 2)
        self.assertEqual(self.display_client.get("/audit-archive", params={"category_id": category["parts"][0]["id"]}).json()["total"], 2)
        other_doc = next(row for row in page["documents"] if row["document_id"] != doc["document_id"])
        corrected = self.display_client.post(
            f"/audit-tasks/{old_task['id']}/display-info",
            json={
                "request_id": str(uuid.uuid4()),
                "reference": {"task_id": old_task["id"], "round_id": old_task["round_id"], "file_version_id": other.id},
                "expected_revision": 0,
                "values": {"customer": "更正客户", "model": "M2", "part_number": "P2", "part": "更正零件"},
            },
        )
        self.assertEqual(corrected.status_code, 200)
        refreshed = self.display_client.get("/audit-archive").json()["documents"]
        self.assertEqual(refreshed[0]["document_id"], other_doc["document_id"])
        self.assertGreater(refreshed[0]["updated_at"], refreshed[1]["updated_at"])
        self.assertEqual(self.display_client.get("/audit-archive", params={"customer": "更正客户"}).json()["total"], 1)
        from product.db import connect

        with connect(self.settings, "migrator") as conn:
            conn.execute("DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s", (first.file_id, self.alice.user_id))
        self.assertEqual(self.display_client.get(history_path + "/result", params=old).status_code, 403)
        remaining = self.display_client.get(history_path).json()["entries"]
        self.assertEqual([entry["sequence"] for entry in remaining], [2])
        self.display_principal = self.store.principal_for_fixture("bob")
        self.assertEqual(self.display_client.get("/audit-archive").json()["total"], 0)
        self.assertEqual(self.display_client.get(history_path).status_code, 403)

    def test_history_reads_reuse_one_connection_and_keep_the_task_projection(self):
        from unittest.mock import patch

        files = [self.upload(f"frozen-{index}.xlsx") for index in range(6)]
        for index, file in enumerate(files):
            self.store.save_file_source(file.id, "file", f"history-document-{index}")
        _, task, _ = self.prepared_display_task(files=files)
        expected = self.store.get_audit_task(self.alice, task["id"])
        page = self.display_client.get("/audit-archive").json()
        document_id = next(row["document_id"] for row in page["documents"] if row["title"] == files[0].original_filename)
        with patch.object(self.store, "_conn", wraps=self.store._conn) as connections:
            history = self.store.read_audit_history(self.alice, document_id)
        self.assertEqual(connections.call_count, 1, "history must not reconnect for every file or validation")
        reference = history["entries"][0]["reference"]
        with patch.object(self.store, "_conn", wraps=self.store._conn) as connections:
            detail = self.store.read_audit_history(self.alice, document_id, reference)
        self.assertEqual(connections.call_count, 1, "detail and frozen originals must share the read transaction")
        self.assertEqual(detail["file"], next(row for row in expected["files"] if row["file"]["id"] == files[0].id))
        self.assertEqual(detail["entry"], history["entries"][0])
        self.assertEqual(set(detail["frozen_file_version_ids"]), {file.id for file in files})
        self.assertEqual({row["file"]["id"] for row in detail["originals"]}, {file.id for file in files})
        self.assertEqual(self.store.get_audit_task(self.alice, task["id"]), expected)

    def test_task_list_reads_summaries_without_building_result_details(self):
        from unittest.mock import patch

        from product.db import connect

        files, task, _ = self.prepared_display_task()
        expected = self.store.get_audit_task(self.alice, task["id"])
        with patch.object(self.store, "_conn", wraps=self.store._conn) as connections, patch.object(self.store, "_recognition_public", side_effect=AssertionError("list must not build task details")):
            rows = self.store.list_audit_tasks(self.alice)
        self.assertEqual(connections.call_count, 1)
        summary = next(row for row in rows if row["id"] == task["id"])
        for key in ("id", "number", "state", "round_id", "input_revision", "created_at", "completion_reason"):
            self.assertEqual(summary[key], expected[key])
        self.assertEqual(summary["files"], [{"file": row["file"], "selection": row["selection"]} for row in expected["files"]])
        self.assertNotIn("progress", summary)
        self.assertNotIn("audit", summary["files"][0])
        # The list title is the first selected file's recognized customer, model and part, read with the summary.
        fields = ("customer", "model", "part")
        named = [row["match"]["metadata"] for row in expected["files"] if row["selection"] == "selected" and row.get("match") and any(row["match"]["metadata"].get(key) for key in fields)]
        recognized = named[0] if named else None
        self.assertEqual(summary.get("subject"), recognized and {key: recognized.get(key) or None for key in fields})
        self.assertEqual(self.store.list_audit_tasks(self.store.principal_for_fixture("bob")), [])
        self.assertEqual(self.store.list_audit_tasks(self.store.principal_for_fixture("carol")), [])
        self.assertEqual(self.store.get_audit_task(self.alice, task["id"]), expected)
        with connect(self.settings, "migrator") as conn:
            # The first file is also the rule source, which the tenant shares; revoke a plain upload.
            conn.execute("DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s", (files[1].file_id, self.alice.user_id))
        with self.assertRaises(KeyError):
            self.store.list_audit_tasks(self.alice)

    def test_rule_directory_is_metadata_only_and_preserves_customer_scope_and_lineage(self):
        import json
        from unittest.mock import patch

        file = self.upload("客户规则.docx")
        first = self.store.publish_rule(self.alice, "客户要求", self.rule_body(), file.id)
        body = {**self.rule_body(), "customers": []}
        second = self.store.publish_rule(
            self.alice,
            "客户要求",
            body,
            file.id,
            predecessor_id=first["id"],
            case_context={
                "registration": json.dumps(
                    {
                        "basis": {"source": {"version": "v2"}},
                        "scope": {"customer": "", "part": "", "revision": "", "allCustomers": True, "allParts": True, "allRevisions": True},
                        "reason": "明确适用范围",
                    }
                )
            },
        )
        with patch.object(self.store, "_conn", wraps=self.store._conn) as connections, patch.object(self.store, "get_rule", side_effect=AssertionError("directory must not fetch each rule")):
            directory = self.store.list_rule_library(self.alice.tenant_id)
        self.assertEqual(connections.call_count, 1)
        versions = {row["id"]: row for row in directory}
        self.assertEqual(versions[second["id"]]["predecessor_id"], first["id"])
        self.assertEqual(versions[second["id"]]["version"], "v2")
        self.assertTrue(versions[second["id"]]["scope"]["allCustomers"])
        self.assertEqual(versions[first["id"]]["scope"]["customer"], "测试客户")
        self.assertEqual(versions[second["id"]]["clause_count"], 1)
        self.assertEqual(versions[second["id"]]["reason"], "明确适用范围")
        self.assertFalse(any("body" in row or "clauses" in row for row in directory))
        self.assertEqual(self.store.list_rule_library(self.store.principal_for_fixture("carol").tenant_id), [])

    def test_round_list_query_count_does_not_grow_per_row(self):
        from unittest.mock import patch

        self.prepared_display_task()
        self.prepared_display_task()
        original = self.store._conn
        count = [0]

        class CountedConnection:
            def __init__(self):
                self.connection = original()

            def __enter__(self):
                self.connection.__enter__()
                return self

            def __exit__(self, *args):
                return self.connection.__exit__(*args)

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, *args, **kwargs):
                count[0] += 1
                return self.connection.execute(*args, **kwargs)

        with patch.object(self.store, "_conn", side_effect=CountedConnection):
            first = self.store.list_rounds(self.alice, limit=1)
            one = count[0]
            count[0] = 0
            all_rows = self.store.list_rounds(self.alice, limit=2)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(all_rows), 2)
        self.assertEqual(first[0], all_rows[0])
        self.assertEqual(count[0], one, "list queries must be batched, not repeated for each round")

    def test_file_read_shares_authorization_and_version_connection_and_honors_revocation(self):
        from unittest.mock import patch

        from product.db import connect

        files, task, _ = self.prepared_display_task()
        # The first file is also the rule source, which the tenant shares; revoke on a plain upload.
        file = files[1]
        with patch.object(self.store, "_conn", wraps=self.store._conn) as connections:
            actual = self.store.read_file_version(self.alice, file.file_id, file.id)
        self.assertEqual(actual, file)
        self.assertEqual(connections.call_count, 1)
        self.assertEqual(self.store.read_file_version(self.alice, file.file_id, file.id, round_id=task["round_id"]), file)
        for principal in (self.store.principal_for_fixture("bob"), self.store.principal_for_fixture("carol")):
            with self.assertRaises(KeyError):
                self.store.read_file_version(principal, file.file_id, file.id, round_id=task["round_id"])
        with self.assertRaises(KeyError):
            self.store.read_file_version(self.alice, str(uuid.uuid4()), file.id)
        with connect(self.settings, "migrator") as conn:
            conn.execute("DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s", (file.file_id, self.alice.user_id))
        with self.assertRaises(KeyError):
            self.store.read_file_version(self.alice, file.file_id, file.id)
        # Frozen round access is independent of the current source-file grant.
        self.assertEqual(self.store.read_file_version(self.alice, file.file_id, file.id, round_id=task["round_id"]), file)

    def test_usage_links_the_paid_file_to_its_own_history_and_keeps_fee_when_access_is_revoked(self):
        from product.db import connect

        self.settings.feishu_ai_item_id = "test-item"
        first = self.upload("同名.xlsx")
        self.store.save_file_source(first.id, "file", "usage-document")
        _files, old_task, _worker = self.prepared_display_task([first])
        newer = self.upload("同名.xlsx")
        self.store.save_file_source(newer.id, "file", "usage-document")
        _files, new_task, _worker = self.prepared_display_task([newer])
        # Historical ledger fixtures, not a charged acceptance or external debit.
        with connect(self.settings, "migrator") as conn:
            for task, version in ((old_task, first), (new_task, newer)):
                conn.execute(
                    """INSERT INTO product.usage_events
                    (id, tenant_id, user_id, ai_item_id, consumed_points, is_free_trial, external_status, round_id, file_version_id)
                    VALUES (%s, %s, %s, 'test-item', 500, true, 'none', %s, %s)""",
                    (str(uuid.uuid4()), self.alice.tenant_id, self.alice.user_id, task["round_id"], version.id),
                )
        before = self.store.list_usage_events(self.alice)
        page = self.display_client.get("/audit-usage?range=all").json()
        old = next(row for row in page["entries"] if row["reference"]["task_id"] == old_task["id"])
        self.assertEqual(old["audit_sequence"], 1)
        self.assertEqual(old["audit_access"]["state"], "allowed")
        path = f"/audit-archive/{old['document_id']}/history/result"
        detail = self.display_client.get(path, params=old["reference"])
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["file"]["file"]["id"], first.id)
        self.assertEqual(detail.json()["entry"]["reference"]["round_id"], old_task["round_id"])
        with connect(self.settings, "migrator") as conn:
            conn.execute("DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s", (first.file_id, self.alice.user_id))
        denied_page = self.display_client.get("/audit-usage?range=all").json()
        self.assertEqual(denied_page["total"], 2)
        old_denied = next(row for row in denied_page["entries"] if row["usage_id"] == old["usage_id"])
        self.assertEqual(old_denied["points"], 500)
        self.assertIsNone(old_denied["document_id"])
        self.assertEqual(old_denied["audit_access"]["state"], "denied")
        new = next(row for row in denied_page["entries"] if row["reference"]["task_id"] == new_task["id"])
        self.assertEqual(new["audit_sequence"], 2)
        self.assertEqual(self.display_client.get(path, params=old["reference"]).status_code, 403)
        self.assertEqual(self.store.list_usage_events(self.alice), before)

    def prepared_display_task(self, files=None, *, complete_audit=True, per_file_rules=False, confirm=True):
        from api.audit_routes import register_audit_routes
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from product import build_product_store

        self.settings.usage_charge_enabled = False
        worker = build_product_store(self.settings, objects=self.store.objects, role="worker")
        files = files or [self.upload(name) for name in ("first.xlsx", "second.xlsx")]
        if per_file_rules:
            rules = {file.id: self.store.publish_rule(self.alice, file.original_filename + "要求", self.rule_body(), file.id) for file in files}
        else:
            rule = self.store.publish_rule(self.alice, "显示更正规则", self.rule_body(), files[0].id)
            rules = {file.id: rule for file in files}
        inputs = []
        for file in files:
            key = str(uuid.uuid4())
            self.store.accept_file_validation(self.alice, key, file.file_id, file.id)
            job = worker.claim_job("display-test")
            self.assertEqual(job.kind, "file_validation")
            self.assertTrue(worker.complete_job(job, "display-test", {"state": "valid", "reasons": []}, "succeeded"))
            inputs.append({"file_version_id": file.id, "validation_id": key, "selection": "selected", "removal_reason": None})
        task = self.store.create_audit_task(self.alice, {"request_id": str(uuid.uuid4()), "customer_preference": None, "files": inputs})
        for _ in files:
            job = worker.claim_job("display-test")
            self.assertEqual(job.kind, "file_analysis")
            worker.complete_job(
                job,
                "display-test",
                {
                    "source_file_version_id": job.payload["source_file_version_id"],
                    "matching_status": "matched",
                    "matched_rule_id": rules[job.payload["source_file_version_id"]]["id"],
                    "document_type": "控制计划",
                    "metadata": {"customer": "测试客户", "model": "M1", "part_number": "P-1", "part": "原零件"},
                    "notices": [],
                },
                "succeeded",
            )
        if not confirm:
            return files, self.store.get_audit_task(self.alice, task["id"]), worker
        task = self.store.confirm_audit_task(self.alice, task["id"], str(uuid.uuid4()), 0)
        if complete_audit:
            job = worker.claim_job("display-test")
            worker.complete_job(job, "display-test", {"findings": [], "coverage": {"rule_items": [{"file_version_id": file.id, "id": "1", "coverage": "unsupported"} for file in files]}}, "succeeded")
        self.display_principal = self.alice
        app = FastAPI()
        register_audit_routes(app, self.store, lambda request: self.display_principal, Mock())
        self.display_client = TestClient(app)
        self.addCleanup(self.display_client.close)
        return files, task, worker

    def test_confirmation_refuses_superseded_rule_without_substituting_new_version(self):
        files, task, worker = self.prepared_display_task(confirm=False)
        rule_id = task["files"][0]["match"]["rule"]["id"]
        self.store.publish_rule(self.alice, "修订规则", self.rule_body(), files[0].id, predecessor_id=rule_id)
        with self.assertRaisesRegex(ValueError, "rule_candidate_expired"):
            self.store.confirm_audit_task(self.alice, task["id"], str(uuid.uuid4()), 0)
        current = self.store.get_audit_task(self.alice, task["id"])
        self.assertIsNone(current["round_id"])
        self.assertEqual(current["files"][0]["match"]["rule"]["id"], rule_id)
        self.assertIsNone(worker.claim_job("no-audit-after-refusal"))

    def test_accepted_confirmation_replays_frozen_rule_after_new_version_is_published(self):
        files, task, _worker = self.prepared_display_task(confirm=False)
        rule_id = task["files"][0]["match"]["rule"]["id"]
        key = str(uuid.uuid4())
        accepted = self.store.confirm_audit_task(self.alice, task["id"], key, 0)
        self.store.publish_rule(self.alice, "后续修订", self.rule_body(), files[0].id, predecessor_id=rule_id)
        replayed = self.store.confirm_audit_task(self.alice, task["id"], key, 0)
        self.assertEqual(replayed["round_id"], accepted["round_id"])
        self.assertEqual(replayed["files"][0]["match"]["rule"]["id"], rule_id)

    def test_unrelated_rule_publication_does_not_invalidate_confirmation(self):
        files, task, _worker = self.prepared_display_task(confirm=False)
        rule_id = task["files"][0]["match"]["rule"]["id"]
        self.store.publish_rule(self.alice, "另一套独立要求", self.rule_body(), files[0].id)
        accepted = self.store.confirm_audit_task(self.alice, task["id"], str(uuid.uuid4()), 0)
        self.assertIsNotNone(accepted["round_id"])
        self.assertEqual(accepted["files"][0]["match"]["rule"]["id"], rule_id)

    def test_display_save_and_original_request_readback_do_not_change_audit_facts(self):
        files, task, worker = self.prepared_display_task()
        path = f"/audit-tasks/{task['id']}"
        before = self.store.get_round(task["round_id"])
        values = {"customer": "更正客户", "model": None, "project": "更正项目", "part_number": "P-2", "part": "更正零件"}
        command = {"request_id": str(uuid.uuid4()), "reference": {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": files[0].id}, "expected_revision": 0, "values": values}
        response = self.display_client.post(path + "/display-info", json=command)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"request": command, "state": "saved", "info": {"revision": 1, "values": values}, "reason": None})
        self.assertEqual(self.display_client.get(path + "/display-info/" + command["request_id"]).json(), response.json())
        loaded = self.display_client.get(path).json()
        self.assertEqual(loaded["files"][0]["display_info"], response.json()["info"])
        self.assertEqual(loaded["files"][0]["match"]["metadata"]["part"], "原零件")
        self.assertEqual(loaded["files"][1]["display_info"]["revision"], 0)
        self.assertEqual(self.store.get_round(task["round_id"]), before)
        self.assertEqual(self.store.list_usage_events(self.alice), [])
        self.assertIsNone(worker.claim_job("no-display-job"))

    def test_a_display_request_without_project_still_saves_and_reads_back_project_as_none(self):
        files, task, _worker = self.prepared_display_task()
        path = f"/audit-tasks/{task['id']}/display-info"
        legacy = {"customer": "更正客户", "model": None, "part_number": None, "part": None}
        command = {"request_id": str(uuid.uuid4()), "reference": {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": files[0].id}, "expected_revision": 0, "values": legacy}
        receipt = self.display_client.post(path, json=command).json()
        self.assertEqual(receipt["request"], command)
        self.assertEqual(receipt["info"], {"revision": 1, "values": {**legacy, "project": None}})
        self.assertEqual(self.display_client.post(path, json=command).json(), receipt)
        self.assertEqual(self.display_client.get(f"/audit-tasks/{task['id']}").json()["files"][0]["display_info"], receipt["info"])

    def test_display_cas_replay_and_conflicts_keep_the_original_receipt(self):
        files, task, _worker = self.prepared_display_task()
        path = f"/audit-tasks/{task['id']}/display-info"
        command = {
            "request_id": str(uuid.uuid4()),
            "reference": {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": files[0].id},
            "expected_revision": 0,
            "values": {"customer": "A", "model": None, "part_number": None, "part": "A"},
        }
        competing = {**command, "request_id": str(uuid.uuid4()), "values": {**command["values"], "part": "B"}}
        with ThreadPoolExecutor(2) as pool:
            replies = list(pool.map(lambda body: self.display_client.post(path, json=body), [command, competing]))
        self.assertTrue(all(reply.status_code == 200 for reply in replies))
        self.assertEqual(sorted(reply.json()["state"] for reply in replies), ["conflict", "saved"])
        saved = next(reply.json() for reply in replies if reply.json()["state"] == "saved")
        conflict = next(reply.json() for reply in replies if reply.json()["state"] == "conflict")
        self.assertEqual(conflict["info"], saved["info"])
        self.assertEqual(self.display_client.post(path, json=saved["request"]).json(), saved)
        self.assertEqual(self.display_client.post(path, json={**saved["request"], "values": {**command["values"], "part": "不同请求"}}).status_code, 409)
        newer = {**command, "request_id": str(uuid.uuid4()), "expected_revision": 1, "values": {**command["values"], "part": "第二版本"}}
        self.assertEqual(self.display_client.post(path, json=newer).json()["info"]["revision"], 2)
        self.assertEqual(self.display_client.get(path + "/" + saved["request"]["request_id"]).json(), saved)
        self.assertEqual(self.display_client.get(path + "/" + conflict["request"]["request_id"]).json(), conflict)
        self.assertIsNone(self.display_client.get(path + "/" + str(uuid.uuid4())).json())

    def test_display_cannot_treat_a_future_revision_as_a_conflict_with_an_older_snapshot(self):
        files, task, _worker = self.prepared_display_task()
        command = {
            "request_id": str(uuid.uuid4()),
            "reference": {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": files[0].id},
            "expected_revision": 10,
            "values": {"customer": None, "model": None, "part_number": None, "part": None},
        }
        path = f"/audit-tasks/{task['id']}/display-info"
        receipt = self.display_client.post(path, json=command).json()
        self.assertEqual(receipt["state"], "rejected")
        self.assertIsNone(receipt["info"])
        self.assertEqual(self.display_client.get(path + "/" + command["request_id"]).json(), receipt)
        self.assertEqual(self.store.get_audit_task(self.alice, task["id"])["files"][0]["display_info"]["revision"], 0)

    def test_display_requires_current_review_access_but_saved_receipt_remains_readable(self):
        from product.db import connect

        files, task, _worker = self.prepared_display_task()
        path = f"/audit-tasks/{task['id']}"
        command = {
            "request_id": str(uuid.uuid4()),
            "reference": {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": files[0].id},
            "expected_revision": 0,
            "values": {"customer": "更正客户", "model": None, "part_number": None, "part": None},
        }
        receipt = self.display_client.post(path + "/display-info", json=command).json()
        self.assertEqual(receipt["state"], "saved")
        for resource_id in (task["id"], task["round_id"]):
            with self.subTest(resource_id=resource_id), connect(self.settings, "migrator") as conn:
                conn.execute("DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s AND capability = 'review'", (resource_id, self.alice.user_id))
                loaded = self.display_client.get(path)
                self.assertEqual(loaded.status_code, 200)
                self.assertEqual(loaded.json()["files"][0]["display_edit_access"]["state"], "denied")
                self.assertEqual(self.display_client.post(path + "/display-info", json={**command, "request_id": str(uuid.uuid4()), "expected_revision": 1}).status_code, 403)
                self.assertEqual(self.display_client.get(path + "/display-info/" + command["request_id"]).json(), receipt)
                conn.execute("INSERT INTO product.resource_grants (resource_id, user_id, capability) VALUES (%s, %s, 'review')", (resource_id, self.alice.user_id))
        with connect(self.settings, "migrator") as conn:
            conn.execute("DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s", (task["round_id"], self.alice.user_id))
        self.assertEqual(self.display_client.get(path + "/display-info/" + command["request_id"]).status_code, 403)

    def test_display_does_not_expand_private_task_ownership_or_accept_other_frozen_inputs(self):
        from product.db import connect

        files, task, _worker = self.prepared_display_task()
        path = f"/audit-tasks/{task['id']}"
        command = {
            "request_id": str(uuid.uuid4()),
            "reference": {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": files[0].id},
            "expected_revision": 0,
            "values": {"customer": None, "model": None, "part_number": None, "part": None},
        }
        for actor in ("bob", "carol"):
            self.display_principal = self.store.principal_for_fixture(actor)
            with connect(self.settings, "migrator") as conn:
                for resource_id in (task["id"], task["round_id"]):
                    for capability in ("read", "review"):
                        conn.execute(
                            "INSERT INTO product.resource_grants (resource_id, user_id, capability) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                            (resource_id, self.display_principal.user_id, capability),
                        )
            self.assertEqual(self.display_client.post(path + "/display-info", json=command).status_code, 403)
            self.assertEqual(self.display_client.get(path + "/display-info/" + command["request_id"]).status_code, 403)
        self.display_principal = self.alice
        for key in ("round_id", "file_version_id"):
            invalid = {**command, "reference": {**command["reference"], key: str(uuid.uuid4())}}
            self.assertEqual(self.display_client.post(path + "/display-info", json=invalid).status_code, 403)
        for invalid in (
            {**command, "reference": {**command["reference"], "task_id": str(uuid.uuid4())}},
            {**command, "expected_revision": True},
            {**command, "values": {**command["values"], "part": []}},
            {**command, "values": {**command["values"], "rule": "不可更正"}},
            {**command, "audit": {"verdict": "PASS"}},
            {**command, "request_id": command["request_id"].upper()},
            {**command, "reference": {**command["reference"], "file_version_id": files[0].id.upper()}},
        ):
            self.assertEqual(self.display_client.post(path + "/display-info", json=invalid).status_code, 400)
        self.assertIsNone(self.display_client.get(path + "/display-info/" + command["request_id"]).json())
        self.assertEqual(self.display_client.get(path).json()["files"][0]["display_info"]["revision"], 0)

    def test_display_corrections_and_original_requests_are_scoped_to_the_task_round_and_version(self):
        files, first, _worker = self.prepared_display_task()
        command = {
            "request_id": str(uuid.uuid4()),
            "reference": {"task_id": first["id"], "round_id": first["round_id"], "file_version_id": files[0].id},
            "expected_revision": 0,
            "values": {"customer": "A", "model": None, "part_number": None, "part": "第一轮名称"},
        }
        receipt = self.display_client.post(f"/audit-tasks/{first['id']}/display-info", json=command).json()
        self.assertEqual(receipt["state"], "saved")
        _files, second, _worker = self.prepared_display_task(files)
        path = f"/audit-tasks/{second['id']}"
        loaded = self.display_client.get(path).json()
        self.assertEqual(loaded["files"][0]["display_info"], {"revision": 0, "values": {"customer": "测试客户", "model": "M1", "project": None, "part_number": "P-1", "part": "原零件"}})
        second_command = {**command, "reference": {**command["reference"], "task_id": second["id"], "round_id": second["round_id"]}, "values": {**command["values"], "part": "第二轮名称"}}
        self.assertEqual(self.display_client.post(path + "/display-info", json=second_command).status_code, 409)
        self.assertIsNone(self.display_client.get(path + "/display-info/" + command["request_id"]).json())
        self.assertEqual(self.display_client.post(path + "/display-info", json={**second_command, "request_id": str(uuid.uuid4())}).json()["info"]["revision"], 1)
        self.assertEqual(self.display_client.get(f"/audit-tasks/{first['id']}").json()["files"][0]["display_info"], receipt["info"])

    def test_display_save_waiting_on_revoked_review_permission_cannot_commit(self):
        from product.db import connect

        files, task, _worker = self.prepared_display_task()
        path = f"/audit-tasks/{task['id']}"
        command = {
            "request_id": str(uuid.uuid4()),
            "reference": {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": files[0].id},
            "expected_revision": 0,
            "values": {"customer": None, "model": None, "part_number": None, "part": "撤权后不可写"},
        }
        with ThreadPoolExecutor(1) as pool, connect(self.settings, "migrator") as revoker:
            with revoker.transaction():
                revoker_pid = revoker.execute("SELECT pg_backend_pid()").fetchone()[0]
                revoker.execute("SELECT 1 FROM product.resource_grants WHERE resource_id = %s AND user_id = %s AND capability = 'review' FOR UPDATE", (task["round_id"], self.alice.user_id)).fetchone()
                pending = pool.submit(self.display_client.post, path + "/display-info", json=command)
                with connect(self.settings, "migrator") as observer:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if observer.execute("SELECT 1 FROM pg_stat_activity WHERE datname = %s AND %s = ANY(pg_blocking_pids(pid))", (self.database, revoker_pid)).fetchone():
                            break
                        time.sleep(0.01)
                    else:
                        self.fail("display save did not reach the locked review permission")
                revoker.execute("DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s AND capability = 'review'", (task["round_id"], self.alice.user_id))
            self.assertEqual(pending.result(timeout=5).status_code, 403)
        self.assertIsNone(self.display_client.get(path + "/display-info/" + command["request_id"]).json())
        loaded = self.display_client.get(path).json()["files"][0]
        self.assertEqual(loaded["display_info"]["revision"], 0)
        self.assertEqual(loaded["display_edit_access"]["state"], "denied")

    def prepared_query_task(self, *, verified=True, documents=(("控制计划.xlsx", "P-1"),)):
        del verified
        import json
        from io import BytesIO

        from agent.mapping import checker_response, product_result, spec_from_work
        from audit.audits import DEEP_PROTOCOL_REVISION
        from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource
        from openpyxl import Workbook

        versions = []
        for filename, value in documents:
            workbook = Workbook()
            workbook.active.append(["零件号"])
            workbook.active.append([value])
            content = BytesIO()
            workbook.save(content)
            workbook.close()
            raw = content.getvalue()
            version = self.store.create_staging(self.alice, filename, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", len(raw))
            self.store.put_blob(version, raw)
            versions.append(self.store.finalize_version(version))
        _files, task, worker = self.prepared_display_task(versions, complete_audit=False, per_file_rules=len(versions) > 1)
        job = worker.claim_job("verified-query")
        work = worker.round_work(task["round_id"])
        spec = spec_from_work(work, job.payload["executor"], job.payload["executor_revision"], DEEP_PROTOCOL_REVISION).model_copy(update={"job_id": job.id, "attempt": job.attempt})
        executed = []
        for target, (_filename, value) in zip((item for item in spec.files if item.role == "target"), documents, strict=True):
            locator = json.dumps({"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A2", "file_version_id": target.file_version_id}, ensure_ascii=False)
            executed.append(
                (
                    target,
                    [
                        AuditResultItem(
                            classification="基本信息",
                            item="必须填写零件号",
                            priority="P0",
                            status="PASS",
                            suggestions="",
                            clause_id="1",
                            checks_performed=1,
                            scope={"total": 1, "checked": 1, "unknown": 0},
                            evidence=AuditEvidence(
                                required="structure",
                                used="structure",
                                source_format="xlsx",
                                prepared_format="xlsx",
                                sources=(EvidenceSource(kind="structure", locator=locator, excerpt=value),),
                            ),
                        )
                    ],
                )
            )
        result = product_result(checker_response(spec, executed, usage={"input_tokens": 1, "output_tokens": 1}, usage_source="controlled"), work)
        self.assertIsNone(result["coverage"].get("verification"))
        self.assertTrue(worker.complete_job(job, "verified-query", result))
        return versions[0], task, worker, job, result

    def test_validated_query_result_survives_commit_and_is_shared_by_task_and_round_readers(self):
        version, task, worker, job, result = self.prepared_query_task()
        path = f"/audit-tasks/{task['id']}"
        current = self.display_client.get(path)
        self.assertEqual(current.status_code, 200)
        audit = current.json()["files"][0]["audit"]
        self.assertEqual((audit["conclusion"], audit["counts"]["pass_count"]), ("PASS", 1))
        self.assertFalse(audit["checks"][0]["evidence_verified"])
        self.assertEqual(audit["checks"][0]["evidence"][0]["excerpt"], "P-1")
        historical = self.display_client.get(f"/rounds/{task['round_id']}?read_view=true").json()
        self.assertEqual(historical["result_job_id"], job.id)
        self.assertEqual(historical["coverage"]["verification"], result["coverage"]["verification"])
        self.assertEqual({key: value for key, value in historical["read_view"]["files"][0]["audit"].items() if key != "report"}, {key: value for key, value in audit.items() if key != "report"})
        self.assertEqual(audit["report"]["state"], "pending")
        self.assertEqual(current.json()["state"], "reviewing")
        self.assertEqual([row["id"] for row in self.store.list_audit_tasks(self.alice, state="reviewing")], [task["id"]])
        self.assertEqual(self.store.list_audit_tasks(self.alice, state="completed"), [])
        self.assertFalse(worker.complete_job(job, "verified-query", {"findings": [], "coverage": {}}, "succeeded"))
        self.assertEqual(self.display_client.get(path).json()["files"][0]["audit"], audit)
        self.assertEqual(self.store.list_usage_events(self.alice), [])
        pdf_job = worker.claim_job("pdf-render")
        self.assertEqual(pdf_job.kind, "report_pdf")
        self.assertEqual(self.display_client.get(path).json()["files"][0]["audit"]["report"]["state"], "generating")
        from worker import process_claimed

        process_claimed(worker, pdf_job, "pdf-render")
        finished = self.display_client.get(path).json()
        report = finished["files"][0]["audit"]["report"]
        self.assertEqual((finished["state"], report["state"]), ("completed", "ready"))
        self.assertGreater(finished["updated_at"], current.json()["updated_at"])
        self.assertEqual([row["id"] for row in self.store.list_audit_tasks(self.alice, state="completed")], [task["id"]])
        self.assertEqual(self.store.list_audit_tasks(self.alice, state="reviewing"), [])
        unchanged = self.display_client.get(f"/rounds/{task['round_id']}").json()
        self.assertEqual(
            (unchanged["execution"], unchanged["result_attempt"], unchanged["findings"], unchanged["coverage"]),
            (historical["execution"], historical["result_attempt"], historical["findings"], historical["coverage"]),
        )
        pdf_path = f"/audit-tasks/{task['id']}/reports/{report['report_id']}/pdf"
        query = {"round_id": task["round_id"], "file_version_id": version.id, "result_attempt": 1, "artifact_id": report["artifact"]["artifact_id"]}
        downloaded = self.display_client.get(pdf_path, params=query)
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.headers["content-type"], "application/pdf")
        self.assertEqual(len(downloaded.content), report["artifact"]["byte_size"])
        import pymupdf

        with pymupdf.open(stream=downloaded.content, filetype="pdf") as pdf:
            text = "".join(page.get_text() for page in pdf)
            self.assertIn("必须填写零件号", text)
            self.assertIn("P-1", text)
            self.assertIn("共1项：符合1项，不符合0项，无法判断0项，不适用0项。", "".join(text.split()))
            self.assertIn("部分判定的证据未经验证", text)
        for field, value in (("round_id", str(uuid.uuid4())), ("file_version_id", str(uuid.uuid4())), ("result_attempt", 2), ("artifact_id", "other-artifact")):
            self.assertEqual(self.display_client.get(pdf_path, params={**query, field: value}).status_code, 403)
        self.display_principal = self.store.principal_for_fixture("bob")
        self.assertEqual(self.display_client.get(pdf_path, params=query).status_code, 403)
        self.display_principal = self.store.principal_for_fixture("carol")
        self.assertEqual(self.display_client.get(pdf_path, params=query).status_code, 403)
        self.display_principal = None
        self.assertEqual(self.display_client.get(pdf_path, params=query).status_code, 401)

    def test_all_pass_checker_disclosure_is_frozen_in_task_history_pdf_and_share(self):
        from product.store.report_sharing import ReportShareStore
        from product.store.suite_releases import SuiteReleaseStore
        from worker import process_claimed

        version, task, worker, _job, _result = self.prepared_query_task()
        path = f"/audit-tasks/{task['id']}"
        audit = self.display_client.get(path).json()["files"][0]["audit"]
        verification = audit["checker_verification"]
        self.assertEqual((audit["conclusion"], audit["counts"]["pass_count"], verification["status"]), ("PASS", 1, "unverified"))
        self.assertIn("未经验证", verification["notice"])
        self.assertIn(verification["suite_revision_id"], verification["notice"])
        pdf_job = worker.claim_job("qualified-pdf")
        self.assertEqual(pdf_job.payload["report_input"]["audit"]["checker_verification"], verification)
        # The cover uses the accepted recognition, not a later display correction.
        recognized = self.display_client.get(path).json()["files"][0]["match"]["metadata"]
        self.assertEqual(pdf_job.payload["report_input"]["recognition"], {key: recognized.get(key) for key in ("customer", "model", "project", "part_number", "part")})
        self.assertTrue(pdf_job.payload["report_input"]["audited_at"])
        process_claimed(worker, pdf_job, "qualified-pdf")
        finished = self.display_client.get(path).json()
        final_audit = finished["files"][0]["audit"]
        report = final_audit["report"]
        reference = {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": version.id, "result_attempt": 1, "report_id": report["report_id"]}
        command = {"request_id": str(uuid.uuid4()), "organization_id": self.alice.tenant_key, "reports": [reference], "targets": [], "content": {"kind": "link", "artifact": None, "package_id": None}}
        shares = ReportShareStore(self.store)
        with self.store._conn() as conn:
            saved = shares.accept(conn, self.alice, command, [])
        self.assertEqual(saved["payload"]["sources"][0]["checker_verification"], verification)
        # Publish a new immutable source; all prior-round and shared claims stay fixed.
        releases = SuiteReleaseStore(self.settings, self.store.objects, role="api")
        suite = releases.suites.resolve_suite(verification["suite_revision_id"], self.alice.tenant_id)
        active = releases.active(self.alice, suite.revision.suite_key)
        candidate = releases.save_candidate(self.alice, suite.revision.id, {"checker.py": suite.files["checker.py"].decode() + "\n# later version\n"})
        releases.publish(self.alice, suite.revision.suite_key, candidate["id"], active["revision"], str(uuid.uuid4()), force_unverified=True)
        self.assertEqual(self.display_client.get(path).json()["files"][0]["audit"]["checker_verification"], verification)
        historical = self.display_client.get(f"/rounds/{task['round_id']}?read_view=true").json()
        self.assertEqual(historical["read_view"]["files"][0]["audit"]["checker_verification"], verification)
        with shares.operation(self.alice, command["request_id"]) as (restored, _guard):
            self.assertEqual(restored["payload"]["sources"][0]["checker_verification"], verification)
        pdf = self.display_client.get(
            path + f"/reports/{report['report_id']}/pdf",
            params={**{key: reference[key] for key in ("round_id", "file_version_id", "result_attempt")}, "artifact_id": report["artifact"]["artifact_id"]},
        )
        self.assertEqual(pdf.status_code, 200)
        import pymupdf

        with pymupdf.open(stream=pdf.content, filetype="pdf") as document:
            text = "".join("".join(page.get_text().split()) for page in document)
            self.assertIn("".join(verification["notice"].split()), text)
            self.assertIn("审核结论：符合", text)

    def test_missing_verification_mark_preserves_machine_pass_and_generates_pdf(self):
        from worker import process_claimed

        _version, task, worker, _job, _result = self.prepared_query_task(verified=False)
        path = f"/audit-tasks/{task['id']}"
        observed = self.display_client.get(path).json()
        audit = observed["files"][0]["audit"]
        self.assertEqual((observed["state"], audit["conclusion"], audit["report"]["state"]), ("reviewing", "PASS", "pending"))
        self.assertEqual(audit["counts"]["pass_count"], 1)
        self.assertFalse(audit["checks"][0]["evidence_verified"])
        pdf_job = worker.claim_job("unverified-pdf")
        self.assertEqual(pdf_job.kind, "report_pdf")
        process_claimed(worker, pdf_job, "unverified-pdf")
        finished = self.display_client.get(path).json()
        final_audit = finished["files"][0]["audit"]
        self.assertEqual((finished["state"], final_audit["conclusion"], final_audit["report"]["state"]), ("completed", "PASS", "ready"))
        self.assertFalse(final_audit["checks"][0]["evidence_verified"])
        self.assertEqual(self.store.list_usage_events(self.alice), [])

    def test_long_pdf_filename_preserves_the_pdf_suffix(self):
        from worker import process_claimed

        _version, task, worker, _job, _result = self.prepared_query_task(documents=(("A" * 250 + ".xlsx", "P-1"),))
        pdf_job = worker.claim_job("long-pdf-name")
        process_claimed(worker, pdf_job, "long-pdf-name")
        report = self.display_client.get(f"/audit-tasks/{task['id']}").json()["files"][0]["audit"]["report"]
        self.assertEqual(report["state"], "ready")
        self.assertEqual(report["artifact"]["filename"], "A" * 246 + "-审核报告.pdf")

    def test_two_frozen_file_reports_keep_their_own_sources_rules_and_download_identity(self):
        import pymupdf
        from worker import process_claimed

        _version, task, worker, _job, _result = self.prepared_query_task(documents=(("first.xlsx", "A_ONLY_017"), ("second.xlsx", "B_ONLY_029")))
        path = f"/audit-tasks/{task['id']}"
        initial = self.display_client.get(path).json()
        self.assertEqual([row["audit"]["report"]["state"] for row in initial["files"]], ["pending", "pending"])
        self.assertEqual(len({row["audit"]["rule"]["id"] for row in initial["files"]}), 2)
        for state in ("reviewing", "completed"):
            pdf_job = worker.claim_job("two-pdfs")
            process_claimed(worker, pdf_job, "two-pdfs")
            self.assertEqual(self.display_client.get(path).json()["state"], state)
        files = self.display_client.get(path).json()["files"]
        self.assertEqual(len({row["audit"]["report"]["report_id"] for row in files}), 2)
        for row, value, other in ((files[0], "A_ONLY_017", "B_ONLY_029"), (files[1], "B_ONLY_029", "A_ONLY_017")):
            audit, report = row["audit"], row["audit"]["report"]
            pdf_path = f"{path}/reports/{report['report_id']}/pdf"
            query = {"round_id": task["round_id"], "file_version_id": row["file"]["id"], "result_attempt": 1, "artifact_id": report["artifact"]["artifact_id"]}
            response = self.display_client.get(pdf_path, params=query)
            self.assertEqual(response.status_code, 200)
            with pymupdf.open(stream=response.content, filetype="pdf") as pdf:
                text = "".join(page.get_text() for page in pdf)
                self.assertIn(value, text)
                self.assertNotIn(other, text)
                self.assertIn(f"file_version={row['file']['id']};result_attempt=1;rule_version={audit['rule']['id']}", pdf.metadata["subject"])
            other_row = next(file for file in files if file["file"]["id"] != row["file"]["id"])
            self.assertEqual(self.display_client.get(pdf_path, params={**query, "file_version_id": other_row["file"]["id"]}).status_code, 403)
            other_report = other_row["audit"]["report"]
            self.assertEqual(self.display_client.get(f"{path}/reports/{other_report['report_id']}/pdf", params={**query, "artifact_id": other_report["artifact"]["artifact_id"]}).status_code, 403)
        self.assertIsNone(worker.claim_job("two-pdfs"))

    def prepared_report_package(self, *, accept=True):
        from worker import process_claimed

        _version, task, worker, _job, _result = self.prepared_query_task(documents=(("same.xlsx", "A_ONLY_017"), ("same.xlsx", "B_ONLY_029")))
        for _ in range(2):
            process_claimed(worker, worker.claim_job("zip-pdfs"), "zip-pdfs")
        task_path = f"/audit-tasks/{task['id']}"
        completed = self.display_client.get(task_path).json()
        reports = [
            {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": row["file"]["id"], "result_attempt": 1, "report_id": row["audit"]["report"]["report_id"]}
            for row in completed["files"]
        ]
        request_id = str(uuid.uuid4())
        command = {"request_id": request_id, "reports": reports}
        if not accept:
            return task, worker, completed, command, None
        accepted = self.display_client.post("/audit-report-packages", json=command)
        self.assertEqual(accepted.status_code, 202)
        return task, worker, completed, command, accepted.json()

    def test_archive_zip_accepts_frozen_reports_from_different_tasks_without_changing_task_packages(self):
        import json
        from io import BytesIO
        from urllib.parse import parse_qs, urlsplit
        from zipfile import ZipFile

        from product import build_product_store
        from product.fixtures import APP_ID
        from worker import process_claimed

        self.settings.feishu_app_id = APP_ID
        first, _worker, _completed, first_command, _ = self.prepared_report_package(accept=False)
        second, worker, _completed, second_command, _ = self.prepared_report_package(accept=False)
        reports = [first_command["reports"][0], second_command["reports"][0]]
        command = {"request_id": str(uuid.uuid4()), "reports": reports}
        accepted = self.display_client.post("/audit-report-packages", json=command)
        self.assertEqual(accepted.status_code, 202, accepted.text)
        package = accepted.json()
        self.assertEqual(package["reports"], reports)
        self.assertEqual(self.display_client.post("/audit-report-packages", json=command).json(), package)
        for task in (first, second):
            self.assertNotIn("report_package", self.display_client.get(f"/audit-tasks/{task['id']}").json())
        job = worker.claim_job("archive-cross-task")
        self.assertEqual(job.kind, "report_zip")
        process_claimed(worker, job, "archive-cross-task")
        ready = self.display_client.get(f"/audit-report-packages/{command['request_id']}").json()
        self.assertEqual(ready["state"], "ready")
        self.assertEqual([outcome["filename"] for outcome in ready["outcomes"]], ["same.xlsx-审核报告.pdf", "same.xlsx-审核报告.pdf"])
        downloaded = self.display_client.get(
            f"/audit-report-packages/{command['request_id']}/zip",
            params={"package_id": ready["package_id"], "artifact_id": ready["artifact"]["artifact_id"]},
        )
        self.assertEqual(downloaded.status_code, 200, downloaded.text[:100])
        with ZipFile(BytesIO(downloaded.content)) as archive:
            entries = json.loads(archive.read("报告清单.txt"))["reports"]
            self.assertEqual({entry["reference"]["task_id"] for entry in entries}, {first["id"], second["id"]})
            self.assertEqual(len({entry["archive_name"] for entry in entries}), 2)
            self.assertEqual(len({entry["filename"] for entry in entries}), 1)
            for entry in entries:
                reference = entry["reference"]
                source = next(source for source in job.payload["sources"] if source["reference"] == reference)
                self.assertEqual(archive.read(entry["archive_name"]), worker.objects.get(source["object_key"]))
        alias = self.display_client.post("/audit-report-packages", json={"request_id": str(uuid.uuid4()), "reports": list(reversed(reports))})
        self.assertEqual(alias.status_code, 202, alias.text)
        self.assertEqual(alias.json()["package_id"], ready["package_id"])
        self.assertEqual(alias.json()["reports"], list(reversed(reports)))
        api_store = build_product_store(self.settings, objects=self.store.objects, role="api")
        notified = []
        while claimed := api_store.claim_task_notification("archive-notifier"):
            notification, recipient = claimed
            delivery = api_store.start_task_notification(notification, "archive-notifier", recipient)
            if delivery:
                if notification.payload["stage"] == "zip_ready":
                    card = delivery["card"]
                    notified.append(card)
                    target = urlsplit(parse_qs(urlsplit(card["body"]["elements"][1]["behaviors"][0]["default_url"]).query)["return_to"][0])
                    self.assertEqual(parse_qs(target.query), {"view": ["archive"], "packageRequest": [command["request_id"]]})
                api_store.finish_task_notification(notification, "archive-notifier", {"state": "sent", "message_id": "om_test", "reason": None})
        self.assertEqual(len(notified), 1)
        self.assertIsNone(worker.claim_job("no-extra-archive-work"))
        self.assertEqual(self.store.list_usage_events(self.alice), [])

    def test_archive_zip_rechecks_every_task_and_recovers_only_the_same_frozen_collection(self):
        from product.db import connect
        from worker import process_claimed

        first, _worker, _completed, first_command, _ = self.prepared_report_package(accept=False)
        second, worker, _completed, second_command, _ = self.prepared_report_package(accept=False)
        reports = [first_command["reports"][0], second_command["reports"][0]]
        command = {"request_id": str(uuid.uuid4()), "reports": reports}
        accepted = self.display_client.post("/audit-report-packages", json=command)
        self.assertEqual(accepted.status_code, 202, accepted.text)
        package = accepted.json()
        old = worker.claim_job("archive-failed")
        orphan = worker.build_report_package(old, "2026-10-20T00:00:00+00:00", threading.Event())
        self.assertTrue(worker.complete_job(old, "archive-failed", {"error": "temporary_failure"}, "failed"))
        path = f"/audit-report-packages/{command['request_id']}"
        retry = {"request_id": str(uuid.uuid4()), "package_id": package["package_id"], "expected_attempt": 1}
        with connect(self.settings, "migrator") as conn:
            capabilities = conn.execute("DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s RETURNING capability", (second["round_id"], self.alice.user_id)).fetchall()
        self.assertEqual(self.display_client.get(path).status_code, 403)
        self.assertEqual(self.display_client.post(path + "/retries", json=retry).status_code, 403)
        self.assertEqual(self.display_client.post("/audit-report-packages", json={**command, "request_id": str(uuid.uuid4())}).status_code, 403)
        self.assertIsNone(worker.claim_job("permission-denied-cannot-retry"))
        with connect(self.settings, "migrator") as conn:
            for (capability,) in capabilities:
                conn.execute("INSERT INTO product.resource_grants VALUES (%s, %s, %s)", (second["round_id"], self.alice.user_id, capability))
        recovered = self.display_client.post(path + "/retries", json=retry)
        self.assertEqual(recovered.status_code, 202, recovered.text)
        self.assertEqual(recovered.json()["reports"], reports)
        current = worker.claim_job("archive-recovery")
        self.assertEqual((current.id, current.attempt), (old.id, 2))
        self.assertEqual(current.payload, old.payload)
        self.assertFalse(worker.complete_job(old, "archive-failed", orphan))
        process_claimed(worker, current, "archive-recovery")
        ready = self.display_client.get(path).json()
        self.assertEqual((ready["state"], ready["artifact"]["artifact_id"]), ("ready", f"{old.id}:2"))
        with connect(self.settings, "migrator") as conn:
            conn.execute("DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s", (first["round_id"], self.alice.user_id))
        self.assertEqual(self.display_client.get(path).status_code, 403)
        self.assertEqual(self.display_client.post("/audit-report-packages", json=command).status_code, 403)
        self.assertEqual(self.display_client.get(path + "/zip", params={"package_id": ready["package_id"], "artifact_id": ready["artifact"]["artifact_id"]}).status_code, 403)
        self.assertEqual(self.store.list_usage_events(self.alice), [])

    def test_report_share_freezes_reports_and_keeps_recipient_task_and_original_private(self):
        import json
        from datetime import datetime, timedelta, timezone

        import httpx
        from api import create_app
        from fastapi.testclient import TestClient
        from product import build_product_store
        from product.feishu import UserTokens
        from product.identity import SESSION_COOKIE

        task, _worker, completed, package_command, _ = self.prepared_report_package(accept=False)
        self.settings.feishu_app_id, self.settings.feishu_app_secret = self.alice.app_id, "test-secret"
        self.store.save_app_ticket(self.alice.app_id, "test-ticket")
        bob = self.store.principal_for_fixture("bob")
        active = [self.alice]
        sent = []

        def provider(request):
            path = request.url.path
            if path.endswith("user_info"):
                return httpx.Response(200, json={"code": 0, "data": {"tenant_key": active[0].tenant_key, "open_id": active[0].subject, "name": "审核人"}})
            if path.endswith("app_access_token"):
                return httpx.Response(200, json={"code": 0, "app_access_token": "test-app-token"})
            if path.endswith("tenant_access_token"):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "test-tenant-token"})
            if "/contact/v3/users/" in path:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "user": {
                                "open_id": path.rsplit("/", 1)[-1],
                                "name": "组织成员",
                                "status": {"is_activated": True, "is_resigned": False, "is_frozen": False, "is_exited": False, "is_unjoin": False},
                            }
                        },
                    },
                )
            if path.endswith("/im/v1/messages"):
                sent.append(json.loads(request.content))
                return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_share"}})
            raise AssertionError(path)

        api = build_product_store(self.settings, objects=self.store.objects, role="api")
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(self.settings, auth_store=api, http=http)) as client:
            client.cookies.set(SESSION_COOKIE, api.create_session(self.alice, UserTokens("test-user-token", "", expires, expires)))
            command = {
                "request_id": str(uuid.uuid4()),
                "organization_id": self.alice.tenant_key,
                "reports": package_command["reports"],
                "targets": [{"id": bob.subject, "kind": "user"}],
                "content": {"kind": "link", "artifact": None, "package_id": None},
            }
            path = "/audit-report-shares/" + command["request_id"]
            response = client.post("/audit-report-shares", json=command)
            self.assertEqual(response.status_code, 200, response.text)
            receipt = response.json()
            self.assertEqual(receipt["deliveries"][0]["state"], "sent")
            self.assertEqual(client.post("/audit-report-shares", json=command).json(), receipt)
            self.assertEqual(len(sent), 1)
            self.assertEqual(client.get("/audit-tasks/" + task["id"]).json()["report_share"], receipt)
            attempts = [{"target": command["targets"][0], "attempt_id": command["request_id"]}]
            self.assertEqual(client.get(path, params={"attempts": json.dumps(attempts)}).json(), receipt)
            from urllib.parse import parse_qs, urlsplit

            for file in completed["files"]:
                verification = file["audit"]["checker_verification"]
                self.assertIn(verification["notice"], json.dumps(json.loads(sent[0]["content"]), ensure_ascii=False))
            link = json.loads(sent[0]["content"])["zh_cn"]["content"][-1][0]["href"]
            self.assertIn("share=" + command["request_id"], parse_qs(urlsplit(link).query)["return_to"][0])
            active[0] = bob
            client.cookies.set(SESSION_COOKIE, api.create_session(bob, UserTokens("test-bob-token", "", expires, expires)))
            self.assertEqual(client.get(path).status_code, 403)
            self.assertEqual(client.get("/audit-tasks/" + task["id"]).status_code, 403)
            shared = client.get(path + "/reports")
            self.assertEqual(shared.status_code, 200, shared.text)
            # Both originals are also tenant rule sources here; the share itself never links an original.
            self.assertNotIn("/download", shared.text)
            self.assertEqual([item["reference"] for item in shared.json()["reports"]], command["reports"])
            first = shared.json()["reports"][0]
            self.assertEqual(first["checker_verification"], completed["files"][0]["audit"]["checker_verification"])
            pdf = client.get(path + "/pdf", params={"report_id": first["reference"]["report_id"], "artifact_id": first["report"]["artifact"]["artifact_id"]})
            self.assertEqual(pdf.status_code, 200)
            self.assertTrue(pdf.content.startswith(b"%PDF-"))
            self.assertEqual(client.get(path + "/pdf", params={"report_id": first["reference"]["report_id"], "artifact_id": "different"}).status_code, 403)
            active[0] = self.store.principal_for_fixture("carol")
            client.cookies.set(SESSION_COOKIE, api.create_session(active[0], UserTokens("test-carol-token", "", expires, expires)))
            self.assertEqual(client.get(path + "/reports").status_code, 403)
            client.cookies.clear()
            self.assertEqual(client.get(path + "/reports").status_code, 401)
        self.assertEqual(self.store.list_usage_events(self.alice), [])

    def prepared_share_transport(self, effect, *, file_mode=False, directory_effect=None):
        import json
        from datetime import datetime, timedelta, timezone

        import httpx
        from api import create_app
        from fastapi.testclient import TestClient
        from product import build_product_store
        from product.feishu import UserTokens
        from product.identity import SESSION_COOKIE

        task, worker, completed, command, _ = self.prepared_report_package(accept=False)
        self.settings.feishu_app_id, self.settings.feishu_app_secret = self.alice.app_id, "test-secret"
        self.store.save_app_ticket(self.alice.app_id, "test-ticket")

        def provider(request):
            path = request.url.path
            if path.endswith("user_info"):
                return httpx.Response(200, json={"code": 0, "data": {"tenant_key": self.alice.tenant_key, "open_id": self.alice.subject, "name": "审核人"}})
            if path.endswith("app_access_token"):
                return httpx.Response(200, json={"code": 0, "app_access_token": "test-app-token"})
            if path.endswith("tenant_access_token"):
                self.assertEqual(json.loads(request.content)["tenant_key"], self.alice.tenant_key)
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "test-tenant-token"})
            if "/contact/v3/users/" in path:
                if directory_effect:
                    return directory_effect(request)
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "user": {
                                "open_id": path.rsplit("/", 1)[-1],
                                "name": "组织成员",
                                "status": {"is_activated": True, "is_resigned": False, "is_frozen": False, "is_exited": False, "is_unjoin": False},
                            }
                        },
                    },
                )
            return effect(request)

        http = httpx.Client(transport=httpx.MockTransport(provider))
        self.addCleanup(http.close)
        api = build_product_store(self.settings, objects=self.store.objects, role="api")
        client = TestClient(create_app(self.settings, auth_store=api, http=http))
        self.addCleanup(client.close)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        client.cookies.set(SESSION_COOKIE, api.create_session(self.alice, UserTokens("test-user-token", "", expires, expires)))
        reports = command["reports"][:1] if file_mode else command["reports"]
        content = {"kind": "pdf", "artifact": completed["files"][0]["audit"]["report"]["artifact"], "package_id": None} if file_mode else {"kind": "link", "artifact": None, "package_id": None}
        share = {
            "request_id": str(uuid.uuid4()),
            "organization_id": self.alice.tenant_key,
            "reports": reports,
            "targets": [{"id": "ou_first", "kind": "user"}, {"id": "ou_second", "kind": "user"}],
            "content": content,
        }
        return client, share, task, worker

    def test_report_sharing_retries_only_a_definite_failure_and_never_replays_an_unknown_send(self):
        import json

        import httpx

        calls = []
        blocked, release = threading.Event(), threading.Event()
        replies = {"ou_first": "sent", "ou_second": "failed"}

        def effect(request):
            self.assertTrue(request.url.path.endswith("/im/v1/messages"))
            message = json.loads(request.content)
            calls.append(message)
            if replies[message["receive_id"]] == "failed":
                return httpx.Response(400, json={"code": 230006})
            if replies[message["receive_id"]] == "unknown":
                blocked.set()
                self.assertTrue(release.wait(timeout=10))
                raise httpx.ReadTimeout("receipt_lost", request=request)
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_" + message["receive_id"]}})

        client, command, _task, worker = self.prepared_share_transport(effect)
        path = "/audit-report-shares/" + command["request_id"]
        first = client.post("/audit-report-shares", json=command)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual([item["state"] for item in first.json()["deliveries"]], ["sent", "failed"])
        self.assertEqual(len({item["uuid"] for item in calls}), 2)
        replies["ou_second"] = "sent"
        retry = {"target": command["targets"][1], "attempt_id": str(uuid.uuid4())}
        retried = client.post(path + "/retries", json=retry)
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual([item["state"] for item in retried.json()["deliveries"]], ["sent", "sent"])
        self.assertEqual([item["receive_id"] for item in calls], ["ou_first", "ou_second", "ou_second"])
        self.assertEqual(client.post(path + "/retries", json=retry).json(), retried.json())
        original = [{"target": target, "attempt_id": command["request_id"]} for target in command["targets"]]
        observed = client.get(path, params={"attempts": json.dumps(original)})
        self.assertEqual([item["state"] for item in observed.json()["deliveries"]], ["sent", "failed"])
        self.assertFalse(observed.json()["deliveries"][1]["retry_allowed"])
        self.assertEqual(len(calls), 3)
        unknown = {**command, "request_id": str(uuid.uuid4())}
        replies["ou_first"] = "unknown"
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(client.post, "/audit-report-shares", json=unknown)
            try:
                self.assertTrue(blocked.wait(timeout=10))
                live_path = "/audit-report-shares/" + unknown["request_id"]
                concurrent = client.post("/audit-report-shares", json=unknown)
                self.assertEqual(concurrent.status_code, 200)
                self.assertEqual(concurrent.json()["deliveries"][0]["state"], "unknown")
                self.assertEqual(client.get(live_path).json()["deliveries"][1]["state"], "pending")
                self.assertEqual(client.post(live_path + "/retries", json={"target": unknown["targets"][0], "attempt_id": str(uuid.uuid4())}).status_code, 409)
            finally:
                release.set()
            response = pending.result(timeout=10)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["state"] for item in response.json()["deliveries"]], ["unknown", "sent"])
        self.assertEqual(client.post(live_path + "/retries", json={"target": unknown["targets"][0], "attempt_id": str(uuid.uuid4())}).status_code, 409)
        self.assertEqual(client.get(live_path).json(), response.json())
        self.assertEqual(client.post("/audit-report-shares", json=unknown).json(), response.json())
        self.assertEqual(len(calls), 5)
        self.assertIsNone(worker.claim_job("sharing-is-not-an-audit-job"))

    def test_lost_share_preparation_guard_cannot_send_after_a_new_attempt_succeeds(self):
        import json

        import httpx
        from product.db import connect

        uploading, release = threading.Event(), threading.Event()
        uploads, sends = [], []

        def effect(request):
            if request.url.path.endswith("/im/v1/files"):
                uploads.append(request.content)
                if len(uploads) == 1:
                    uploading.set()
                    self.assertTrue(release.wait(timeout=10))
                return httpx.Response(200, json={"code": 0, "data": {"file_key": "file_" + str(len(uploads))}})
            self.assertTrue(request.url.path.endswith("/im/v1/messages"))
            sends.append(json.loads(request.content))
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_recovered"}})

        client, command, _task, _worker = self.prepared_share_transport(effect, file_mode=True)
        command["targets"] = command["targets"][:1]
        path = "/audit-report-shares/" + command["request_id"]
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(client.post, "/audit-report-shares", json=command)
            try:
                self.assertTrue(uploading.wait(timeout=10))
                with connect(self.settings, "api") as conn:
                    pids = conn.execute(
                        "SELECT DISTINCT pid FROM pg_locks WHERE database = (SELECT oid FROM pg_database WHERE datname = current_database()) AND locktype = 'advisory' AND granted"
                    ).fetchall()
                    self.assertEqual(len(pids), 1)
                    self.assertTrue(conn.execute("SELECT pg_terminate_backend(%s)", (pids[0][0],)).fetchone()[0])
                interrupted = client.get(path)
                self.assertEqual(interrupted.status_code, 200, interrupted.text)
                self.assertEqual(interrupted.json()["deliveries"][0]["state"], "failed")
                retry = {"target": command["targets"][0], "attempt_id": str(uuid.uuid4())}
                recovered = client.post(path + "/retries", json=retry)
                self.assertEqual(recovered.status_code, 200, recovered.text)
                self.assertEqual(recovered.json()["deliveries"][0]["state"], "sent")
                self.assertEqual(len(sends), 1)
            finally:
                release.set()
            with self.assertRaises(Exception):
                pending.result(timeout=10)
        self.assertEqual(len(sends), 1)
        self.assertEqual(str(uuid.UUID(sends[0]["uuid"])), sends[0]["uuid"])
        self.assertEqual(client.get(path).json(), recovered.json())
        self.assertEqual(len(uploads), 2)

    def test_pdf_and_zip_shares_upload_only_the_selected_frozen_report_artifact(self):
        import json
        from email.parser import BytesParser
        from email.policy import default

        import httpx
        from worker import process_claimed

        uploads, sends = [], []

        def effect(request):
            if request.url.path.endswith("/im/v1/files"):
                message = BytesParser(policy=default).parsebytes(b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content)
                parts = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
                uploads.append(
                    {
                        "kind": parts["file_type"].get_payload(decode=True).decode("utf-8"),
                        "name": parts["file_name"].get_payload(decode=True).decode("utf-8"),
                        "body": parts["file"].get_payload(decode=True),
                    }
                )
                return httpx.Response(200, json={"code": 0, "data": {"file_key": "file_" + str(len(uploads))}})
            sends.append(json.loads(request.content))
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_file"}})

        client, command, task, worker = self.prepared_share_transport(effect, file_mode=True)
        ref = command["reports"][0]
        expected = client.get(f"/audit-tasks/{ref['task_id']}/reports/{ref['report_id']}/pdf", params={**ref, "artifact_id": command["content"]["artifact"]["artifact_id"]})
        self.assertEqual(expected.status_code, 200, expected.text)
        response = client.post("/audit-report-shares", json=command)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row["state"] for row in response.json()["deliveries"]], ["sent", "sent"])
        self.assertEqual(uploads, [{"kind": "pdf", "name": command["content"]["artifact"]["filename"], "body": expected.content}])
        self.assertEqual([json.loads(item["content"]) for item in sends], [{"file_key": "file_1"}, {"file_key": "file_1"}])
        current = client.get("/audit-tasks/" + task["id"]).json()
        references = [
            {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": row["file"]["id"], "result_attempt": 1, "report_id": row["audit"]["report"]["report_id"]}
            for row in current["files"]
        ]
        package_id = str(uuid.uuid4())
        package = client.post("/audit-report-packages", json={"request_id": package_id, "reports": references})
        self.assertEqual(package.status_code, 202, package.text)
        process_claimed(worker, worker.claim_job("sharing-zip"), "sharing-zip")
        ready = client.get("/audit-report-packages/" + package_id).json()
        artifact = ready["artifact"]
        expected_zip = client.get("/audit-report-packages/" + package_id + "/zip", params={"package_id": ready["package_id"], "artifact_id": artifact["artifact_id"]})
        zipped = {**command, "request_id": str(uuid.uuid4()), "reports": references, "content": {"kind": "zip", "artifact": artifact, "package_id": ready["package_id"]}}
        zipped_response = client.post("/audit-report-shares", json=zipped)
        self.assertEqual(zipped_response.status_code, 200, zipped_response.text)
        self.assertEqual([row["state"] for row in zipped_response.json()["deliveries"]], ["sent", "sent"])
        self.assertEqual(uploads[1], {"kind": "stream", "name": artifact["filename"], "body": expected_zip.content})
        shared_zip = client.get("/audit-report-shares/" + zipped["request_id"] + "/zip", params={"artifact_id": artifact["artifact_id"]})
        self.assertEqual(shared_zip.content, expected_zip.content)
        invalid = {**command, "request_id": str(uuid.uuid4()), "reports": references}
        self.assertEqual(client.post("/audit-report-shares", json=invalid).status_code, 409)
        self.assertEqual((len(uploads), len(sends)), (2, 4))
        self.assertEqual(len({item["uuid"] for item in sends}), 4)
        self.assertEqual(self.store.list_usage_events(self.alice), [])

    def test_failed_target_can_be_rechecked_after_a_temporary_directory_failure_without_resending_successes(self):
        import json

        import httpx

        directory_calls, sends = {}, []
        restored = [False]

        def directory(request):
            target = request.url.path.rsplit("/", 1)[-1]
            directory_calls[target] = directory_calls.get(target, 0) + 1
            if target == "ou_second" and directory_calls[target] > 1 and not restored[0]:
                return httpx.Response(503, json={"code": 1})
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"user": {"open_id": target, "name": "组织审核人", "status": {"is_activated": True, "is_resigned": False, "is_frozen": False, "is_exited": False, "is_unjoin": False}}},
                },
            )

        def effect(request):
            sends.append(json.loads(request.content))
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_sent"}})

        client, command, _task, _worker = self.prepared_share_transport(effect, directory_effect=directory)
        path = "/audit-report-shares/" + command["request_id"]
        response = client.post("/audit-report-shares", json=command)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["state"] for item in response.json()["deliveries"]], ["sent", "failed"])
        self.assertEqual(response.json()["deliveries"][1]["target"]["eligibility"], "unknown")
        restored[0] = True
        rechecked = client.get(path)
        self.assertEqual(rechecked.status_code, 200, rechecked.text)
        self.assertTrue(rechecked.json()["deliveries"][1]["retry_allowed"])
        self.assertEqual(rechecked.json()["deliveries"][1]["target"]["eligibility"], "eligible")
        self.assertEqual([item["receive_id"] for item in sends], ["ou_first"])
        retry = {"target": command["targets"][1], "attempt_id": str(uuid.uuid4())}
        recovered = client.post(path + "/retries", json=retry)
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual([item["state"] for item in recovered.json()["deliveries"]], ["sent", "sent"])
        self.assertEqual([item["receive_id"] for item in sends], ["ou_first", "ou_second"])

    def test_missing_group_permission_does_not_block_the_freshly_verified_self_target(self):
        import httpx

        def effect(request):
            self.assertEqual(request.url.path, "/open-apis/im/v1/chats")
            return httpx.Response(400, json={"code": 99991672, "msg": "private provider diagnostic"})

        client, _command, _task, _worker = self.prepared_share_transport(effect)
        denied = client.get("/audit-share-targets", params={"kind": "group"})
        self.assertEqual(denied.status_code, 503)
        self.assertEqual(denied.json(), {"error": "share_directory_permission_missing"})
        own = client.get("/audit-share-targets", params={"kind": "user"})
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual([(target["id"], target["eligibility"]) for target in own.json()["targets"]], [(self.alice.subject, "eligible")])

    def test_attachment_permission_recovery_keeps_failed_targets_and_never_sends_during_readback(self):
        import json

        import httpx

        granted, sends = [False], []

        def effect(request):
            if request.url.path.endswith("/im/v1/files"):
                return httpx.Response(200, json={"code": 0, "data": {"file_key": "file_after_grant"}}) if granted[0] else httpx.Response(400, json={"code": 99991672})
            self.assertEqual(request.url.path, "/open-apis/im/v1/messages")
            sends.append(json.loads(request.content))
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_after_grant"}})

        client, command, _task, _worker = self.prepared_share_transport(effect, file_mode=True)
        path = "/audit-report-shares/" + command["request_id"]
        first = client.post("/audit-report-shares", json=command)
        self.assertEqual(first.status_code, 200, first.text)
        failed = first.json()
        self.assertEqual([row["state"] for row in failed["deliveries"]], ["failed", "failed"])
        self.assertTrue(all(isinstance(row["reason"], str) and row["reason"].strip() for row in failed["deliveries"]))
        self.assertEqual(sends, [])
        granted[0] = True
        self.assertEqual(client.get(path).json(), failed)
        self.assertEqual(client.post("/audit-report-shares", json=command).json(), failed)
        self.assertEqual(sends, [])
        attempt = {"target": command["targets"][0], "attempt_id": str(uuid.uuid4())}
        recovered = client.post(path + "/retries", json=attempt)
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual([row["state"] for row in recovered.json()["deliveries"]], ["sent", "failed"])
        self.assertEqual(recovered.json()["request"], command)
        self.assertEqual(client.post(path + "/retries", json=attempt).json(), recovered.json())
        self.assertEqual([send["receive_id"] for send in sends], [command["targets"][0]["id"]])

    def test_report_zip_is_persistent_and_contains_the_exact_ready_pdfs_without_another_audit(self):
        from io import BytesIO
        from zipfile import ZipFile

        from worker import process_claimed

        task, worker, completed, command, package = self.prepared_report_package()
        task_path, reports, request_id = f"/audit-tasks/{task['id']}", command["reports"], command["request_id"]
        self.assertEqual((package["state"], package["receipt"]["state"]), ("pending", "accepted"))
        self.assertEqual(self.display_client.post("/audit-report-packages", json=command).json(), package)
        self.assertEqual(self.display_client.get(task_path).json()["report_package"], package)
        claimed = worker.claim_job("zip-build")
        self.assertEqual(claimed.kind, "report_zip")
        self.assertEqual(self.display_client.get(f"/audit-report-packages/{request_id}").json()["state"], "generating")
        process_claimed(worker, claimed, "zip-build")
        ready = self.display_client.get(f"/audit-report-packages/{request_id}").json()
        self.assertEqual((ready["state"], ready["retry_allowed"]), ("ready", False))
        self.assertEqual([item["state"] for item in ready["outcomes"]], ["prepared", "prepared"])
        loaded = self.display_client.get(task_path).json()
        self.assertEqual((loaded["state"], loaded["files"]), ("completed", completed["files"]))
        self.assertEqual(loaded["report_package"], ready)
        response = self.display_client.get(f"/audit-report-packages/{request_id}/zip", params={"package_id": ready["package_id"], "artifact_id": ready["artifact"]["artifact_id"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        import json

        with ZipFile(BytesIO(response.content)) as archive:
            self.assertEqual(len(archive.namelist()), 3)
            manifest = json.loads(archive.read("报告清单.txt"))
            self.assertEqual({item["reference"]["file_version_id"] for item in manifest["reports"]}, {row["file"]["id"] for row in completed["files"]})
            for item in manifest["reports"]:
                row = next(row for row in completed["files"] if row["file"]["id"] == item["reference"]["file_version_id"])
                report = row["audit"]["report"]
                pdf = self.display_client.get(
                    f"{task_path}/reports/{report['report_id']}/pdf",
                    params={"round_id": task["round_id"], "file_version_id": row["file"]["id"], "result_attempt": 1, "artifact_id": report["artifact"]["artifact_id"]},
                )
                self.assertEqual(archive.read(item["archive_name"]), pdf.content)
                self.assertEqual(item["rule"]["id"], row["audit"]["rule"]["id"])
        alias_id = str(uuid.uuid4())
        alias = self.display_client.post("/audit-report-packages", json={"request_id": alias_id, "reports": list(reversed(reports))}).json()
        self.assertEqual((alias["package_id"], alias["artifact"]), (ready["package_id"], ready["artifact"]))
        self.assertEqual(alias["reports"], list(reversed(reports)))
        self.assertEqual(self.display_client.get(f"/audit-report-packages/{alias_id}").json(), alias)
        self.assertEqual(self.display_client.post("/audit-report-packages", json={**command, "reports": list(reversed(reports))}).status_code, 409)
        self.assertIsNone(self.display_client.get(f"/audit-report-packages/{uuid.uuid4()}").json())
        download_path = f"/audit-report-packages/{request_id}/zip"
        for params in ({"package_id": str(uuid.uuid4()), "artifact_id": ready["artifact"]["artifact_id"]}, {"package_id": ready["package_id"], "artifact_id": "wrong"}):
            self.assertEqual(self.display_client.get(download_path, params=params).status_code, 403)
        for fixture in ("bob", "carol"):
            self.display_principal = self.store.principal_for_fixture(fixture)
            self.assertEqual(self.display_client.get(f"/audit-report-packages/{request_id}").status_code, 403)
            self.assertEqual(self.display_client.post("/audit-report-packages", json=command).status_code, 403)
            self.assertEqual(self.display_client.get(download_path, params={"package_id": ready["package_id"], "artifact_id": ready["artifact"]["artifact_id"]}).status_code, 403)
        self.display_principal = None
        self.assertEqual(self.display_client.get(f"/audit-report-packages/{request_id}").status_code, 401)
        self.assertIsNone(worker.claim_job("zip-build"))
        self.assertEqual(self.store.list_usage_events(self.alice), [])

    def test_failed_zip_recovers_the_same_frozen_pdfs_without_reopening_the_task(self):
        import json
        import threading
        from io import BytesIO
        from zipfile import ZipFile

        from worker import process_claimed

        task, worker, completed, command, package = self.prepared_report_package()
        old = worker.claim_job("zip-recovery-old")
        orphan = worker.build_report_package(old, "2026-10-17T01:00:00+00:00", threading.Event())
        self.assertTrue(worker.complete_job(old, "zip-recovery-old", {"error": "reply_lost"}, "failed"))
        request_id = str(uuid.uuid4())
        path = f"/audit-report-packages/{command['request_id']}/retries"
        retry = {"request_id": request_id, "package_id": package["package_id"], "expected_attempt": old.attempt}
        accepted = self.display_client.post(path, json=retry)
        self.assertEqual(accepted.status_code, 202, accepted.text)
        recovering = accepted.json()
        self.assertEqual((recovering["package_id"], recovering["state"], recovering["attempt"]), (package["package_id"], "pending", 1))
        self.assertEqual(recovering["reports"], command["reports"])
        self.assertEqual(self.display_client.post(path, json=retry).json(), recovering)
        self.assertEqual(self.display_client.get(f"/audit-report-packages/{request_id}").json(), recovering)
        current = worker.claim_job("zip-recovery-current")
        self.assertEqual((current.kind, current.id, current.attempt), ("report_zip", old.id, 2))
        self.assertEqual(current.payload, old.payload)
        self.assertFalse(worker.complete_job(old, "zip-recovery-old", orphan))
        process_claimed(worker, current, "zip-recovery-current")
        ready = self.display_client.get(f"/audit-report-packages/{request_id}").json()
        self.assertEqual((ready["state"], ready["attempt"]), ("ready", 2))
        self.assertEqual(ready["artifact"]["artifact_id"], f"{old.id}:2")
        self.assertFalse(worker.complete_job(old, "zip-recovery-old", orphan))
        response = self.display_client.get(f"/audit-report-packages/{request_id}/zip", params={"package_id": package["package_id"], "artifact_id": ready["artifact"]["artifact_id"]})
        self.assertEqual(response.status_code, 200)
        with ZipFile(BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("报告清单.txt"))["reports"]
            self.assertEqual(len(manifest), 2)
            for item in manifest:
                source = next(source for source in old.payload["sources"] if source["reference"] == item["reference"])
                self.assertEqual(archive.read(item["archive_name"]), worker.objects.get(source["object_key"]))
                self.assertEqual(item["sha256"], source["sha256"])
        observed = self.display_client.get(f"/audit-tasks/{task['id']}").json()
        self.assertEqual((observed["state"], observed["round_id"], observed["files"]), ("completed", completed["round_id"], completed["files"]))
        self.assertIsNone(worker.claim_job("no-extra-recovery"))
        self.assertEqual(self.store.list_usage_events(self.alice), [])

    def test_zip_alias_recoveries_compete_for_one_attempt_and_stale_failures_cannot_restart_it(self):
        from worker import process_claimed

        _task, worker, _completed, command, package = self.prepared_report_package()
        alias_id = str(uuid.uuid4())
        alias = self.display_client.post("/audit-report-packages", json={**command, "request_id": alias_id}).json()
        self.assertEqual(alias["package_id"], package["package_id"])
        old = worker.claim_job("zip-failed")
        self.assertTrue(worker.complete_job(old, "zip-failed", {"error": "temporary_failure"}, "failed"))
        paths = [f"/audit-report-packages/{key}/retries" for key in (command["request_id"], alias_id)]
        commands = [{"request_id": str(uuid.uuid4()), "package_id": package["package_id"], "expected_attempt": 1} for _ in paths]
        barrier = threading.Barrier(2)

        def retry(index):
            barrier.wait(timeout=5)
            return self.display_client.post(paths[index], json=commands[index])

        with ThreadPoolExecutor(2) as pool:
            replies = list(pool.map(retry, range(2)))
        self.assertEqual(sorted(reply.status_code for reply in replies), [202, 409])
        winner = next(index for index, reply in enumerate(replies) if reply.status_code == 202)
        loser = 1 - winner
        current = worker.claim_job("zip-retry-failed")
        self.assertEqual((current.id, current.attempt), (old.id, 2))
        self.assertIsNone(worker.claim_job("no-parallel-retry"))
        self.assertTrue(worker.complete_job(current, "zip-retry-failed", {"error": "temporary_failure"}, "failed"))
        self.assertEqual(self.display_client.post(paths[loser], json=commands[loser]).status_code, 409)
        self.assertEqual(self.display_client.post(paths[winner], json=commands[winner]).json()["state"], "failed")
        self.assertEqual(self.display_client.post(paths[winner], json={**commands[winner], "expected_attempt": 2}).status_code, 409)
        self.assertIsNone(worker.claim_job("stale-page-cannot-retry"))
        fresh = {**commands[loser], "request_id": str(uuid.uuid4()), "expected_attempt": 2}
        self.assertEqual(self.display_client.post(paths[loser], json=fresh).status_code, 202)
        last = worker.claim_job("zip-fresh-retry")
        self.assertEqual((last.id, last.attempt), (old.id, 3))
        process_claimed(worker, last, "zip-fresh-retry")
        self.assertEqual(self.display_client.get(f"/audit-report-packages/{fresh['request_id']}").json()["state"], "ready")
        self.assertEqual(self.store.list_usage_events(self.alice), [])

    def test_zip_recovery_rechecks_owner_and_quota_only_for_new_requests(self):
        task, worker, _completed, command, package = self.prepared_report_package()
        old = worker.claim_job("zip-denied-failed")
        self.assertTrue(worker.complete_job(old, "zip-denied-failed", {"error": "temporary_failure"}, "failed"))
        path = f"/audit-report-packages/{command['request_id']}/retries"
        retry = {"request_id": str(uuid.uuid4()), "package_id": package["package_id"], "expected_attempt": 1}
        for actor in ("bob", "carol", None):
            self.display_principal = self.store.principal_for_fixture(actor) if actor else None
            self.assertEqual(self.display_client.post(path, json=retry).status_code, 403 if actor else 401)
        self.display_principal = self.alice
        self.settings.usage_charge_enabled = True
        denied = self.display_client.post(path, json=retry)
        self.assertEqual((denied.status_code, denied.json()), (409, {"error": "usage_quota_unavailable"}))
        self.assertTrue(self.display_client.get(f"/audit-report-packages/{command['request_id']}").json()["retry_allowed"])
        self.assertIsNone(self.display_client.get(f"/audit-report-packages/{retry['request_id']}").json())
        self.assertIsNone(worker.claim_job("no-charged-retry"))
        self.settings.usage_charge_enabled = False
        self.assertEqual(self.display_client.post(path, json=retry).status_code, 202)
        self.settings.usage_charge_enabled = True
        self.assertEqual(self.display_client.post(path, json=retry).status_code, 202)
        self.assertEqual(self.display_client.get(f"/audit-report-packages/{retry['request_id']}").json()["receipt"]["state"], "accepted")
        self.schema.require(
            self.schema.psql(
                self.schema.migrator_user(),
                self.schema.migrator_password(),
                self.database,
                "-c",
                f"DELETE FROM product.resource_grants WHERE resource_id = '{task['round_id']}' AND user_id = '{self.alice.user_id}'",
            ),
            "revoke isolated round access",
        )
        self.assertEqual(self.display_client.post(path, json=retry).status_code, 403)
        self.assertEqual(self.display_client.get(f"/audit-report-packages/{retry['request_id']}").status_code, 403)
        self.assertIsNotNone(worker.claim_job("accepted-before-revocation"))
        self.assertIsNone(worker.claim_job("no-replay-after-revocation"))
        self.assertEqual(self.store.list_usage_events(self.alice), [])

    def test_zip_recovery_cannot_rebind_the_package_or_silently_replace_a_frozen_pdf(self):
        from product.files import ObjectMissing
        from worker import process_claimed

        _task, worker, _completed, command, package = self.prepared_report_package()
        old = worker.claim_job("zip-original-failed")
        self.assertTrue(worker.complete_job(old, "zip-original-failed", {"error": "temporary_failure"}, "failed"))
        path = f"/audit-report-packages/{command['request_id']}/retries"
        retry = {"request_id": str(uuid.uuid4()), "package_id": package["package_id"], "expected_attempt": 1}
        self.assertEqual(self.display_client.post(path, json={**retry, "package_id": str(uuid.uuid4())}).status_code, 403)
        self.assertEqual(self.display_client.post(path, json={**retry, "expected_attempt": True}).status_code, 400)
        self.assertEqual(self.display_client.post(path, json={**retry, "reports": command["reports"]}).status_code, 400)
        source = old.payload["sources"][0]
        worker.objects.delete(source["object_key"])
        self.assertEqual(self.display_client.post(path, json=retry).status_code, 202)
        current = worker.claim_job("zip-missing-frozen-pdf")
        self.assertEqual(current.payload, old.payload)
        process_claimed(worker, current, "zip-missing-frozen-pdf")
        failed = self.display_client.get(f"/audit-report-packages/{retry['request_id']}").json()
        self.assertEqual((failed["state"], failed["artifact"]), ("failed", None))
        self.assertEqual(next(row for row in failed["outcomes"] if row["reference"] == source["reference"])["state"], "failed")
        with self.assertRaises(ObjectMissing):
            worker.objects.get(source["object_key"])
        self.assertIsNone(worker.claim_job("no-pdf-regeneration"))

    def test_lost_zip_write_reply_and_a_new_request_cannot_rebuild_the_failed_collection(self):
        from worker import process_claimed

        task, worker, completed, command, package = self.prepared_report_package()
        objects = worker.objects
        written = []

        class LostReply:
            def get(self, key):
                return objects.get(key)

            def put_exclusive(self, key, body, content_type):
                objects.put_exclusive(key, body, content_type)
                if content_type == "application/zip":
                    written.append(key)
                    raise TimeoutError("write succeeded; reply lost")

        worker.objects = LostReply()
        process_claimed(worker, worker.claim_job("lost-zip"), "lost-zip")
        request_id = command["request_id"]
        failed = self.display_client.get(f"/audit-report-packages/{request_id}").json()
        self.assertEqual((failed["state"], failed["artifact"], failed["retry_allowed"]), ("failed", None, True))
        self.assertEqual([item["state"] for item in failed["outcomes"]], ["prepared", "prepared"])
        self.assertEqual(len(written), 1)
        self.assertTrue(objects.get(written[0]).startswith(b"PK"))
        self.assertEqual(self.display_client.get(f"/audit-tasks/{task['id']}").json()["files"], completed["files"])
        alias = self.display_client.post("/audit-report-packages", json={**command, "request_id": str(uuid.uuid4()), "reports": list(reversed(command["reports"]))}).json()
        self.assertEqual((alias["package_id"], alias["state"], alias["artifact"]), (package["package_id"], "failed", None))
        self.assertIsNone(worker.claim_job("no-rebuild"))
        self.assertEqual(self.display_client.get(f"/audit-report-packages/{request_id}/zip", params={"package_id": package["package_id"], "artifact_id": "unpublished"}).status_code, 409)
        self.assertEqual(self.store.list_usage_events(self.alice), [])

    def test_one_unreadable_pdf_fails_only_its_package_preparation_and_does_not_publish_a_partial_zip(self):
        from product.files import ObjectMissing
        from worker import process_claimed

        task, worker, completed, command, _package = self.prepared_report_package()
        objects = worker.objects

        class MissingFirstPdf:
            missing = True

            def get(self, key):
                if self.missing and key.endswith(".pdf"):
                    self.missing = False
                    raise ObjectMissing()
                return objects.get(key)

            def put_exclusive(self, key, body, content_type):
                objects.put_exclusive(key, body, content_type)

        worker.objects = MissingFirstPdf()
        process_claimed(worker, worker.claim_job("zip-source-error"), "zip-source-error")
        package = self.display_client.get(f"/audit-report-packages/{command['request_id']}").json()
        self.assertEqual((package["state"], package["artifact"]), ("failed", None))
        self.assertEqual(sorted(item["state"] for item in package["outcomes"]), ["failed", "prepared"])
        self.assertTrue(next(item for item in package["outcomes"] if item["state"] == "failed")["reason"])
        self.assertEqual(self.display_client.get(f"/audit-tasks/{task['id']}").json()["files"], completed["files"])

    def test_temporary_zip_write_failure_does_not_claim_that_valid_pdfs_are_unreadable(self):
        from io import BytesIO
        from unittest.mock import patch

        from worker import process_claimed

        task, worker, completed, command, _package = self.prepared_report_package()

        class FailedTemporaryFile(BytesIO):
            failed = False

            def write(self, data):
                if not self.failed:
                    self.failed = True
                    raise OSError("temporary disk full")
                return super().write(data)

        with patch("tempfile.SpooledTemporaryFile", return_value=FailedTemporaryFile()):
            process_claimed(worker, worker.claim_job("zip-disk-error"), "zip-disk-error")
        package = self.display_client.get(f"/audit-report-packages/{command['request_id']}").json()
        self.assertEqual((package["state"], package["artifact"]), ("failed", None))
        self.assertTrue(all(item["state"] != "failed" for item in package["outcomes"]))
        self.assertEqual(self.display_client.get(f"/audit-tasks/{task['id']}").json()["files"], completed["files"])

    def test_concurrent_zip_requests_share_one_frozen_work_and_old_generation_cannot_publish(self):
        import threading

        from product.db import connect
        from worker import process_claimed

        task, worker, _completed, command, _package = self.prepared_report_package(accept=False)
        barrier = threading.Barrier(2)

        def accept_alias(_number):
            barrier.wait(timeout=5)
            return self.store.accept_report_package(self.alice, {**command, "request_id": str(uuid.uuid4())})

        with ThreadPoolExecutor(2) as pool:
            aliases = list(pool.map(accept_alias, range(2)))
        package = aliases[0]
        command["request_id"] = package["receipt"]["request_id"]
        self.assertEqual({row["package_id"] for row in aliases}, {package["package_id"]})
        old = worker.claim_job("zip-old")
        with connect(self.settings, "worker") as conn:
            conn.execute("UPDATE engine.jobs SET lease_until = now() - interval '1 second' WHERE id = %s", (old.id,))
        current = worker.claim_job("zip-current")
        self.assertEqual((current.id, current.attempt), (old.id, 2))
        old_result = worker.build_report_package(old, "2026-10-17T01:00:00+00:00", threading.Event())
        process_claimed(worker, current, "zip-current")
        before = self.display_client.get(f"/audit-tasks/{task['id']}").json()
        self.assertEqual(before["report_package"]["artifact"]["artifact_id"], f"{current.id}:2")
        self.assertFalse(worker.complete_job(old, "zip-old", old_result))
        self.assertEqual(self.display_client.get(f"/audit-tasks/{task['id']}").json(), before)
        denied = self.display_client.get(f"/audit-report-packages/{command['request_id']}/zip", params={"package_id": package["package_id"], "artifact_id": old_result["artifact"]["artifact_id"]})
        self.assertEqual(denied.status_code, 403)
        self.assertIsNone(worker.claim_job("no-duplicate-zip"))

    def test_failed_pdf_can_be_explicitly_regenerated_without_reopening_or_reauditing_the_task(self):
        from worker import process_claimed

        version, task, worker, _job, _result = self.prepared_query_task()
        path = f"/audit-tasks/{task['id']}"
        first = worker.claim_job("pdf-first")
        self.assertTrue(worker.complete_job(first, "pdf-first", {"error": "report_pdf_failed"}, "failed"))
        failed = self.display_client.get(path).json()
        original = self.display_client.get(f"/rounds/{task['round_id']}").json()
        reference = {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": version.id, "result_attempt": 1, "report_id": first.id}
        command = {"request_id": str(uuid.uuid4()), "reference": reference}
        retry_path = path + f"/reports/{first.id}/regenerations"
        response = self.display_client.post(retry_path, json=command)
        self.assertEqual(response.status_code, 201, response.text)
        accepted = response.json()
        self.assertEqual(accepted["reference"], reference)
        self.assertEqual(accepted["receipt"], {"request_id": command["request_id"], "state": "accepted", "reason": None})
        self.assertEqual(accepted["report"]["state"], "pending")
        self.assertEqual(self.display_client.post(retry_path, json=command).json(), accepted)
        loaded = self.display_client.get(path).json()
        self.assertEqual((failed["state"], loaded["state"]), ("completed", "completed"))
        self.assertEqual(loaded["files"][0]["audit"]["report"]["generation_request"], accepted["receipt"])
        self.assertEqual([row["id"] for row in self.store.list_audit_tasks(self.alice, state="completed")], [task["id"]])
        self.assertEqual(self.store.list_audit_tasks(self.alice, state="reviewing"), [])
        retry = worker.claim_job("pdf-recovery")
        self.assertEqual((retry.id, retry.kind, retry.attempt), (first.id, "report_pdf", 2))
        self.assertEqual(retry.payload["report_input"], first.payload["report_input"])
        self.assertIsNone(worker.claim_job("no-extra-work"))
        process_claimed(worker, retry, "pdf-recovery")
        report = self.display_client.get(path).json()["files"][0]["audit"]["report"]
        self.assertEqual((report["state"], report["report_id"], report["artifact"]["artifact_id"]), ("ready", first.id, f"{first.id}:2"))
        query = {key: reference[key] for key in ("round_id", "file_version_id", "result_attempt")}
        observed = self.display_client.get(retry_path + "/" + command["request_id"], params=query)
        self.assertEqual(observed.status_code, 200)
        self.assertEqual(observed.json()["report"], report)
        pdf = self.display_client.get(path + f"/reports/{first.id}/pdf", params={**query, "artifact_id": report["artifact"]["artifact_id"]})
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF-"))
        self.assertEqual(self.display_client.get(f"/rounds/{task['round_id']}").json(), original)
        self.assertEqual(self.store.list_usage_events(self.alice), [])

    def test_concurrent_pdf_recovery_and_late_attempt_cannot_schedule_or_publish_twice(self):
        from worker import process_claimed

        version, task, worker, _job, _result = self.prepared_query_task()
        first = worker.claim_job("pdf-failed")
        self.assertTrue(worker.complete_job(first, "pdf-failed", {"error": "report_pdf_failed"}, "failed"))
        reference = {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": version.id, "result_attempt": 1, "report_id": first.id}
        path = f"/audit-tasks/{task['id']}"
        retry_path = path + f"/reports/{first.id}/regenerations"
        commands = [{"request_id": str(uuid.uuid4()), "reference": reference} for _ in range(2)]
        with ThreadPoolExecutor(2) as pool:
            replies = list(pool.map(lambda command: self.display_client.post(retry_path, json=command), commands))
        self.assertEqual(sorted(reply.status_code for reply in replies), [201, 409])
        current = worker.claim_job("pdf-current")
        self.assertEqual((current.id, current.kind, current.attempt), (first.id, "report_pdf", 2))
        self.assertIsNone(worker.claim_job("no-second-recovery"))
        late = worker.write_audit_report(first, b"%PDF-1.7\nold private attempt", "2026-10-17T01:00:00+00:00")
        before = self.display_client.get(path).json()
        self.assertFalse(worker.complete_job(first, "pdf-failed", late))
        self.assertEqual(self.display_client.get(path).json(), before)
        process_claimed(worker, current, "pdf-current")
        ready = self.display_client.get(path).json()
        self.assertEqual((ready["state"], ready["files"][0]["audit"]["report"]["artifact"]["artifact_id"]), ("completed", f"{first.id}:2"))
        self.assertFalse(worker.complete_job(first, "pdf-failed", late))
        self.assertEqual(self.display_client.get(path).json(), ready)
        self.assertEqual(self.store.list_usage_events(self.alice), [])

    def test_pdf_recovery_requires_owner_and_live_quota_for_new_requests(self):
        version, task, worker, _job, _result = self.prepared_query_task()
        first = worker.claim_job("pdf-failed")
        self.assertTrue(worker.complete_job(first, "pdf-failed", {"error": "report_pdf_failed"}, "failed"))
        reference = {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": version.id, "result_attempt": 1, "report_id": first.id}
        command = {"request_id": str(uuid.uuid4()), "reference": reference}
        path = f"/audit-tasks/{task['id']}/reports/{first.id}/regenerations"
        query = {key: reference[key] for key in ("round_id", "file_version_id", "result_attempt")}
        for actor in ("bob", "carol", None):
            self.display_principal = self.store.principal_for_fixture(actor) if actor else None
            expected = 403 if actor else 401
            self.assertEqual(self.display_client.post(path, json=command).status_code, expected)
            self.assertEqual(self.display_client.get(path + "/" + command["request_id"], params=query).status_code, expected)
        self.display_principal = self.alice
        self.settings.usage_charge_enabled = True
        denied = self.display_client.post(path, json=command)
        self.assertEqual((denied.status_code, denied.json()), (409, {"error": "usage_quota_unavailable"}))
        self.assertTrue(self.display_client.get(f"/audit-tasks/{task['id']}").json()["files"][0]["audit"]["report"]["regenerate_allowed"])
        self.assertIsNone(self.display_client.get(path + "/" + command["request_id"], params=query).json())
        self.assertIsNone(worker.claim_job("no-denied-work"))
        self.settings.usage_charge_enabled = False
        self.assertEqual(self.display_client.post(path, json=command).status_code, 201)
        self.settings.usage_charge_enabled = True
        replay = self.display_client.post(path, json=command)
        self.assertEqual((replay.status_code, replay.json()["receipt"]["state"]), (201, "accepted"))
        self.assertIsNotNone(worker.claim_job("only-accepted-work"))
        self.assertIsNone(worker.claim_job("no-replay-work"))
        self.assertEqual(self.store.list_usage_events(self.alice), [])

    def test_pdf_recovery_request_cannot_be_rebound_to_another_frozen_report(self):
        _version, task, worker, _job, _result = self.prepared_query_task(documents=(("first.xlsx", "P-1"), ("second.xlsx", "P-2")))
        for _ in range(2):
            first = worker.claim_job("pdf-failed")
            self.assertTrue(worker.complete_job(first, "pdf-failed", {"error": "report_pdf_failed"}, "failed"))
        loaded = self.display_client.get(f"/audit-tasks/{task['id']}").json()
        references = [
            {"task_id": task["id"], "round_id": task["round_id"], "file_version_id": row["file"]["id"], "result_attempt": 1, "report_id": row["audit"]["report"]["report_id"]}
            for row in loaded["files"]
        ]
        key = str(uuid.uuid4())
        for index, reference in enumerate(references):
            path = f"/audit-tasks/{task['id']}/reports/{reference['report_id']}/regenerations"
            response = self.display_client.post(path, json={"request_id": key, "reference": reference})
            self.assertEqual(response.status_code, 201 if index == 0 else 409, response.text)
        wrong = {**references[0], "result_attempt": 2}
        self.assertEqual(self.display_client.post(f"/audit-tasks/{task['id']}/reports/{wrong['report_id']}/regenerations", json={"request_id": str(uuid.uuid4()), "reference": wrong}).status_code, 403)
        self.assertIsNotNone(worker.claim_job("first-only"))
        self.assertIsNone(worker.claim_job("no-rebound-work"))

    def test_lost_object_write_reply_retains_a_private_object_but_never_publishes_a_report(self):
        from unittest.mock import patch

        from worker import process_claimed

        _version, task, worker, _job, _result = self.prepared_query_task()
        original = self.display_client.get(f"/rounds/{task['round_id']}").json()
        claimed = worker.claim_job("pdf-lost-reply")
        put = worker.objects.put_exclusive

        def lost_reply(key, body, content_type):
            put(key, body, content_type)
            raise TimeoutError("object write reply lost")

        with patch.object(worker.objects, "put_exclusive", side_effect=lost_reply):
            process_claimed(worker, claimed, "pdf-lost-reply")
        observed = self.display_client.get(f"/audit-tasks/{task['id']}").json()
        report = observed["files"][0]["audit"]["report"]
        self.assertEqual((observed["state"], report["state"], report["regenerate_allowed"], report["artifact"]), ("completed", "failed", True, None))
        self.assertTrue(worker.objects.get(f"reports/{claimed.tenant_id}/{claimed.id}/{claimed.attempt}.pdf").startswith(b"%PDF-"))
        self.assertEqual(self.display_client.get(f"/rounds/{task['round_id']}").json(), original)
        self.assertIsNone(worker.claim_job("not-retried"))

    def test_a_late_pdf_attempt_cannot_replace_the_published_artifact(self):
        from documents.audit_pdf import render_audit_pdf
        from product.db import connect
        from worker import process_claimed

        version, task, worker, _job, _result = self.prepared_query_task()
        old = worker.claim_job("pdf-old")
        with connect(self.settings, "worker") as conn:
            conn.execute("UPDATE engine.jobs SET lease_until = now() - interval '1 second' WHERE id = %s", (old.id,))
        current = worker.claim_job("pdf-current")
        self.assertEqual((current.id, current.attempt), (old.id, 2))
        generated_at = "2026-10-17T01:00:00+00:00"
        old_result = worker.write_audit_report(old, render_audit_pdf({**old.payload["report_input"], "generated_at": generated_at}), generated_at)
        process_claimed(worker, current, "pdf-current")
        before = self.display_client.get(f"/audit-tasks/{task['id']}").json()
        self.assertEqual(before["files"][0]["audit"]["report"]["artifact"]["artifact_id"], f"{current.id}:2")
        self.assertFalse(worker.complete_job(old, "pdf-old", old_result))
        self.assertEqual(self.display_client.get(f"/audit-tasks/{task['id']}").json(), before)
        denied = self.display_client.get(
            f"/audit-tasks/{task['id']}/reports/{current.id}/pdf",
            params={"round_id": task["round_id"], "file_version_id": version.id, "result_attempt": 1, "artifact_id": old_result["artifact"]["artifact_id"]},
        )
        self.assertEqual(denied.status_code, 403)


class UsageAcceptanceTests(AuditDatabaseCase):
    def setUp(self):
        super().setUp()
        self.settings.usage_charge_enabled = True
        self.settings.feishu_ai_item_id = "test-item"
        self.settings.feishu_free_trial_quota_points = 700

    def accepted(self, draft, key=None):
        from product.feishu_usage import PartnerAiItem

        return self.store.accept_draft(self.alice, draft, key or draft, usage_policy=lambda actor: PartnerAiItem(True, 1, True))

    def prepared(self):
        from product.fixtures import RULE_A

        target, reference = self.upload("target.xlsx"), self.upload("reference.pdf")
        return self.draft(
            [
                {"file_version_id": target.id, "role": "target", "rule_version_id": RULE_A},
                {"file_version_id": reference.id, "role": "reference"},
            ]
        )

    def test_usage_page_groups_file_allocations_and_preserves_tenant_ledger_without_granting_history(self):
        from api.audit_routes import register_audit_routes
        from api.web_contract import AuditUsagePage
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from product.db import connect
        from product.fixtures import RULE_A
        from pydantic import TypeAdapter

        first, second = self.upload("费用核对.xlsx"), self.upload("另一份.pdf")
        draft = self.draft([{"file_version_id": version.id, "role": "target", "rule_version_id": RULE_A} for version in (first, second)])
        accepted = self.accepted(draft)
        before = self.store.list_usage_events(self.alice)
        principal = [None]
        app = FastAPI()
        register_audit_routes(app, self.store, lambda request: principal[0], Mock())
        client = TestClient(app)
        self.assertEqual(client.get("/audit-usage").status_code, 401)
        principal[0] = self.alice
        response = client.get("/audit-usage?range=all")
        self.assertEqual(response.status_code, 200)
        page = response.json()
        TypeAdapter(AuditUsagePage).validate_python(page)
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["summary"]["consumption_count"]["value"], 2)
        self.assertEqual(page["summary"]["free_remaining"]["value"], 0)
        self.assertEqual(page["summary"]["free_total"]["value"], 700)
        self.assertEqual(page["summary"]["charged_points"]["value"], 700)
        self.assertEqual(page["summary"]["pending_points"]["value"], 300)
        entries = {row["filename"]: row for row in page["entries"]}
        self.assertEqual(entries[first.original_filename]["allocation"], {"free_points": 500, "paid_points": 0, "reason": None})
        mixed = entries[second.original_filename]
        self.assertEqual(mixed["allocation"], {"free_points": 200, "paid_points": 300, "reason": None})
        self.assertEqual(mixed["settlement"]["state"], "pending")
        self.assertEqual(mixed["reference"], {"task_id": draft, "round_id": accepted["id"], "file_version_id": second.id, "result_attempt": None})
        self.assertEqual(entries[first.original_filename]["settlement"]["state"], "trial")
        self.assertTrue(all(row["points"] == 500 and row["audit_access"]["state"] != "allowed" for row in page["entries"]))
        self.assertEqual(client.get("/audit-usage?range=all&search=费用核对").json()["total"], 1)
        self.assertEqual(client.get("/audit-usage?range=all&page=2").json()["entries"], [])
        self.assertEqual(client.get("/audit-usage?range=invalid").status_code, 400)
        self.assertEqual(client.get("/audit-usage?page=0").status_code, 422)
        principal[0] = self.store.principal_for_fixture("bob")
        teammate = client.get("/audit-usage?range=all").json()
        self.assertEqual(teammate["entries"], page["entries"])
        self.assertEqual(client.get("/audit-usage?search=alice").json()["total"], 2)
        principal[0] = self.store.principal_for_fixture("carol")
        self.assertEqual(client.get("/audit-usage?range=all").json()["entries"], [])
        self.assertEqual(self.store.list_usage_events(self.alice), before)

        pending = next(row for row in before if not row["is_free_trial"])
        claim = self.store.claim_usage_report(self.alice, pending["id"])
        principal[0] = self.alice
        processing = client.get("/audit-usage?range=all").json()
        self.assertEqual(next(row for row in processing["entries"] if row["usage_id"] == mixed["usage_id"])["settlement"]["state"], "processing")
        with connect(self.settings, "migrator") as conn:
            conn.execute("UPDATE product.usage_events SET reporting_at = now() - INTERVAL '3 minutes' WHERE id = %s", (pending["id"],))
        stale = client.get("/audit-usage?range=all").json()
        self.assertEqual(next(row for row in stale["entries"] if row["usage_id"] == mixed["usage_id"])["settlement"]["state"], "unknown")
        self.assertIsNone(self.store.claim_usage_report(self.alice, pending["id"]))
        self.store.settle_usage_external(self.alice, pending["id"], claim, "confirmed", "provider-usage-receipt")
        principal[0] = self.alice
        readback = client.get("/audit-usage?range=all").json()
        updated = next(row for row in readback["entries"] if row["filename"] == second.original_filename)
        self.assertEqual(updated["usage_id"], mixed["usage_id"])
        self.assertEqual(updated["orders"], [])
        self.assertEqual(updated["settlement"]["state"], "unknown")
        self.assertIn("扣费", updated["settlement"]["reason"])

    def test_cross_month_debit_preserves_acceptance_cohort_and_unknown_report_receipt(self):
        from datetime import datetime, timezone
        from unittest.mock import patch

        import psycopg
        from api.audit_routes import register_audit_routes
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from product.db import connect

        self.settings.feishu_free_trial_quota_points = 0
        draft = self.prepared()
        accepted = self.accepted(draft)
        event = self.store.list_usage_events(self.alice)[0]
        with connect(self.settings, "migrator") as conn:
            conn.execute("UPDATE product.audit_rounds SET created_at = '2026-10-28T02:00:00Z' WHERE id = %s", (accepted["id"],))
            conn.execute("UPDATE product.usage_events SET created_at = '2026-11-01T01:00:00Z' WHERE id = %s", (event["id"],))
        app = FastAPI()
        register_audit_routes(app, self.store, lambda request: self.alice, Mock())
        client = TestClient(app)
        observed = datetime(2026, 11, 4, tzinfo=timezone.utc)
        with patch("product.store.store._now", return_value=observed):
            before = client.get("/audit-usage?range=all").json()
            claim = self.store.claim_usage_report(self.alice, event["id"])
            self.assertTrue(self.store.settle_usage_external(self.alice, event["id"], claim, "confirmed", "usage-report-ack"))
            reported = client.get("/audit-usage?range=all").json()
            self.assertEqual(reported["entries"][0]["settlement"]["state"], "unknown")
            self.assertIsNone(reported["entries"][0]["debited_at"])
            # Verified provider data arranged in a scratch DB, not a real charge.
            with connect(self.settings, "migrator") as conn:
                for order, time in (("order", None), (None, "2026-11-03T03:00:00Z"), (" ", "2026-11-03T03:00:00Z")):
                    with self.assertRaises(psycopg.errors.CheckViolation), conn.transaction():
                        conn.execute("UPDATE product.usage_events SET debit_order_id = %s, debited_at = %s WHERE id = %s", (order, time, event["id"]))
                conn.execute("UPDATE product.usage_events SET debit_order_id = 'verified-order', debited_at = '2026-11-03T03:00:00Z' WHERE id = %s", (event["id"],))
            ledger = self.store.list_usage_events(self.alice)
            settled = client.get("/audit-usage?range=all").json()
            self.assertEqual(client.get("/audit-usage?range=all").json(), settled)
            self.assertEqual(self.store.list_usage_events(self.alice), ledger)
        for page in (before, reported, settled):
            self.assertEqual(page["total"], 1)
            self.assertEqual(page["summary"]["consumption_count"]["value"], 0)
            self.assertEqual(page["summary"]["charged_points"]["value"], 0)
            self.assertEqual(page["summary"]["pending_points"]["value"], 0)
            self.assertEqual(page["entries"][0]["usage_id"], before["entries"][0]["usage_id"])
            self.assertEqual(page["entries"][0]["occurred_at"], "2026-10-28T02:00:00+00:00")
        self.assertEqual(settled["entries"][0]["settlement"]["state"], "charged")
        self.assertEqual(settled["entries"][0]["debited_at"], "2026-11-03T03:00:00+00:00")
        self.assertEqual(settled["entries"][0]["orders"], [{"order_id": "verified-order", "confirmed_at": "2026-11-03T03:00:00+00:00"}])
        self.assertNotEqual(ledger[0]["external_confirmed_at"], settled["entries"][0]["debited_at"])

    def test_verified_debit_cannot_be_claimed_for_another_usage_report(self):
        from product.db import connect

        self.settings.feishu_free_trial_quota_points = 0
        draft = self.prepared()
        self.accepted(draft)
        event = self.store.list_usage_events(self.alice)[0]
        with connect(self.settings, "migrator") as conn:
            conn.execute("UPDATE product.usage_events SET debit_order_id = 'verified-order', debited_at = now() WHERE id = %s", (event["id"],))
        self.assertIsNone(self.store.claim_usage_report(self.alice, event["id"]))

    def test_late_unknown_usage_report_cannot_erase_independent_debit_evidence(self):
        from product.db import connect

        self.settings.feishu_free_trial_quota_points = 0
        draft = self.prepared()
        self.accepted(draft)
        event = self.store.list_usage_events(self.alice)[0]
        claim = self.store.claim_usage_report(self.alice, event["id"])
        with connect(self.settings, "migrator") as conn:
            conn.execute("UPDATE product.usage_events SET debit_order_id = 'verified-order', debited_at = now() WHERE id = %s", (event["id"],))
        self.store.settle_usage_external(self.alice, event["id"], claim, "unknown")
        page = self.store.list_audit_usage(self.alice, {"range": "all", "search": "", "page": 1, "page_size": 10})
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["entries"][0]["settlement"]["state"], "charged")
        self.assertEqual(page["entries"][0]["orders"][0]["order_id"], "verified-order")
        self.assertEqual(page["summary"]["consumption_count"]["value"], 1)
        self.assertEqual(page["summary"]["charged_points"]["value"], 500)
        self.assertEqual(page["summary"]["pending_points"]["value"], 0)

    def test_usage_page_keeps_legacy_aggregate_visible_without_fabricating_file_charges(self):
        from api.audit_routes import register_audit_routes
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from product.db import connect

        with connect(self.settings, "migrator") as conn:
            conn.execute(
                """INSERT INTO product.usage_events (id, tenant_id, user_id, ai_item_id, consumed_points, is_free_trial, external_status)
                VALUES (%s, %s, %s, 'test-item', 1200, false, 'unknown')""",
                (str(uuid.uuid4()), self.alice.tenant_id, self.alice.user_id),
            )
        app = FastAPI()
        register_audit_routes(app, self.store, lambda request: self.alice, Mock())
        with TestClient(app) as client:
            page = client.get("/audit-usage?range=all").json()
        self.assertEqual(page["entries"], [])
        self.assertEqual(page["unattributed"]["points"], 1200)
        self.assertEqual(page["unattributed"]["slice_count"], 1)
        self.assertTrue(page["unattributed"]["reason"])
        self.assertIsNone(page["summary"]["consumption_count"]["value"])
        self.assertEqual(self.store.list_usage_events(self.alice)[0]["consumed_points"], 1200)

    def test_each_target_has_its_own_usage_slices(self):
        from product.fixtures import RULE_A

        first, second = self.upload("first.xlsx"), self.upload("second.xlsx")
        draft = self.draft(
            [
                {"file_version_id": first.id, "role": "target", "rule_version_id": RULE_A},
                {"file_version_id": second.id, "role": "target", "rule_version_id": RULE_A},
            ]
        )
        accepted = self.accepted(draft)
        self.accepted(draft)
        events = self.store.list_usage_events(self.alice)
        self.assertEqual({row.get("file_version_id") for row in events}, {first.id, second.id})
        for version in (first.id, second.id):
            self.assertEqual(sum(row["consumed_points"] for row in events if row["file_version_id"] == version), 500)
        self.assertEqual(sum(row["consumed_points"] for row in events if row["is_free_trial"]), 700)
        self.assertEqual({row["round_id"] for row in events}, {accepted["id"]})
        self.schema.require(self.schema.apply(self.database), "reapply migrations with file usage")
        self.assertEqual(self.store.list_usage_events(self.alice), events)

    def test_unknown_usage_cannot_be_claimed_for_another_send(self):
        from datetime import datetime, timedelta, timezone

        import httpx
        from api import create_app
        from fastapi.testclient import TestClient
        from product.feishu import UserTokens
        from product.identity import SESSION_COOKIE

        self.settings.feishu_free_trial_quota_points = 0
        draft = self.prepared()
        self.accepted(draft)
        event = self.store.list_usage_events(self.alice)[0]
        claim = self.store.claim_usage_report(self.alice, event["id"])
        self.assertIsNotNone(claim)
        self.store.settle_usage_external(self.alice, event["id"], claim, "unknown")
        self.assertIsNone(self.store.claim_usage_report(self.alice, event["id"]))
        current = self.store.usage_event(self.alice, event["id"])
        self.assertEqual(current["external_status"], "unknown")
        self.assertIsNone(current["external_record_id"])
        self.assertIsNone(current["external_confirmed_at"])

        def no_external_request(request):
            self.fail("Unknown usage must not trigger an external request")

        now = datetime.now(timezone.utc)
        session = self.store.create_session(self.alice, UserTokens("test-access", "test-refresh", now + timedelta(hours=1), now + timedelta(days=1)))
        with httpx.Client(transport=httpx.MockTransport(no_external_request)) as http:
            app = create_app(self.settings, lambda _: {"state": "ok"}, lambda _: {"state": "ok"}, self.store, http)
            with TestClient(app) as client:
                client.cookies.set(SESSION_COOKIE, session)
                response = client.post(f"/integrations/feishu/usage/events/{event['id']}/report")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["external_status"], "unknown")

    def test_concurrent_rounds_share_trial_and_replay_does_not_charge(self):
        drafts = [self.prepared(), self.prepared()]
        with ThreadPoolExecutor(2) as pool:
            rounds = list(pool.map(self.accepted, drafts))
        self.accepted(drafts[0])
        events = self.store.list_usage_events(self.alice)
        self.assertEqual(sum(e["consumed_points"] for e in events), 1000)
        self.assertTrue(all(e["external_confirmed_at"] is None for e in events))
        self.assertEqual(sum(e["consumed_points"] for e in events if e["is_free_trial"]), 700)
        self.assertEqual({e["round_id"] for e in events}, {r["id"] for r in rounds})
        pending = next(e for e in events if not e["is_free_trial"])
        with ThreadPoolExecutor(2) as pool:
            claims = list(pool.map(lambda _: self.store.claim_usage_report(self.alice, pending["id"]), range(2)))
        self.assertEqual(sum(c is not None for c in claims), 1)
        with self.store._conn() as conn:
            conn.execute("UPDATE product.usage_events SET reporting_at = now() - INTERVAL '3 minutes' WHERE id = %s", (pending["id"],))
        self.assertEqual(self.store.usage_event_public(self.store.usage_event(self.alice, pending["id"]))["external_status"], "unknown")
        self.assertIsNone(self.store.claim_usage_report(self.alice, pending["id"]))
        late = self.store.settle_usage_external(self.alice, pending["id"], next(c for c in claims if c), "confirmed", "test-record")
        self.assertEqual(late["external_status"], "confirmed")
        public = self.store.usage_event_public(late)
        self.assertIsNotNone(public.get("external_confirmed_at"))
        self.assertGreaterEqual(public["external_confirmed_at"], public["created_at"])
        self.assertEqual(public["external_record_id"], "test-record")
        self.assertIsNone(self.store.claim_usage_report(self.alice, pending["id"]))
        self.assertEqual(self.store.usage_event_public(self.store.usage_event(self.alice, pending["id"])), public)
        bob = self.store.principal_for_fixture("bob")
        self.assertEqual(str(self.store.usage_event(bob, pending["id"])["id"]), pending["id"])
        self.assertIsNone(self.store.claim_usage_report(bob, pending["id"]))
        carol = self.store.principal_for_fixture("carol")
        self.assertIsNone(self.store.usage_event(carol, pending["id"]))

    def test_usage_error_rolls_back_round_and_job(self):
        from unittest.mock import patch

        draft = self.prepared()
        with patch.object(self.store, "_record_round_usage", side_effect=RuntimeError("test rollback")):
            with self.assertRaises(RuntimeError):
                self.accepted(draft, "rollback")
        with self.store._conn() as conn:
            for table in ("product.audit_rounds", "engine.jobs", "product.usage_events"):
                self.assertEqual(conn.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)
        self.assertEqual(self.store.get_draft(draft)["status"], "open")


if __name__ == "__main__":
    unittest.main()
