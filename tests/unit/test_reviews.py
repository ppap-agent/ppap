"""Human reviews do not mutate machine findings. Not S05 Live."""
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

from ppap_core.fixtures import CATALOG_PROJECT, USER_ALICE, USER_BOB
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


class ReviewHttpTests(unittest.TestCase):
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

    def _round(self) -> dict:
        catalog = self.client.get(f"/rule-versions?project_id={CATALOG_PROJECT}").json()["items"]
        psw = next(item for item in catalog if item["engine_rule_id"] == "零件提交保证书")
        file_id, version_id = self._ready("PSW.xlsx")
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
                    }
                ]
            },
        )
        accepted = self.client.post(f"/drafts/{draft['id']}/accept", json={"idempotency_key": "review-round"})
        self.assertEqual(accepted.status_code, 201)
        return accepted.json()

    def test_review_persists_without_changing_machine_results(self):
        self._login("alice")
        accepted = self._round()
        machine = list(accepted["results"])
        saved = self.client.post(
            f"/rounds/{accepted['id']}/reviews",
            json={"judgment": "questioned", "reason": "need a second look", "next_action": "核对尺寸栏", "actor_user_id": USER_BOB},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["results"], machine)
        self.assertEqual(saved.json()["reviews"][0]["reason"], "need a second look")
        self.assertEqual(saved.json()["reviews"][0]["actor_user_id"], USER_ALICE)
        self.assertNotEqual(saved.json()["reviews"][0]["actor_user_id"], USER_BOB)
        fetched = self.client.get(f"/rounds/{accepted['id']}")
        self.assertEqual(fetched.json()["reviews"][0]["judgment"], "questioned")
        conflict = self.client.post(
            f"/rounds/{accepted['id']}/reviews",
            json={"judgment": "noted", "reason": "overwrite", "next_action": "none", "expected_version": 1},
        )
        stale = self.client.post(
            f"/rounds/{accepted['id']}/reviews",
            json={"judgment": "noted", "reason": "stale", "next_action": "none", "expected_version": 1},
        )
        self.assertEqual(conflict.status_code, 200)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json(), {"error": "review_conflict"})
        resubmit = self.client.post(f"/drafts/{accepted['draft_id']}/accept", json={"idempotency_key": "new-round"})
        self.assertNotEqual(resubmit.json()["id"], accepted["id"])
        self.assertEqual(resubmit.json()["reviews"], [])
        old = self.client.get(f"/rounds/{accepted['id']}")
        self.assertEqual(old.json()["reviews"][0]["reason"], "overwrite")
        self._login("bob")
        self.store.grant_table.setdefault((accepted["id"], USER_BOB), set()).add("read")
        denied = self.client.post(
            f"/rounds/{accepted['id']}/reviews",
            json={"judgment": "noted", "reason": "bob", "next_action": "none"},
        )
        self.assertEqual(denied.status_code, 403)
        self._login("carol")
        hidden = self.client.get(f"/rounds/{accepted['id']}")
        self.assertEqual(hidden.status_code, 403)


if __name__ == "__main__":
    unittest.main()
