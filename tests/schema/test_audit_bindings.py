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
