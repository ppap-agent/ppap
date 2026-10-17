"""Per-target rules and acceptance concurrency against actual PostgreSQL."""

import os
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest


class AuditDatabaseCase(unittest.TestCase):
    previous_schema = False

    @pytest.fixture(autouse=True)
    def _schema_support(self, schema_tools, memory_objects_type, sample_rule_body_factory):
        self.schema = schema_tools
        self.memory_objects_type = memory_objects_type
        self.rule_body = sample_rule_body_factory

    def setUp(self):
        from ppap_application import build_product_store
        from ppap_core.settings import Settings

        self.schema.require_postgres()
        self.database = "ppap_bindings_" + uuid.uuid4().hex[:12]
        self.schema.require(
            self.schema.psql(self.schema.bootstrap_user(), self.schema.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {self.database} OWNER {self.schema.migrator_user()}"),
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
        else:
            self.schema.require(self.schema.apply(self.database), "apply audit schema")
        self.settings = Settings(
            pg_database=self.database,
            pg_host=os.environ.get("PGHOST", "127.0.0.1"),
            pg_port=int(os.environ.get("PGPORT", "5432")),
            pg_migrator_user=self.schema.migrator_user(),
            pg_migrator_password=self.schema.migrator_password(),
        )  # noqa: E501
        self.store = build_product_store(self.settings, objects=self.memory_objects_type())
        self.store.seed_isolated_dev()
        self.alice = self.store.principal_for_fixture("alice")

    def upload(self, name):
        version = self.store.create_staging(self.alice, name, "application/octet-stream", 1)
        self.store.put_blob(version, b"x")
        return self.store.finalize_version(version)

    def draft(self, items):
        draft = self.store.create_draft(self.alice)
        self.store.save_draft(self.alice, draft["id"], items)
        return draft["id"]


class AuditBindingDatabaseTests(AuditDatabaseCase):
    def test_support_reads_current_session_and_does_not_create_audit_or_usage_records(self):
        from fastapi.testclient import TestClient
        from ppap_api import create_app
        from ppap_core.identity import SESSION_COOKIE

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
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from ppap_api.audit_routes import register_audit_routes
        from ppap_api.web_contract import Round
        from ppap_application import build_product_store
        from ppap_worker import process_claimed
        from pydantic import TypeAdapter

        files = [self.upload(name) for name in ("missing.xlsx", "healthy.xlsx", "last.xlsx")]
        rule = self.store.publish_rule(self.alice, "客户自定义要求", self.rule_body(), files[-1].id)
        draft = self.draft([{"file_version_id": file.id, "role": "target", "rule_version_id": rule["id"]} for file in files])
        accepted = self.store.accept_draft(self.alice, draft, "file-isolation")
        self.store.objects.delete(files[0].object_key)
        worker = build_product_store(self.settings, objects=self.store.objects, role="worker")
        claimed = worker.claim_job("file-isolation-test")
        self.assertIsNotNone(claimed)
        process_claimed(worker, claimed, "file-isolation-test")

        app = FastAPI()
        register_audit_routes(app, self.store, lambda request: self.alice)
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
        self.assertEqual([audits[file.id]["counts"]["not_covered_count"] for file in files[1:]], [1, 1])
        self.assertEqual([row["status"] for row in observed["findings"]], ["ERR"])
        self.assertTrue(all(audit["report"]["state"] == "not_eligible" for audit in audits.values()))
        self.assertEqual(client.get(f"/rounds/{accepted['id']}?read_view=true").json(), observed)
        self.assertFalse(worker.complete_job(claimed, "file-isolation-test", {"findings": [], "coverage": {}}, "succeeded"))
        self.assertEqual(client.get(f"/rounds/{accepted['id']}?read_view=true").json(), observed)

    def test_manual_coverage_review_round_trip_preserves_machine_facts_and_rejects_stale_or_outside_input(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from ppap_api.audit_routes import register_audit_routes
        from ppap_application import build_product_store

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
        register_audit_routes(app, self.store, lambda request: principal[0])
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
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from ppap_api.audit_routes import register_audit_routes
        from ppap_api.web_contract import Round
        from ppap_core.fixtures import RULE_A
        from pydantic import TypeAdapter

        target, reference = self.upload("historical.txt"), self.upload("reference.txt")
        draft = self.draft([{"file_version_id": target.id, "role": "target", "rule_version_id": RULE_A}, {"file_version_id": reference.id, "role": "reference"}])
        round = self.store.accept_draft(self.alice, draft, "historical-reader")
        principal = [self.alice]
        app = FastAPI()
        register_audit_routes(app, self.store, lambda request: principal[0])
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
        from ppap_core.fixtures import RULE_A

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
        from ppap_core.fixtures import RULE_A

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
        self.assertEqual(by_id[second.id]["rule"]["body"], self.rule_body())
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
        self.assertEqual(next(item for item in next_round["inputs"] if item["file_version_id"] == second.id)["rule"]["body"], changed)

    def test_same_key_concurrent_acceptance_and_conflicts_check_bindings_roles_and_round_access(self):
        from ppap_core.fixtures import RULE_A

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
        from ppap_core.fixtures import RULE_A, RULE_B

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
        from ppap_core.audits import request_fingerprint
        from ppap_core.files import sha256_bytes, version_object_key
        from ppap_core.fixtures import RULE_A
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
    def prepared_display_task(self, files=None, *, analysis_mode="standard", complete_audit=True, per_file_rules=False, confirm=True):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from ppap_api.audit_routes import register_audit_routes
        from ppap_application import build_product_store

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
        task = self.store.create_audit_task(self.alice, {"request_id": str(uuid.uuid4()), "analysis_mode": analysis_mode, "customer_preference": None, "files": inputs})
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
        register_audit_routes(app, self.store, lambda request: self.display_principal)
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
        values = {"customer": "更正客户", "model": None, "part_number": "P-2", "part": "更正零件"}
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
        from ppap_core.db import connect

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
        from ppap_core.db import connect

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
        self.assertEqual(loaded["files"][0]["display_info"], {"revision": 0, "values": {"customer": "测试客户", "model": "M1", "part_number": "P-1", "part": "原零件"}})
        second_command = {**command, "reference": {**command["reference"], "task_id": second["id"], "round_id": second["round_id"]}, "values": {**command["values"], "part": "第二轮名称"}}
        self.assertEqual(self.display_client.post(path + "/display-info", json=second_command).status_code, 409)
        self.assertIsNone(self.display_client.get(path + "/display-info/" + command["request_id"]).json())
        self.assertEqual(self.display_client.post(path + "/display-info", json={**second_command, "request_id": str(uuid.uuid4())}).json()["info"]["revision"], 1)
        self.assertEqual(self.display_client.get(f"/audit-tasks/{first['id']}").json()["files"][0]["display_info"], receipt["info"])

    def test_display_save_waiting_on_revoked_review_permission_cannot_commit(self):
        from ppap_core.db import connect

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
        from io import BytesIO
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from openpyxl import Workbook
        from ppap_deep.auditor import product_response, semantic_inputs
        from ppap_deep.mapping import product_result, spec_from_work
        from ppap_deep.semantic.document_tools import snapshot
        from ppap_deep.semantic.protocol import ResultBundle

        versions, contents = [], {}
        for filename, value in documents:
            workbook = Workbook()
            workbook.active.append(["零件号"])
            workbook.active.append([value])
            content = BytesIO()
            workbook.save(content)
            version = self.store.create_staging(self.alice, filename, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", len(content.getvalue()))
            self.store.put_blob(version, content.getvalue())
            version = self.store.finalize_version(version)
            versions.append(version)
            contents[version.id] = content.getvalue()
        _files, task, worker = self.prepared_display_task(versions, analysis_mode="deep", complete_audit=False, per_file_rules=len(versions) > 1)
        job = worker.claim_job("verified-query")
        work = worker.round_work(task["round_id"])
        spec = spec_from_work(work, job.payload["executor"], job.payload["executor_revision"], job.payload["executor_revision"]).model_copy(update={"job_id": job.id, "attempt": job.attempt})
        submission, packets = semantic_inputs(spec, "a" * 64)
        with TemporaryDirectory() as directory:
            sources, rule_sources = {}, {}
            for version in versions:
                target_path = Path(directory) / (version.id + ".xlsx")
                target_path.write_bytes(contents[version.id])
                sources[version.id] = snapshot(target_path)
            for rule in submission.rules:
                rule_path = Path(directory) / (rule.rule_id + ".json")
                rule_path.write_bytes(packets[rule.rule_id])
                rule_sources[rule.rule_id] = snapshot(rule_path)
        bundle = ResultBundle.model_validate(
            {
                "execution_id": str(submission.execution_id),
                "semantic_engine_revision": "a" * 64,
                "documents": sources,
                "rule_sources": rule_sources,
                "plans": {
                    rule.rule_id: {
                        "source_sha256": rule.sha256,
                        "plan_revision": rule.plan_revision,
                        "rules": [
                            {
                                "id": "1",
                                "description": "必须填写零件号",
                                "source_locator": {"kind": "published_clause", "clause_id": "1"},
                                "scope": "第二行的零件号",
                                "predicate": {"kind": "query", "quantifier": "forall", "predicates": [{"field": "part_number", "op": "not_empty"}]},
                                "missing_evidence": "UNABLE",
                                "ambiguities": [],
                                "empty_scope_status": "UNABLE",
                                "empty_scope_basis": "无正文不认定通过",
                            }
                        ],
                    }
                    for rule in submission.rules
                },
                "report": {
                    "checks": [
                        {
                            "audit_id": version.id,
                            "rule_id": "1",
                            "status": "PASS",
                            "reason": f"正文零件号 {value} 已填写",
                            "total": 1,
                            "checked": 1,
                            "unknown": 0,
                            "evidence": [{"document_id": version.id, "locator": {"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A2"}}],
                        }
                        for version, (_filename, value) in zip(versions, documents, strict=True)
                    ]
                },
                "trace": {
                    "bindings": [
                        {
                            "id": "body-" + version.id,
                            "document_id": version.id,
                            "source_sha256": version.sha256,
                            "table": "Sheet",
                            "header_rows": [1],
                            "first_row": 2,
                            "last_row": 2,
                            "columns": {"part_number": "A"},
                            "mapping_basis": "首行零件号列",
                            "boundary_basis": "正文只有第二行",
                        }
                        for version in versions
                    ],
                    "proofs": [{"kind": "query", "audit_id": version.id, "rule_id": "1", "binding_id": "body-" + version.id} for version in versions],
                },
            }
        )
        result = product_result(product_response(spec, bundle, submission), work)
        if not verified:
            result["coverage"].pop("verification")
        self.assertTrue(worker.complete_job(job, "verified-query", result))
        return versions[0], task, worker, job, result

    def test_validated_query_result_survives_commit_and_is_shared_by_task_and_round_readers(self):
        version, task, worker, job, result = self.prepared_query_task()
        path = f"/audit-tasks/{task['id']}"
        current = self.display_client.get(path)
        self.assertEqual(current.status_code, 200)
        audit = current.json()["files"][0]["audit"]
        self.assertEqual((audit["conclusion"], audit["counts"]["pass_count"]), ("PASS", 1))
        self.assertTrue(audit["checks"][0]["evidence_verified"])
        self.assertEqual(audit["checks"][0]["evidence"][0]["excerpt"], "P-1")
        self.assertEqual(audit["summary"]["text"], "已核验 1 项要求：满足 1 项，不符合 0 项，不适用 0 项。")
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
        from ppap_worker import process_claimed

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
            self.assertIn("审核总结", text)
            self.assertIn("已核验1项要求：满足1项，不符合0项，不适用0项。", "".join(text.split()))
        for field, value in (("round_id", str(uuid.uuid4())), ("file_version_id", str(uuid.uuid4())), ("result_attempt", 2), ("artifact_id", "other-artifact")):
            self.assertEqual(self.display_client.get(pdf_path, params={**query, field: value}).status_code, 403)
        self.display_principal = self.store.principal_for_fixture("bob")
        self.assertEqual(self.display_client.get(pdf_path, params=query).status_code, 403)
        self.display_principal = self.store.principal_for_fixture("carol")
        self.assertEqual(self.display_client.get(pdf_path, params=query).status_code, 403)
        self.display_principal = None
        self.assertEqual(self.display_client.get(pdf_path, params=query).status_code, 401)

    def test_unverified_machine_pass_does_not_enqueue_a_formal_pdf(self):
        _version, task, worker, _job, _result = self.prepared_query_task(verified=False)
        observed = self.display_client.get(f"/audit-tasks/{task['id']}").json()
        self.assertEqual((observed["state"], observed["files"][0]["audit"]["conclusion"], observed["files"][0]["audit"]["report"]["state"]), ("completed", "UNKNOWN", "not_eligible"))
        self.assertIsNone(worker.claim_job("no-pdf"))

    def test_long_pdf_filename_preserves_the_pdf_suffix(self):
        from ppap_worker import process_claimed

        _version, task, worker, _job, _result = self.prepared_query_task(documents=(("A" * 250 + ".xlsx", "P-1"),))
        pdf_job = worker.claim_job("long-pdf-name")
        process_claimed(worker, pdf_job, "long-pdf-name")
        report = self.display_client.get(f"/audit-tasks/{task['id']}").json()["files"][0]["audit"]["report"]
        self.assertEqual(report["state"], "ready")
        self.assertEqual(report["artifact"]["filename"], "A" * 246 + "-审核报告.pdf")

    def test_two_frozen_file_reports_keep_their_own_sources_rules_and_download_identity(self):
        import pymupdf
        from ppap_worker import process_claimed

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
        from ppap_worker import process_claimed

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

    def test_report_zip_is_persistent_and_contains_the_exact_ready_pdfs_without_another_audit(self):
        from io import BytesIO
        from zipfile import ZipFile

        from ppap_worker import process_claimed

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

        from ppap_worker import process_claimed

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
        from ppap_worker import process_claimed

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

    def test_zip_recovery_rechecks_owner_permissions_and_blocks_only_new_charged_requests(self):
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
        self.assertEqual((denied.status_code, denied.json()), (409, {"error": "audit_report_charging_unavailable"}))
        self.assertFalse(self.display_client.get(f"/audit-report-packages/{command['request_id']}").json()["retry_allowed"])
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
        from ppap_core.files import ObjectMissing
        from ppap_worker import process_claimed

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
        from ppap_worker import process_claimed

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
        from ppap_core.files import ObjectMissing
        from ppap_worker import process_claimed

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

        from ppap_worker import process_claimed

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

        from ppap_core.db import connect
        from ppap_worker import process_claimed

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
        from ppap_worker import process_claimed

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
        from ppap_worker import process_claimed

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

    def test_pdf_recovery_requires_owner_and_explicit_no_charge_configuration(self):
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
        self.assertEqual((denied.status_code, denied.json()), (409, {"error": "audit_report_charging_unavailable"}))
        self.assertFalse(self.display_client.get(f"/audit-tasks/{task['id']}").json()["files"][0]["audit"]["report"]["regenerate_allowed"])
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

        from ppap_worker import process_claimed

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
        from ppap_core.db import connect
        from ppap_docops.audit_pdf import render_audit_pdf
        from ppap_worker import process_claimed

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
        self.settings.feishu_ai_item_id = "test-item"
        self.settings.feishu_free_trial_quota_points = 700

    def prepared(self):
        from ppap_core.fixtures import RULE_A

        target, reference = self.upload("target.xlsx"), self.upload("reference.pdf")
        return self.draft(
            [
                {"file_version_id": target.id, "role": "target", "rule_version_id": RULE_A},
                {"file_version_id": reference.id, "role": "reference"},
            ]
        )

    def test_concurrent_rounds_share_trial_and_replay_does_not_charge(self):
        drafts = [self.prepared(), self.prepared()]
        with ThreadPoolExecutor(2) as pool:
            rounds = list(pool.map(lambda d: self.store.accept_draft(self.alice, d, d), drafts))
        self.store.accept_draft(self.alice, drafts[0], drafts[0])
        events = self.store.list_usage_events(self.alice)
        self.assertEqual(sum(e["consumed_points"] for e in events), 1000)
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
                self.store.accept_draft(self.alice, draft, "rollback")
        with self.store._conn() as conn:
            for table in ("product.audit_rounds", "engine.jobs", "product.usage_events"):
                self.assertEqual(conn.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)
        self.assertEqual(self.store.get_draft(draft)["status"], "open")


if __name__ == "__main__":
    unittest.main()
