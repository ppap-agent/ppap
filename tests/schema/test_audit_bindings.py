"""Per-target rules and acceptance concurrency against actual PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
import unittest
import uuid

import tests.pathsetup  # noqa: F401
from tests.schema.test_upgrade import MIGRATIONS, _bootstrap_password, _bootstrap_user, _migrator_password, _migrator_user, _psql, _require


class AuditDatabaseCase(unittest.TestCase):
    previous_schema = False

    def setUp(self):
        from ppap_api.store import AuthStore
        from ppap_core.settings import Settings
        from tests.unit.memory_auth import MemoryObjects

        probe = _psql(_bootstrap_user(), _bootstrap_password(), "postgres", "-Atc", "SELECT 1")
        if probe.returncode:
            self.skipTest("postgres is not reachable with native psql")
        self.database = "ppap_bindings_" + uuid.uuid4().hex[:12]
        _require(_psql(_bootstrap_user(), _bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {self.database} OWNER {_migrator_user()}"), "create isolated audit DB")
        self.addCleanup(lambda: _require(_psql(_bootstrap_user(), _bootstrap_password(), "postgres", "-c", f"DROP DATABASE {self.database} WITH (FORCE)"), "drop isolated audit DB"))
        if self.previous_schema:
            from tests.schema.test_upgrade import migration_files

            for migration in migration_files():
                if migration.name >= "0010":
                    break
                _require(_psql(_migrator_user(), _migrator_password(), self.database, "-f", str(migration)), "apply prior audit schema")
        else:
            _require(_psql(_migrator_user(), _migrator_password(), self.database, "-f", str(MIGRATIONS / "apply.sql")), "apply audit schema")
        self.settings = Settings(pg_database=self.database, pg_host=os.environ.get("PGHOST", "127.0.0.1"), pg_port=int(os.environ.get("PGPORT", "5432")), pg_migrator_user=_migrator_user(), pg_migrator_password=_migrator_password())
        self.store = AuthStore(self.settings, MemoryObjects())
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
    def test_resumable_drafts_keep_origin_and_respect_grants(self):
        from ppap_core.fixtures import RULE_A
        file = self.upload('draft.txt')
        items = [{'file_version_id':file.id, 'role':'target', 'rule_version_id':RULE_A}]
        draft = self.draft(items)
        first = self.store.accept_draft(self.alice, draft, 'origin')
        next_draft = self.store.create_draft(self.alice, first['id'])['id']
        self.store.save_draft(self.alice, next_draft, items)
        rows = self.store.list_drafts(self.alice)
        self.assertEqual([row['id'] for row in rows], [next_draft])
        self.assertEqual(rows[0]['predecessor_id'], first['id'])
        self.assertTrue(rows[0]['updated_at'])
        bob = self.store.principal_for_fixture('bob')
        self.assertEqual(self.store.list_drafts(bob), [])
        with self.assertRaises(KeyError):
            self.store.create_draft(bob, first['id'])
        accepted = self.store.accept_draft(self.alice, next_draft, 'resumed')
        self.assertEqual(accepted['predecessor_id'], first['id'])
        self.assertEqual(self.store.list_drafts(self.alice), [])

    def test_each_target_keeps_its_rule_snapshot_through_replay_and_explicit_resubmit(self):
        from ppap_core.fixtures import RULE_A
        from tests.unit.test_rules import rule_body

        first, second, reference = [self.upload(name) for name in ("a.xlsx", "b.xlsx", "context.pdf")]
        rule = self.store.publish_rule(self.alice, "客户规则", rule_body(), reference.id)
        items = [{"file_version_id": first.id, "role": "target", "rule_version_id": RULE_A}, {"file_version_id": second.id, "role": "target", "rule_version_id": rule["id"]}, {"file_version_id": reference.id, "role": "reference"}]
        draft = self.draft(items)
        accepted = self.store.accept_draft(self.alice, draft, "per-input")
        by_id = {item["file_version_id"]: item for item in accepted["inputs"]}
        self.assertEqual(by_id[first.id]["rule"]["id"], RULE_A)
        self.assertEqual(by_id[second.id]["rule"]["body"], rule_body())
        self.assertIsNone(by_id[reference.id]["rule"])
        self.assertNotIn("rule", accepted)
        self.assertEqual(self.store.accept_draft(self.alice, draft, "per-input")["id"], accepted["id"])
        changed = rule_body()
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
        from tests.unit.test_rules import rule_body

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
        rule = self.store.publish_rule(self.alice, "客户规则", rule_body(), reference.id)
        changed_rule = [{**items[0], "rule_version_id": rule["id"]}, items[1]]
        with self.assertRaisesRegex(ValueError, "idempotency_conflict"):
            self.store.accept_draft(self.alice, self.draft(changed_rule), key)
        bob = self.store.principal_for_fixture("bob")
        with self.store._conn() as conn:
            for version in (first, reference):
                conn.execute("INSERT INTO product.resource_grants (resource_id, user_id, capability) VALUES (%s, %s, 'read')", (version.file_id, bob.user_id))
        other_draft = self.store.create_draft(bob)["id"]
        self.store.save_draft(bob, other_draft, items)
        with self.assertRaises(KeyError):
            self.store.accept_draft(bob, other_draft, key)

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
        from psycopg.types.json import Jsonb
        from ppap_core.audits import request_fingerprint
        from ppap_core.fixtures import RULE_A

        target, reference = self.upload("target.xlsx"), self.upload("reference.xlsx")
        draft, round_id = str(uuid.uuid4()), str(uuid.uuid4())
        frozen = {"kind": "presence", "snapshot": "accepted before upgrade"}
        with self.store._conn() as conn:
            for ident, kind in ((draft, "audit_draft"), (round_id, "audit_round")):
                conn.execute("INSERT INTO product.resources (id, tenant_id, kind) VALUES (%s,%s,%s)", (ident, self.alice.tenant_id, kind))
                conn.execute("INSERT INTO product.resource_grants VALUES (%s,%s,'read')", (ident, self.alice.user_id))
            conn.execute("INSERT INTO product.audit_drafts (id,tenant_id,created_by,rule_version_id,status) VALUES (%s,%s,%s,%s,'accepted')", (draft, self.alice.tenant_id, self.alice.user_id, RULE_A))
            conn.execute("INSERT INTO product.audit_rounds (id,tenant_id,created_by,source_draft_id,rule_version_id,rule_title,rule_body,idempotency_key,request_fingerprint,execution) VALUES (%s,%s,%s,%s,%s,'Frozen title',%s,'old-key','old-fingerprint','completed')", (round_id, self.alice.tenant_id, self.alice.user_id, draft, RULE_A, Jsonb(frozen)))
            for version, role in ((target, "target"), (reference, "reference")):
                conn.execute("INSERT INTO product.draft_items VALUES (%s,%s,%s)", (draft, version.id, role))
                conn.execute("INSERT INTO product.round_inputs VALUES (%s,%s,%s)", (round_id, version.id, role))
            conn.execute("INSERT INTO product.findings (id,round_id,item_key,classification,item,status) VALUES (%s,%s,'check','A','Original result','NG')", (str(uuid.uuid4()), round_id))
            conn.execute("INSERT INTO product.reviews (round_id,item_key,user_id,judgment,version) VALUES (%s,'check',%s,'保留人工判断',1)", (round_id, self.alice.user_id))
        for _ in range(2):
            _require(_psql(_migrator_user(), _migrator_password(), self.database, "-f", str(MIGRATIONS / "0010_input_rule_bindings.sql")), "upgrade input rule snapshots")
        loaded = self.store.get_round(round_id)
        by_id = {row["file_version_id"]: row for row in loaded["inputs"]}
        self.assertEqual(by_id[target.id]["rule"], {"id": RULE_A, "title": "Frozen title", "body": frozen})
        self.assertIsNone(by_id[reference.id]["rule"])
        self.assertEqual(loaded["findings"][0]["status"], "NG")
        self.assertEqual(loaded["findings"][0]["review"]["judgment"], "保留人工判断")
        _require(_psql(_migrator_user(), _migrator_password(), self.database, "-f", str(MIGRATIONS / "apply.sql")), "upgrade to current before using current store")
        self.assertEqual(self.store.accept_draft(self.alice, draft, "old-key")["id"], round_id)
        with self.store._conn() as conn:
            fingerprint = conn.execute("SELECT request_fingerprint FROM product.audit_rounds WHERE id=%s", (round_id,)).fetchone()[0]
        self.assertEqual(fingerprint, request_fingerprint([{"file_version_id": target.id, "role": "target", "rule_version_id": RULE_A}, {"file_version_id": reference.id, "role": "reference"}]))


if __name__ == "__main__":
    unittest.main()
