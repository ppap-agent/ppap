"""Usage allocation and send ownership on real PostgreSQL, not a mock lock."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from tests.schema.test_audit_bindings import AuditDatabaseCase


class UsageAcceptanceTests(AuditDatabaseCase):
    def setUp(self):
        super().setUp()
        self.settings.feishu_ai_item_id = "test-item"
        self.settings.feishu_free_trial_quota_points = 700

    def prepared(self):
        from ppap_core.fixtures import RULE_A
        target, reference = self.upload("target.xlsx"), self.upload("reference.pdf")
        return self.draft([
            {"file_version_id": target.id, "role": "target", "rule_version_id": RULE_A},
            {"file_version_id": reference.id, "role": "reference"},
        ])

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
        self.assertIsNone(self.store.usage_event(bob, pending["id"]))

    def test_usage_error_rolls_back_round_and_job(self):
        draft = self.prepared()
        with patch.object(self.store, "_record_round_usage", side_effect=RuntimeError("test rollback")):
            with self.assertRaises(RuntimeError):
                self.store.accept_draft(self.alice, draft, "rollback")
        with self.store._conn() as conn:
            for table in ("product.audit_rounds", "engine.jobs", "product.usage_events"):
                self.assertEqual(conn.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)
        self.assertEqual(self.store.get_draft(draft)["status"], "open")
