"""Product audit draft/round/review loop. Does not copy checker corpus into E2E."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.fixtures import RULE_A, USER_BOB
from product.settings import Settings
from product.store.suite_releases import SuiteReleaseStore
from psycopg import errors, sql
from worker import process_claimed

ROOT = Path(__file__).resolve().parents[2]


def rule_body():
    return {
        "kind": "engine",
        "rule_profile": "v3",
        "type": "控制计划",
        "customers": ["测试客户"],
        "models": ["M1"],
        "parts": ["P1"],
        "rules": [{"id": "1", "category": "基本信息", "priority": "P0", "rule_to_check": "必须填写零件号", "type_of_rule": "table"}],
    }


def _clean_env(**extra: str):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("PPAP_", "FEISHU_"))}
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class AuditFlowTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory, core_system):
        self.auth_store = auth_store_factory
        self.core = core_system

    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings(usage_charge_enabled=False)
        self.store = self.auth_store(self.settings)
        self.store.seed_isolated_dev()
        self.core.adopt_presence(self.store)
        self.core.install(self.store)
        self.originals = {}
        self.client = TestClient(create_app(self.settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.store))

    def tearDown(self):
        self._env.stop()

    def _scalar(self, query: str, params=()):
        with self.store._conn() as conn:
            return conn.execute(query, params).fetchone()[0]

    def _execute(self, query: str, params=()) -> None:
        with self.store._conn() as conn:
            conn.execute(query, params)

    def _login(self, subject: str) -> None:
        self.client.cookies.clear()
        response = self.client.post("/isolated-dev/sessions", json={"subject": subject})
        self.assertEqual(response.status_code, 200)

    def _upload(self, name: str, body: bytes, file_id: str | None = None) -> dict:
        if name.endswith(".xlsx"):
            body = self.core.workbook(body.decode())
        meta = {"original_filename": name, "content_type": "application/octet-stream", "byte_size": len(body)}
        created = self.client.post(f"/files/{file_id}/versions", json=meta) if file_id else self.client.post("/files", json=meta)
        self.assertEqual(created.status_code, 201)
        file_id, vid = created.json()["file_id"], created.json()["id"]
        self.assertEqual(self.client.put(f"/files/{file_id}/versions/{vid}/blob", content=body).status_code, 200)
        ready = self.client.post(f"/files/{file_id}/versions/{vid}/finalize")
        self.assertEqual(ready.status_code, 200)
        self.originals[vid] = body
        return ready.json()

    def _finish_worker(self) -> None:
        claimed = self.store.claim_job("test-worker")
        self.assertIsNotNone(claimed)
        process_claimed(self.store, claimed, "test-worker")

    def test_reaudit_draft_acceptance_retains_authorized_predecessor(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")

        def draft():
            ident = self.client.post("/drafts").json()["id"]
            self.client.put(f"/drafts/{ident}", json={"items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A}]})
            return ident

        first_draft = draft()
        first = self.client.post(f"/drafts/{first_draft}/accept", json={"idempotency_key": "first-report"}).json()
        second_draft = draft()
        payload = {"idempotency_key": "second-report", "predecessor_id": first["id"]}
        second = self.client.post(f"/drafts/{second_draft}/accept", json=payload)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.json()["predecessor_id"], first["id"])
        self.assertEqual(self.client.get(f"/drafts/{second_draft}").json()["status"], "accepted")
        self.assertEqual(self.client.post(f"/drafts/{second_draft}/accept", json=payload).json()["id"], second.json()["id"])
        self.assertEqual(self.client.post(f"/drafts/{second_draft}/accept", json={"idempotency_key": "second-report"}).status_code, 409)
        third_draft = draft()
        self._execute(
            "DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s",
            (first["id"], self.store.principal_for_fixture("alice").user_id),
        )
        third = self.client.post(f"/drafts/{third_draft}/accept", json={"idempotency_key": "third-report", "predecessor_id": first["id"]})
        self.assertEqual(third.status_code, 201)
        self.assertEqual(third.json()["predecessor_id"], first["id"])

    def test_reaudit_preparation_keeps_original_inputs_and_replays_one_draft(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")
        reference = self._upload("context.pdf", b"reference-bytes")
        inputs = [
            {"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A},
            {"file_version_id": reference["id"], "role": "reference", "rule_version_id": None},
        ]
        source = self.client.post("/drafts").json()["id"]
        self.assertEqual(self.client.put(f"/drafts/{source}", json={"items": inputs}).status_code, 200)
        original = self.client.post(f"/drafts/{source}/accept", json={"idempotency_key": "preparation-source"}).json()
        jobs = self._scalar("SELECT count(*) FROM engine.jobs")
        charges = self._scalar("SELECT count(*) FROM product.usage_events")
        request_id = str(uuid.uuid4())
        payload = {"request_id": request_id, "predecessor_id": original["id"], "items": inputs, "case_context": {"registration": "{}"}}
        created = self.client.post("/drafts", json=payload)
        self.assertEqual(created.status_code, 201)
        prepared = created.json()
        self.assertEqual(prepared["id"], request_id)
        self.assertEqual(prepared["status"], "open")
        self.assertEqual(prepared["predecessor_id"], original["id"])
        self.assertCountEqual([{key: item[key] for key in ("file_version_id", "role", "rule_version_id")} for item in prepared["items"]], inputs)
        self.assertEqual(prepared["case_context"], payload["case_context"])
        self.assertEqual(self.client.get(f"/drafts/{request_id}").json(), prepared)
        self.assertEqual(self.client.post("/drafts", json=payload).json(), prepared)
        self.assertEqual(self.client.get(f"/rounds/{original['id']}").json(), original)
        self.assertEqual(self._scalar("SELECT count(*) FROM engine.jobs"), jobs)
        self.assertEqual(self._scalar("SELECT count(*) FROM product.usage_events"), charges)
        conflict = self.client.post("/drafts", json={**payload, "items": inputs[:1]})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.client.get(f"/drafts/{request_id}").json(), prepared)
        self._login("bob")
        self.assertEqual(self.client.post("/drafts", json=payload).status_code, 403)
        self._login("carol")
        self.assertEqual(self.client.post("/drafts", json=payload).status_code, 403)
        self._login("alice")
        self._execute("DELETE FROM product.resource_grants WHERE resource_id = %s", (request_id,))
        self.assertEqual(self.client.get(f"/drafts/{request_id}").status_code, 403)
        self.assertEqual(self.client.post("/drafts", json=payload).status_code, 403)

    def test_initial_draft_inputs_are_atomic_and_concurrent_creation_reuses_identity(self):
        self._login("alice")
        target = self._upload("initial.xlsx", b"initial-bytes")
        request_id = str(uuid.uuid4())
        payload = {"request_id": request_id, "items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A}]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.client.post("/drafts", json=payload), range(2)))
        self.assertEqual([response.status_code for response in responses], [201, 201])
        self.assertEqual(responses[0].json(), responses[1].json())
        self.assertEqual(self.client.get(f"/drafts/{request_id}").json()["items"][0]["file_version_id"], target["id"])
        self.assertEqual(self._scalar("SELECT count(*) FROM product.audit_drafts WHERE id = %s", (request_id,)), 1)
        self.assertEqual(self.client.post("/drafts", json={"request_id": "invalid"}).status_code, 400)
        self.assertEqual(self.client.post("/drafts", json={"request_id": target["file_id"]}).status_code, 403)
        self.assertEqual(self.client.post("/drafts", json={"request_id": str(uuid.uuid4()), "case_context": []}).status_code, 400)
        failed_id = str(uuid.uuid4())
        self._login("carol")
        self.assertEqual(self.client.post("/drafts", json={**payload, "request_id": failed_id}).status_code, 403)
        self.assertEqual(self._scalar("SELECT count(*) FROM product.resources WHERE id = %s", (failed_id,)), 0)
        self.assertEqual(self._scalar("SELECT count(*) FROM engine.jobs"), 0)
        self.assertEqual(self._scalar("SELECT count(*) FROM product.usage_events"), 0)

    def test_draft_does_not_create_a_round(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")
        draft = self.client.post("/drafts")
        self.assertEqual(draft.status_code, 201)
        saved = self.client.put(
            f"/drafts/{draft.json()['id']}",
            json={
                "items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A}],
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["status"], "open")
        self.assertEqual(self._scalar("SELECT count(*) FROM engine.jobs"), 0)
        self.assertEqual(self._scalar("SELECT count(*) FROM product.audit_rounds"), 0)
        missing = self.client.get(f"/rounds/{draft.json()['id']}")
        self.assertEqual(missing.status_code, 403)

    def test_accept_idempotency_and_worker_writeback(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")
        reference = self._upload("context.pdf", b"reference-bytes")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{draft}",
            json={
                "items": [
                    {"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A},
                    {"file_version_id": reference["id"], "role": "reference"},
                ],
            },
        )
        first = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "submit-1"})
        replay = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "submit-1"})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(first.json()["id"], replay.json()["id"])
        self.assertEqual(self._scalar("SELECT count(*) FROM product.audit_rounds"), 1)
        self.assertEqual(self._scalar("SELECT count(*) FROM engine.jobs"), 1)
        conflict = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "submit-2"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"], "draft_already_accepted")
        other = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{other}",
            json={
                "items": [{"file_version_id": reference["id"], "role": "target", "rule_version_id": RULE_A}],
            },
        )
        mismatched = self.client.post(f"/drafts/{other}/accept", json={"idempotency_key": "submit-1"})
        self.assertEqual(mismatched.status_code, 409)
        self.assertEqual(mismatched.json()["error"], "idempotency_conflict")
        self._finish_worker()
        round_id = first.json()["id"]
        body = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(body["execution"], "completed")
        statuses = {row["status"] for row in body["findings"]}
        self.assertEqual(statuses, {"PASS"}, body["findings"])
        self.assertFalse(body["coverage"]["locator_gap"])
        self.assertTrue(any(row["locator"] and row["locator"]["kind"] == "file_version" for row in body["findings"]))
        original = body["findings"][0]
        download = self.client.get(f"/rounds/{round_id}/files/{original['locator']['file_id']}/versions/{original['locator']['file_version_id']}/download")
        self.assertIn(download.content, {self.originals[target["id"]], self.originals[reference["id"]]})

    def test_standard_result_writeback_failure_does_not_fabricate_engine_results_or_new_audits(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")
        draft = self.client.post("/drafts").json()["id"]
        saved = self.client.put(f"/drafts/{draft}", json={"items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A}]})
        self.assertEqual(saved.status_code, 200, saved.text)
        accepted = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "standard-writeback-fault"})
        self.assertEqual(accepted.status_code, 201, accepted.text)
        frozen = accepted.json()
        usage = self.client.get("/usage").json()
        worker = build_product_store(self.settings, objects=self.store.objects, role="worker")
        job = worker.claim_job("standard-writeback")
        self.assertIsNotNone(job)
        self.assertEqual((job.kind, job.round_id, job.attempt), ("audit", frozen["id"], 1))

        suffix = uuid.uuid4().hex
        sequence = sql.Identifier("engine", "test_write_sequence_" + suffix)
        function = sql.Identifier("engine", "test_write_failure_" + suffix)
        trigger = sql.Identifier("test_write_failure_" + suffix)
        try:
            with self.store._conn() as conn:
                # This fault belongs to the isolated test DB, not a store mock.
                # nextval survives the rejected transaction, so a second write
                # succeeds and exposes a fabricated engine_error on current code.
                conn.execute(sql.SQL("CREATE SEQUENCE {}").format(sequence))
                conn.execute(
                    sql.SQL("CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS {}").format(
                        function,
                        sql.Literal("BEGIN IF nextval(TG_ARGV[0]::regclass) = 1 THEN RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'test-only audit writeback failure'; END IF; RETURN NEW; END;"),
                    )
                )
                conn.execute(
                    sql.SQL("CREATE TRIGGER {} BEFORE INSERT ON product.findings FOR EACH ROW WHEN (NEW.round_id = {}::uuid) EXECUTE FUNCTION {}({})").format(
                        trigger, sql.Literal(frozen["id"]), function, sql.Literal("engine.test_write_sequence_" + suffix)
                    )
                )
            write_error = None
            try:
                process_claimed(worker, job, "standard-writeback")
            except errors.SerializationFailure as error:
                write_error = error
            loaded = self.client.get(f"/rounds/{frozen['id']}").json()
            writes = self._scalar(sql.SQL("SELECT last_value FROM {}").format(sequence))
            self.assertEqual(loaded["inputs"], frozen["inputs"])
            self.assertIsNone(loaded["failure"])
            if write_error is not None:
                self.assertEqual(write_error.sqlstate, "40001")
                self.assertEqual(loaded["execution"], "running")
                self.assertEqual(loaded["findings"], [])
                self.assertEqual(loaded["coverage"], {})
                self.assertIsNone(loaded["result_attempt"])
            else:
                # A safe retry of the same known result is also valid. It must
                # not replace the actual presence PASS with an engine error.
                self.assertEqual(loaded["execution"], "completed")
                self.assertEqual([row["status"] for row in loaded["findings"]], ["PASS"], f"write attempts={writes}")
                self.assertEqual(loaded["result_attempt"], job.attempt)
            for _ in range(2):
                self.assertEqual(self.client.get(f"/rounds/{frozen['id']}").json(), loaded)
            self.assertIsNone(worker.claim_job("no-writeback-reexecution"))
            self.assertEqual([row["id"] for row in self.client.get("/rounds").json()["rounds"]], [frozen["id"]])
            self.assertEqual(self.client.get("/usage").json(), usage)
        finally:
            with self.store._conn() as conn:
                conn.execute(sql.SQL("DROP TRIGGER IF EXISTS {} ON product.findings").format(trigger))
                conn.execute(sql.SQL("DROP FUNCTION IF EXISTS {}()").format(function))
                conn.execute(sql.SQL("DROP SEQUENCE IF EXISTS {}").format(sequence))

    def test_cross_tenant_and_review_permission(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"secret-bytes")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{draft}",
            json={"items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A}]},
        )
        round_id = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "k"}).json()["id"]
        self._finish_worker()
        self._login("carol")
        denied = self.client.get(f"/rounds/{round_id}")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json(), {"error": "access_denied"})
        self.assertNotIn(b"secret-bytes", denied.content)
        self._login("bob")
        visible = self.client.get(f"/rounds/{round_id}")
        self.assertEqual(visible.status_code, 200)
        finding = visible.json()["findings"][0]
        original = self.client.get(f"/rounds/{round_id}/files/{finding['locator']['file_id']}/versions/{finding['locator']['file_version_id']}/download")
        self.assertEqual(original.status_code, 200)
        self.assertEqual(original.content, self.originals[target["id"]])
        reviewed = self.client.put(
            f"/rounds/{round_id}/reviews",
            json={"item_key": finding["item_key"], "judgment": "同意", "rationale": "x", "next_step": "y", "version": 0},
        )
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(reviewed.json()["findings"][0]["review"]["judgment"], "同意")
        self.assertEqual(
            str(
                self._scalar(
                    "SELECT user_id FROM product.reviews WHERE round_id = %s AND item_key = %s",
                    (round_id, finding["item_key"]),
                )
            ),
            USER_BOB,
        )

    def test_drafts_stay_personal_while_rounds_are_tenant_owned(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"draft-bytes")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{draft}",
            json={"items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A}]},
        )
        self._login("bob")
        self.assertEqual(self.client.get(f"/drafts/{draft}").status_code, 403)
        self.assertEqual(self.client.get("/drafts").json()["drafts"], [])
        self._login("alice")
        round_id = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "tenant-round"}).json()["id"]
        self._login("bob")
        self.assertEqual(self.client.get("/drafts").json()["drafts"], [])
        self.assertEqual(self.client.get(f"/rounds/{round_id}").status_code, 200)

    def test_review_does_not_mutate_finding_and_conflicts(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{draft}",
            json={"items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A}]},
        )
        round_id = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "rev"}).json()["id"]
        self._finish_worker()
        finding = self.client.get(f"/rounds/{round_id}").json()["findings"][0]
        before_review = self.client.get("/rounds").json()["rounds"][0]
        self.assertEqual([row["id"] for row in self.client.get("/rounds?unreviewed=true").json()["rounds"]], [round_id])
        machine = finding["status"]
        saved = self.client.put(
            f"/rounds/{round_id}/reviews",
            json={"item_key": finding["item_key"], "judgment": "需修改", "rationale": "字段不清", "next_step": "补传", "version": 0},
        )
        self.assertEqual(saved.status_code, 200)
        after_review = self.client.get("/rounds").json()["rounds"][0]
        self.assertEqual(after_review["created_at"], before_review["created_at"])
        self.assertGreater(after_review["updated_at"], before_review["updated_at"])
        self.assertEqual(self.client.get("/rounds?unreviewed=true").json()["rounds"], [])
        self.assertEqual(len(self.client.get("/rounds").json()["rounds"]), 1)
        again = saved.json()["findings"][0]
        self.assertEqual(again["status"], machine)
        self.assertEqual(again["review"]["judgment"], "需修改")
        conflict = self.client.put(
            f"/rounds/{round_id}/reviews",
            json={"item_key": finding["item_key"], "judgment": "覆盖", "rationale": "", "next_step": "", "version": 0},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"], "review_conflict")
        updated = self.client.put(
            f"/rounds/{round_id}/reviews",
            json={"item_key": finding["item_key"], "judgment": "通过", "rationale": "已核对", "next_step": "关闭", "version": 1},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["findings"][0]["status"], machine)
        self.assertEqual(updated.json()["findings"][0]["review"]["judgment"], "通过")

    def test_resubmit_keeps_old_round(self):
        self._login("alice")
        first = self._upload("report.xlsx", b"version-one")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{draft}",
            json={"items": [{"file_version_id": first["id"], "role": "target", "rule_version_id": RULE_A}]},
        )
        old_id = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "old"}).json()["id"]
        self._finish_worker()
        self.client.put(
            f"/rounds/{old_id}/reviews",
            json={
                "item_key": self.client.get(f"/rounds/{old_id}").json()["findings"][0]["item_key"],
                "judgment": "旧复核",
                "rationale": "",
                "next_step": "",
                "version": 0,
            },
        )
        second = self._upload("report.xlsx", b"version-two", file_id=first["file_id"])
        created = self.client.post(
            f"/rounds/{old_id}/resubmit",
            json={
                "idempotency_key": "new",
                "items": [{"file_version_id": second["id"], "role": "target", "rule_version_id": RULE_A}],
            },
        )
        self.assertEqual(created.status_code, 201)
        new_id = created.json()["id"]
        self.assertNotEqual(new_id, old_id)
        self._finish_worker()
        old = self.client.get(f"/rounds/{old_id}").json()
        new = self.client.get(f"/rounds/{new_id}").json()
        self.assertEqual(old["findings"][0]["review"]["judgment"], "旧复核")
        self.assertEqual(new["predecessor_id"], old_id)
        self.assertIsNone(new["findings"][0]["review"])
        old_bytes = self.client.get(f"/rounds/{old_id}/files/{first['file_id']}/versions/{first['id']}/download")
        new_bytes = self.client.get(f"/rounds/{new_id}/files/{second['file_id']}/versions/{second['id']}/download")
        self.assertEqual(old_bytes.content, self.originals[first["id"]])
        self.assertEqual(new_bytes.content, self.originals[second["id"]])

    def test_staging_cannot_be_accepted(self):
        self._login("alice")
        created = self.client.post("/files", json={"original_filename": "a.bin", "content_type": "application/octet-stream", "byte_size": 4})
        file_id, vid = created.json()["file_id"], created.json()["id"]
        self.client.put(f"/files/{file_id}/versions/{vid}/blob", content=b"wait")
        draft = self.client.post("/drafts").json()["id"]
        blocked = self.client.put(
            f"/drafts/{draft}",
            json={"items": [{"file_version_id": vid, "role": "target", "rule_version_id": RULE_A}]},
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"], "file_not_ready")

    def test_late_attempt_does_not_overwrite(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{draft}",
            json={"items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A}]},
        )
        round_id = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "fence"}).json()["id"]
        first = self.store.claim_job("w1")
        process_claimed(self.store, first, "w1")
        stale = first
        later = self.store.claim_job("w2")
        self.assertIsNone(later)
        from audit.audits import ClaimedJob

        stale_write = ClaimedJob(stale.id, stale.kind, stale.payload, stale.attempt, stale.round_id)
        self._execute(
            "UPDATE engine.jobs SET status = 'leased', lease_owner = 'late' WHERE id = %s",
            (stale.id,),
        )
        overwritten = self.store.complete_job(
            ClaimedJob(stale.id, stale.kind, stale.payload, 0, stale.round_id),
            "late",
            {
                "findings": [
                    {
                        "item_key": "target:" + target["id"],
                        "classification": "资料",
                        "item": "待审文件版本已就绪",
                        "priority": "P0",
                        "status": "ERR",
                        "suggestions": "stale",
                        "locator": None,
                    }
                ],
                "coverage": {"checked": [], "unchecked": [], "locator_gap": True},
            },
        )
        self.assertFalse(overwritten)
        body = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(body["findings"][0]["status"], "PASS")
        self.assertEqual(stale_write.round_id, round_id)

    def test_expired_leased_job_is_reclaimed_and_late_attempt_is_rejected(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{draft}",
            json={"items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A}]},
        )
        round_id = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "reclaim"}).json()["id"]
        first = self.store.claim_job("w1")
        self.assertEqual(first.attempt, 1)
        self.assertIsNone(self.store.claim_job("w2"))
        self._execute(
            "UPDATE engine.jobs SET lease_until = now() - interval '1 second' WHERE id = %s",
            (first.id,),
        )
        second = self.store.claim_job("w2")
        self.assertIsNotNone(second)
        self.assertEqual(second.id, first.id)
        self.assertEqual(second.attempt, 2)
        rejected = self.store.complete_job(
            first,
            "w1",
            {
                "findings": [
                    {
                        "item_key": "target:" + target["id"],
                        "classification": "资料",
                        "item": "待审文件版本已就绪",
                        "priority": "P0",
                        "status": "ERR",
                        "suggestions": "late-attempt-1",
                        "locator": None,
                    }
                ],
                "coverage": {"checked": [], "unchecked": [], "locator_gap": True},
            },
        )
        self.assertFalse(rejected)
        process_claimed(self.store, second, "w2")
        body = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(body["result_attempt"], 2)
        self.assertEqual(body["findings"][0]["status"], "PASS")
        self.assertNotEqual(body["findings"][0]["suggestions"], "late-attempt-1")

    def test_accept_keeps_every_target_and_reference(self):
        self._login("alice")
        first = self._upload("a.xlsx", b"one")
        second = self._upload("b.xlsx", b"two")
        reference = self._upload("context.pdf", b"ref")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{draft}",
            json={
                "items": [
                    {"file_version_id": first["id"], "role": "target", "rule_version_id": RULE_A},
                    {"file_version_id": second["id"], "role": "target", "rule_version_id": RULE_A},
                    {"file_version_id": reference["id"], "role": "reference"},
                ],
            },
        )
        round_id = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "multi"}).json()["id"]
        self._finish_worker()
        body = self.client.get(f"/rounds/{round_id}").json()
        keys = {row["item_key"] for row in body["findings"]}
        self.assertEqual(keys, {f"{first['id']}:rule:presence", f"{second['id']}:rule:presence"})
        self.assertEqual(sorted(body["coverage"]["checked"]), ["a.xlsx", "b.xlsx"])
        self.assertTrue(any("context.pdf" in row for row in body["coverage"]["unchecked"]))
        self.assertFalse(body["coverage"]["locator_gap"])

    def test_round_history_lists_authorized_rounds_only(self):
        self.assertEqual(self.client.get("/rounds").status_code, 401)
        self._login("alice")
        empty = self.client.get("/rounds")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["rounds"], [])
        target = self._upload("report.xlsx", b"target-bytes")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{draft}",
            json={"items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A}]},
        )
        round_id = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "history-1"}).json()["id"]
        listed = self.client.get("/rounds").json()["rounds"]
        self.assertEqual([row["id"] for row in listed], [round_id])
        self.assertEqual(listed[0]["inputs"][0]["original_filename"], "report.xlsx")
        self.assertTrue(listed[0]["inputs"][0]["file_version_id"])
        self.assertEqual(set(listed[0]["result_counts"]), {"PASS", "NG", "ERR", "UNABLE", "NA"})
        self.assertGreaterEqual(listed[0]["updated_at"], listed[0]["created_at"])
        self.assertNotIn("findings", listed[0])
        self._finish_worker()
        done = self.client.get("/rounds").json()["rounds"]
        self.assertGreater(done[0]["result_counts"]["PASS"], 0)
        self.assertGreater(done[0]["updated_at"], listed[0]["updated_at"])
        self._login("carol")
        other = self.client.get("/rounds")
        self.assertEqual(other.status_code, 200)
        self.assertEqual(other.json()["rounds"], [])
        self._login("bob")
        bob_list = self.client.get("/rounds").json()["rounds"]
        self.assertEqual([row["id"] for row in bob_list], [round_id])

    def test_uploaded_rule_without_binding_cannot_claim_presence_pass(self):
        self._login("alice")
        source = self._upload("new-rule.json", b"{}")
        uploaded = self.client.post("/rules", json={"title": "新规则", "body": rule_body(), "source_file_version_id": source["id"]})
        self.assertEqual(uploaded.status_code, 201)
        draft = self.client.post("/drafts", json={"items": [{"file_version_id": source["id"], "role": "target", "rule_version_id": uploaded.json()["id"]}]}).json()
        accepted = self.client.post(f"/drafts/{draft['id']}/accept", json={"idempotency_key": "unimplemented-upload"})
        # Without a generated program the rule is not offered: nothing runs, so nothing can pass.
        self.assertEqual(accepted.status_code, 400)
        self.assertEqual(accepted.json()["error"], "checker_not_generated")
        self.assertEqual(self.core.calls, 0)

    def test_selected_rule_items_are_frozen_in_confirmed_order(self):
        self._login("alice")
        source = self._upload("rules.json", b"{}")
        mapped_text = "明确绑定的首条检查"
        body = rule_body()
        body["rules"] = [
            {"id": "keep", "category": mapped_text, "priority": "P1", "rule_to_check": mapped_text, "type_of_rule": "text"},
            {"id": "extra", "category": "customer-only", "priority": None, "rule_to_check": "arbitrary customer requirement", "type_of_rule": "text"},
        ]
        published = self.client.post("/rules", json={"title": "控制计划清单", "body": body, "source_file_version_id": source["id"]})
        self.assertEqual(published.status_code, 201)
        self.core.adopt(
            self.store,
            published.json()["id"],
            """from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource

def check(context):
    clause = next(row for row in context.rule.rules if row["id"] == context.clause_id)
    from openpyxl import load_workbook
    from io import BytesIO
    book = load_workbook(BytesIO(context.sources[context.audit_file.audit_id]))
    assert book.active["A1"].value == "target-bytes"
    book.close()
    return AuditResultItem(status="PASS", classification="规则", item=clause["rule_to_check"], priority="P0",
        suggestions="已核对受控原件", clause_id=context.clause_id, checks_performed=1,
        evidence=AuditEvidence(required="structure", used="structure", source_format="xlsx", prepared_format="xlsx",
            sources=(EvidenceSource(kind="structure", locator='{"kind":"xlsx_cell","sheet":"Sheet","cell":"A1"}'),)))
""",
        )
        target = self._upload("plan.xlsx", b"target-bytes")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(f"/drafts/{draft}", json={"items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": published.json()["id"]}]})
        accepted = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "rule-items-1"})
        self.assertEqual(accepted.status_code, 201)
        frozen = self._scalar(
            "SELECT rule_execution FROM product.round_inputs WHERE round_id = %s AND role = 'target'",
            (accepted.json()["id"],),
        )
        self.assertEqual(frozen["mode"], "checker_core")
        accepted_rules = accepted.json()["inputs"][0]["rule"]["body"]["rules"]
        self.assertEqual([row["id"] for row in accepted_rules], ["keep", "extra"])
        self.assertEqual(frozen["verification"], "unverified")
        self.assertEqual(frozen["binding"]["entrypoints"], {"keep": "checker:check", "extra": "checker:check"})

        self._finish_worker()
        loaded = self.client.get("/rounds/" + accepted.json()["id"]).json()
        self.assertEqual(loaded["findings"][0]["item_key"], target["id"] + ":rule:keep")
        self.assertEqual(loaded["findings"][0]["item"], mapped_text)
        self.assertFalse(loaded["coverage"]["unchecked"])
        self.assertEqual(loaded["findings"][1]["item"], "arbitrary customer requirement")
        releases = SuiteReleaseStore(self.store.settings, self.store.objects, role="api")
        principal = self.store.principal_for_fixture("alice")
        suite_key = published.json()["body"]["suite_key"]
        active = releases.active(principal, suite_key)
        changed = rule_body()
        changed["rules"][0]["rule_to_check"] = "新版要求"
        successor = self.client.post("/rules", json={"title": "新版", "body": changed, "source_file_version_id": source["id"], "predecessor_id": published.json()["id"]})
        self.assertEqual(successor.status_code, 201)
        # Upload is a candidate based on the adopted Checker, not adoption.
        after_upload = releases.active(principal, suite_key)
        self.assertEqual(after_upload["current_revision_id"], active["current_revision_id"])
        self.assertEqual(after_upload["revision"], active["revision"])
        self.assertEqual(after_upload["history"], active["history"])
        self.assertEqual(after_upload["verification_status"], "stale")
        self.assertEqual(after_upload["rule_update"]["target_revision_id"], successor.json()["body"]["suite_revision_id"])
        self.assertEqual(self.client.get("/rounds/" + accepted.json()["id"]).json(), loaded)
        candidate_id = successor.json()["body"]["suite_revision_id"]
        releases.publish(principal, suite_key, candidate_id, active["revision"], str(uuid.uuid4()), force_unverified=True)
        self.assertEqual(releases.active(principal, suite_key)["current_revision_id"], candidate_id)
        replay = self.client.get("/rounds/" + accepted.json()["id"]).json()
        self.assertEqual(replay, loaded)
        self.assertEqual(
            self._scalar("SELECT rule_execution FROM product.round_inputs WHERE round_id = %s AND role = 'target'", (accepted.json()["id"],)),
            frozen,
        )
        self.assertEqual(replay["findings"][0]["item"], mapped_text)
        self.assertEqual(replay["inputs"][0]["rule"]["id"], published.json()["id"])


class PresenceEvaluationTests(unittest.TestCase):
    def test_round_payload_does_not_import_engine(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
from worker import run_claimed_job
result = run_claimed_job("audit", {"round_id": "abc"})
assert result["note"] == "round_audit"
assert "audit.engine.app_runtime" not in sys.modules
""",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
