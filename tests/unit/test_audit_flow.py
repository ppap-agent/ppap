"""Product audit draft/round/review loop. Does not copy checker corpus into E2E."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages"), str(ROOT / "services" / "api"), str(ROOT / "services" / "worker")]

from fastapi.testclient import TestClient

from ppap_core.audits import evaluate_round
from ppap_core.fixtures import RULE_A, USER_BOB
from ppap_core.settings import Settings
from ppap_api import create_app
from ppap_worker import process_claimed, run_claimed_job
from tests.unit.memory_auth import MemoryAuthStore


def _clean_env(**extra: str):
    keep = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))
    }
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class AuditFlowTests(unittest.TestCase):
    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings()
        self.store = MemoryAuthStore(self.settings)
        self.store.seed_isolated_dev()
        self.client = TestClient(
            create_app(self.settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.store)
        )

    def tearDown(self):
        self._env.stop()

    def _login(self, subject: str) -> None:
        self.client.cookies.clear()
        response = self.client.post("/isolated-dev/sessions", json={"subject": subject})
        self.assertEqual(response.status_code, 200)

    def _upload(self, name: str, body: bytes, file_id: str | None = None) -> dict:
        meta = {"original_filename": name, "content_type": "application/octet-stream", "byte_size": len(body)}
        created = (
            self.client.post(f"/files/{file_id}/versions", json=meta)
            if file_id
            else self.client.post("/files", json=meta)
        )
        self.assertEqual(created.status_code, 201)
        file_id, vid = created.json()["file_id"], created.json()["id"]
        self.assertEqual(self.client.put(f"/files/{file_id}/versions/{vid}/blob", content=body).status_code, 200)
        ready = self.client.post(f"/files/{file_id}/versions/{vid}/finalize")
        self.assertEqual(ready.status_code, 200)
        return ready.json()

    def _finish_worker(self) -> None:
        claimed = self.store.claim_job("test-worker")
        self.assertIsNotNone(claimed)
        process_claimed(self.store, claimed, "test-worker")

    def test_draft_does_not_create_a_round(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")
        draft = self.client.post("/drafts")
        self.assertEqual(draft.status_code, 201)
        saved = self.client.put(
            f"/drafts/{draft.json()['id']}",
            json={
                "rule_version_id": RULE_A,
                "items": [{"file_version_id": target["id"], "role": "target"}],
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["status"], "open")
        self.assertEqual(self.store.jobs, {})
        self.assertEqual(self.store.rounds, {})
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
                "rule_version_id": RULE_A,
                "items": [
                    {"file_version_id": target["id"], "role": "target"},
                    {"file_version_id": reference["id"], "role": "reference"},
                ],
            },
        )
        first = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "submit-1"})
        replay = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "submit-1"})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(first.json()["id"], replay.json()["id"])
        self.assertEqual(len(self.store.rounds), 1)
        self.assertEqual(len(self.store.jobs), 1)
        conflict = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "submit-2"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"], "draft_already_accepted")
        other = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{other}",
            json={
                "rule_version_id": RULE_A,
                "items": [{"file_version_id": reference["id"], "role": "target"}],
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
        self.assertEqual(statuses, {"PASS"})
        self.assertFalse(body["coverage"]["locator_gap"])
        self.assertTrue(any(row["locator"] and row["locator"]["kind"] == "file_version" for row in body["findings"]))
        original = body["findings"][0]
        download = self.client.get(
            f"/rounds/{round_id}/files/{original['locator']['file_id']}/versions/{original['locator']['file_version_id']}/download"
        )
        self.assertIn(download.content, {b"target-bytes", b"reference-bytes"})

    def test_cross_tenant_and_review_permission(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"secret-bytes")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{draft}",
            json={"rule_version_id": RULE_A, "items": [{"file_version_id": target["id"], "role": "target"}]},
        )
        round_id = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "k"}).json()["id"]
        self._finish_worker()
        self._login("carol")
        denied = self.client.get(f"/rounds/{round_id}")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json(), {"error": "access_denied"})
        self.assertNotIn(b"secret-bytes", denied.content)
        self._login("bob")
        self.assertEqual(self.client.get(f"/rounds/{round_id}").status_code, 403)
        self.store.grant_table[(round_id, USER_BOB)] = {"read"}
        visible = self.client.get(f"/rounds/{round_id}")
        self.assertEqual(visible.status_code, 200)
        finding = visible.json()["findings"][0]
        blocked = self.client.put(
            f"/rounds/{round_id}/reviews",
            json={"item_key": finding["item_key"], "judgment": "同意", "rationale": "x", "next_step": "y", "version": 0},
        )
        self.assertEqual(blocked.status_code, 403)

    def test_review_does_not_mutate_finding_and_conflicts(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{draft}",
            json={"rule_version_id": RULE_A, "items": [{"file_version_id": target["id"], "role": "target"}]},
        )
        round_id = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "rev"}).json()["id"]
        self._finish_worker()
        finding = self.client.get(f"/rounds/{round_id}").json()["findings"][0]
        machine = finding["status"]
        saved = self.client.put(
            f"/rounds/{round_id}/reviews",
            json={"item_key": finding["item_key"], "judgment": "需修改", "rationale": "字段不清", "next_step": "补传", "version": 0},
        )
        self.assertEqual(saved.status_code, 200)
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
            json={"rule_version_id": RULE_A, "items": [{"file_version_id": first["id"], "role": "target"}]},
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
                "items": [{"file_version_id": second["id"], "role": "target"}],
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
        old_bytes = self.client.get(
            f"/rounds/{old_id}/files/{first['file_id']}/versions/{first['id']}/download"
        )
        new_bytes = self.client.get(
            f"/rounds/{new_id}/files/{second['file_id']}/versions/{second['id']}/download"
        )
        self.assertEqual(old_bytes.content, b"version-one")
        self.assertEqual(new_bytes.content, b"version-two")

    def test_staging_cannot_be_accepted(self):
        self._login("alice")
        created = self.client.post(
            "/files", json={"original_filename": "a.bin", "content_type": "application/octet-stream", "byte_size": 4}
        )
        file_id, vid = created.json()["file_id"], created.json()["id"]
        self.client.put(f"/files/{file_id}/versions/{vid}/blob", content=b"wait")
        draft = self.client.post("/drafts").json()["id"]
        blocked = self.client.put(
            f"/drafts/{draft}",
            json={"rule_version_id": RULE_A, "items": [{"file_version_id": vid, "role": "target"}]},
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"], "file_not_ready")

    def test_late_attempt_does_not_overwrite(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{draft}",
            json={"rule_version_id": RULE_A, "items": [{"file_version_id": target["id"], "role": "target"}]},
        )
        round_id = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "fence"}).json()["id"]
        first = self.store.claim_job("w1")
        process_claimed(self.store, first, "w1")
        stale = first
        later = self.store.claim_job("w2")
        self.assertIsNone(later)
        from ppap_core.audits import ClaimedJob

        stale_write = ClaimedJob(stale.id, stale.kind, stale.payload, stale.attempt, stale.round_id)
        self.store.jobs[stale.id]["status"] = "leased"
        self.store.jobs[stale.id]["lease_owner"] = "late"
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


class PresenceEvaluationTests(unittest.TestCase):
    def test_presence_does_not_import_engine(self):
        from ppap_core.audits import RoundInput, RoundWork

        work = RoundWork(
            "r1",
            "t1",
            "资料就绪",
            {"kind": "presence"},
            [RoundInput("target", "f1", "v1", "a.xlsx", "ready")],
        )
        with patch.dict(os.environ, {}, clear=False):
            result = evaluate_round(work)
        self.assertEqual(result["findings"][0]["status"], "PASS")
        self.assertNotIn("ppap_excel_audit_engine", sys.modules)

    def test_round_payload_does_not_import_engine(self):
        result = run_claimed_job("audit", {"round_id": "abc"})
        self.assertEqual(result["note"], "round_audit")
        self.assertNotIn("ppap_excel_audit_engine.app_runtime", sys.modules)


class WebHookTests(unittest.TestCase):
    def test_workbench_hooks_exist(self):
        html = (ROOT / "apps" / "web" / "index.html").read_text()
        for marker in (
            'data-testid="submit-audit"',
            'data-testid="round-id"',
            'data-testid="findings"',
            'data-testid="save-review"',
            'data-testid="resubmit-audit"',
            'data-testid="isolated-dev-login"',
        ):
            self.assertIn(marker, html)
