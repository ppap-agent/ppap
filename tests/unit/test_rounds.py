"""Accepted audit rounds, idempotency, and fencing. Not S01 or S04 Live."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages"), str(ROOT / "services" / "api"), str(ROOT / "services" / "worker")]

from fastapi.testclient import TestClient
from openpyxl import Workbook

from ppap_core.fixtures import CATALOG_PROJECT
from ppap_core.settings import Settings
from ppap_api import create_app
from tests.unit.memory_auth import MemoryAuthStore


def _clean_env(**extra: str):
    keep = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))
    }
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


def _xlsx(name: str) -> bytes:
    from io import BytesIO

    workbook = Workbook()
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class RoundHttpTests(unittest.TestCase):
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

    def _ready(self, filename: str) -> tuple[str, str]:
        body = _xlsx(filename)
        created = self.client.post(
            "/files",
            json={"original_filename": filename, "content_type": "application/octet-stream", "byte_size": len(body)},
        )
        file_id, version_id = created.json()["file_id"], created.json()["id"]
        self.client.put(f"/files/{file_id}/versions/{version_id}/blob", content=body)
        self.client.post(f"/files/{file_id}/versions/{version_id}/finalize")
        return file_id, version_id

    def _bound_draft(self) -> str:
        catalog = self.client.get(f"/rule-versions?project_id={CATALOG_PROJECT}").json()["items"]
        psw = next(item for item in catalog if item["engine_rule_id"] == "零件提交保证书")
        file_id, version_id = self._ready("PSW.xlsx")
        ref = self._ready("ref.xlsx")
        draft = self.client.post("/drafts", json={"project_id": CATALOG_PROJECT}).json()
        self.client.put(
            f"/drafts/{draft['id']}",
            json={
                "items": [
                    {
                        "file_id": file_id,
                        "file_version_id": version_id,
                        "role": "target",
                        "product_rule_version_id": psw["id"],
                    },
                    {"file_id": ref[0], "file_version_id": ref[1], "role": "reference"},
                ]
            },
        )
        return draft["id"]

    def test_accept_is_idempotent_and_conflicts_on_different_input(self):
        self._login("alice")
        draft_id = self._bound_draft()
        first = self.client.post(f"/drafts/{draft_id}/accept", json={"idempotency_key": "k1"})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["status"], "accepted")
        self.assertEqual(first.json()["execution_state"], "accepted")
        self.assertEqual(first.json()["visible_findings"], [])
        self.assertEqual(first.json()["coverage"]["reference_count"], 1)
        self.assertEqual(len(first.json()["jobs"]), 1)
        self.assertEqual(first.json()["jobs"][0]["kind"], "audit.execute")
        self.assertEqual([item["role"] for item in first.json()["inputs"]], ["target", "reference"])
        replay = self.client.post(f"/drafts/{draft_id}/accept", json={"idempotency_key": "k1"})
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["id"], first.json()["id"])
        self.assertEqual(len([job for job in self.store.jobs.values() if job["kind"] == "audit.execute"]), 1)
        other = self._ready("other.xlsx")
        catalog = self.client.get(f"/rule-versions?project_id={CATALOG_PROJECT}").json()["items"]
        psw = next(item for item in catalog if item["engine_rule_id"] == "零件提交保证书")
        self.client.put(
            f"/drafts/{draft_id}",
            json={
                "items": [
                    {
                        "file_id": other[0],
                        "file_version_id": other[1],
                        "role": "target",
                        "product_rule_version_id": psw["id"],
                    }
                ]
            },
        )
        conflict = self.client.post(f"/drafts/{draft_id}/accept", json={"idempotency_key": "k1"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json(), {"error": "idempotency_conflict"})
        resubmit = self.client.post(f"/drafts/{draft_id}/accept", json={"idempotency_key": "k2"})
        self.assertEqual(resubmit.status_code, 201)
        self.assertNotEqual(resubmit.json()["id"], first.json()["id"])
        again = self.client.get(f"/rounds/{first.json()['id']}")
        self.assertEqual(again.json()["id"], first.json()["id"])
        self.assertEqual(again.json()["inputs"][0]["original_filename"], "PSW.xlsx")

    def test_get_does_not_enqueue_and_unbound_target_is_rejected(self):
        self._login("alice")
        file_id, version_id = self._ready("PSW.xlsx")
        draft = self.client.post("/drafts", json={"project_id": CATALOG_PROJECT}).json()
        self.client.put(
            f"/drafts/{draft['id']}",
            json={"items": [{"file_id": file_id, "file_version_id": version_id, "role": "target"}]},
        )
        denied = self.client.post(f"/drafts/{draft['id']}/accept", json={"idempotency_key": "no-rule"})
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(denied.json()["error"], "rule_not_bound")
        draft_id = self._bound_draft()
        accepted = self.client.post(f"/drafts/{draft_id}/accept", json={"idempotency_key": "bound"})
        before = len(self.store.jobs)
        fetched = self.client.get(f"/rounds/{accepted.json()['id']}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(len(self.store.jobs), before)
        retry = self.client.post(f"/rounds/{accepted.json()['id']}/retry")
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(len(self.store.jobs), before)
        self.store.round_results[accepted.json()["id"]].append(
            {
                "id": "kept",
                "job_id": accepted.json()["jobs"][0]["id"],
                "attempt": 1,
                "file_version_id": accepted.json()["inputs"][0]["file_version_id"],
                "findings": [{"item": "kept-rule", "status": "NG"}],
            }
        )
        self.store.jobs[accepted.json()["jobs"][0]["id"]]["status"] = "failed"
        retried = self.client.post(f"/rounds/{accepted.json()['id']}/retry")
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(self.store.jobs[accepted.json()["jobs"][0]["id"]]["status"], "queued")
        self.assertEqual(retried.json()["results"][0]["id"], "kept")
        self.assertEqual(retried.json()["visible_findings"][0]["id"], "kept-rule")
        self.assertEqual(retried.json()["visible_findings"][0]["status"], "NG")
        self.assertEqual(retried.json()["inputs"][0]["original_filename"], "PSW.xlsx")

    def test_cross_tenant_round_denied_and_stale_complete_rejected(self):
        self._login("alice")
        draft_id = self._bound_draft()
        accepted = self.client.post(f"/drafts/{draft_id}/accept", json={"idempotency_key": "alice-round"})
        job_id = accepted.json()["jobs"][0]["id"]
        first = self.client.post("/isolated-dev/claim-job", json={"job_id": job_id, "worker": "stale-worker"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["attempt"], 1)
        self.assertEqual(accepted.json()["status"], "accepted")
        expired = self.client.post("/isolated-dev/expire-job-lease", json={"job_id": job_id})
        self.assertTrue(expired.json()["expired"])
        live = self.client.post("/isolated-dev/claim-job", json={"job_id": job_id, "worker": "live-worker"})
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json()["attempt"], 2)
        running = self.client.get(f"/rounds/{accepted.json()['id']}")
        self.assertEqual(running.json()["status"], "running")
        stale = self.client.post(
            "/isolated-dev/forge-job-result",
            json={"job_id": job_id, "worker": "stale-worker", "attempt": 1, "result": {"findings": [{"status": "PASS"}]}},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertFalse(stale.json()["applied"])
        held = self.client.post("/isolated-dev/claim-job", json={"job_id": job_id, "worker": "other-worker"})
        self.assertEqual(held.status_code, 409)
        self._login("carol")
        denied = self.client.get(f"/rounds/{accepted.json()['id']}")
        self.assertEqual(denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
