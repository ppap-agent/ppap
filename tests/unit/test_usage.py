"""Usage events are observations, not invented balances. Not S07 Live."""
from __future__ import annotations

import json
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


class UsageHttpTests(unittest.TestCase):
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

    def _accept(self, key: str) -> dict:
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
        accepted = self.client.post(f"/drafts/{draft['id']}/accept", json={"idempotency_key": key})
        self.assertEqual(accepted.status_code, 201)
        return accepted.json()

    def test_accept_records_observation_without_balance_or_charge(self):
        self._login("alice")
        accepted = self._accept("usage-round")
        listed = self.client.get("/usage")
        self.assertEqual(listed.status_code, 200)
        payload = listed.json()
        blob = json.dumps(payload)
        self.assertNotIn("balance", blob)
        self.assertNotIn("quota", blob)
        self.assertNotIn('"amount"', blob)
        self.assertNotIn("10000", blob)
        self.assertEqual(payload["partner_ai"], "disabled")
        self.assertEqual(len(payload["items"]), 1)
        event = payload["items"][0]
        self.assertEqual(event["round_id"], accepted["id"])
        self.assertEqual(event["state"], "observed")
        self.assertFalse(event["charged"])
        self.assertNotIn("amount", event)
        self.assertEqual(event["business_key"], f"audit_round:{accepted['id']}")
        self.assertEqual(accepted["usage"]["id"], event["id"])
        replay = self.client.post(
            f"/drafts/{accepted['draft_id']}/accept", json={"idempotency_key": "usage-round"}
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(len(self.client.get("/usage").json()["items"]), 1)
        self.client.post(f"/rounds/{accepted['id']}/retry")
        self.assertEqual(len(self.client.get("/usage").json()["items"]), 1)
        forged = self.client.post("/usage", json={"amount": 500, "charged": True})
        self.assertIn(forged.status_code, {404, 405, 422})
        reconcile = self.client.post(
            f"/usage/{event['id']}/reconcile", json={"state": "confirmed", "amount": 500}
        )
        self.assertEqual(reconcile.status_code, 200)
        self.assertEqual(reconcile.json()["id"], event["id"])
        self.assertEqual(reconcile.json()["business_key"], event["business_key"])
        self.assertEqual(reconcile.json()["state"], "reconciling")
        self.assertFalse(reconcile.json()["charged"])
        again = self.client.post(f"/usage/{event['id']}/reconcile")
        self.assertEqual(again.json()["id"], event["id"])
        self.assertEqual(len(self.client.get("/usage").json()["items"]), 1)
        resubmit = self.client.post(
            f"/drafts/{accepted['draft_id']}/accept", json={"idempotency_key": "usage-new"}
        )
        self.assertEqual(resubmit.status_code, 201)
        items = self.client.get("/usage").json()["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual({item["round_id"] for item in items}, {accepted["id"], resubmit.json()["id"]})
        self._login("bob")
        bob = self.client.get("/usage")
        self.assertEqual(bob.status_code, 200)
        self.assertEqual({item["round_id"] for item in bob.json()["items"]}, {accepted["id"], resubmit.json()["id"]})
        self._login("carol")
        hidden = self.client.get("/usage")
        self.assertEqual(hidden.status_code, 200)
        self.assertEqual(hidden.json()["items"], [])
        denied = self.client.post(f"/usage/{event['id']}/reconcile")
        self.assertEqual(denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
