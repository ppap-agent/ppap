"""Write common round findings back into the source Feishu doc. Mocked HTTP only."""
from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages"), str(ROOT / "services" / "api"), str(ROOT / "services" / "worker")]

from fastapi.testclient import TestClient

from ppap_core.feishu import UserTokens
from ppap_core.feishu_writeback import comment_text, history_text, structured_blocks, writeback_state
from ppap_core.fixtures import RULE_A
from ppap_core.identity import SESSION_COOKIE
from ppap_core.settings import Settings
from ppap_api import create_app
from ppap_worker import process_claimed
from tests.unit.memory_auth import MemoryAuthStore


def _clean_env(**extra: str):
    keep = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))
    }
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class HistorySemanticsTests(unittest.TestCase):
    def test_pass_round_writes_result_and_return_url(self):
        body = {
            "id": "round-1",
            "execution": "completed",
            "created_at": "2026-09-19T12:00:00+00:00",
            "inputs": [{"role": "target", "original_filename": "report.xlsx"}],
            "findings": [
                {
                    "item": "封面",
                    "status": "PASS",
                    "suggestions": "",
                    "locator": {"filename": "report.xlsx"},
                }
            ],
        }
        text = history_text(body, "http://127.0.0.1:4173/?round=round-1")
        self.assertIn("审核时间：2026 年 09 月 19 日 12:00", text)
        self.assertIn("**审核内容1：report.xlsx**", text)
        self.assertIn("审核结果：PASS", text)
        self.assertIn("回访：http://127.0.0.1:4173/?round=round-1", text)
        self.assertEqual(writeback_state(body), "ready")
        self.assertEqual(comment_text(body), "")
        types = [block["block_type"] for block in structured_blocks(text)]
        self.assertEqual(types[:3], [4, 5, 2])

    def test_ng_round_writes_summary_and_comment(self):
        body = {
            "id": "round-2",
            "execution": "completed",
            "inputs": [{"role": "target", "original_filename": "plan.xlsx"}],
            "findings": [
                {
                    "item": "零件号",
                    "status": "NG",
                    "suggestions": "与封面不一致",
                    "locator": {"filename": "plan.xlsx"},
                }
            ],
        }
        text = history_text(body)
        self.assertIn("总结：", text)
        self.assertIn("零件号 : 与封面不一致", text)
        self.assertNotIn("审核结果：PASS", text)
        self.assertEqual(comment_text(body), "总结：\n零件号：与封面不一致")

    def test_err_and_incomplete_are_not_ready(self):
        incomplete = {"execution": "queued", "findings": [{"status": "PASS"}]}
        self.assertEqual(writeback_state(incomplete), "round_incomplete")
        errored = {
            "execution": "completed",
            "findings": [{"status": "ERR", "suggestions": "engine_error"}],
        }
        self.assertEqual(writeback_state(errored), "round_has_errors")


class _WritebackMock:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_insert = False
        self.fail_comment = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        method = request.method.upper()
        payload = json.loads(request.content.decode()) if request.content else {}
        self.calls.append((method, url, payload))
        if request.headers.get("authorization") != "Bearer user-access":
            return httpx.Response(401, json={"code": 99991663, "msg": "unauthorized"})
        if method == "POST" and "/docx/v1/documents/" in url and url.endswith("/children"):
            if self.fail_insert:
                return httpx.Response(500, json={"code": 1, "msg": "insert failed"})
            if payload.get("index") != 0 or not payload.get("children"):
                return httpx.Response(400, json={"code": 1, "msg": "bad children"})
            return httpx.Response(200, json={"code": 0, "data": {}})
        if method == "POST" and "/drive/v1/files/" in url and "/comments" in url:
            if self.fail_comment:
                return httpx.Response(500, json={"code": 1, "msg": "comment failed"})
            return httpx.Response(200, json={"code": 0, "data": {}})
        return httpx.Response(404, json={"code": 1, "msg": "unmocked"})


class FeishuWritebackTests(unittest.TestCase):
    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings()
        self.store = MemoryAuthStore(self.settings)
        self.store.seed_isolated_dev()
        self.feishu = _WritebackMock()
        self.http = httpx.Client(transport=httpx.MockTransport(self.feishu.handler))
        self.client = TestClient(
            create_app(
                self.settings,
                lambda _s: {"state": "ok"},
                lambda _s: {"state": "ok"},
                self.store,
                self.http,
            )
        )

    def tearDown(self):
        self.http.close()
        self._env.stop()

    def _login_fixture(self, subject: str) -> None:
        self.client.cookies.clear()
        response = self.client.post("/isolated-dev/sessions", json={"subject": subject})
        self.assertEqual(response.status_code, 200)

    def _login_feishu_user(self, subject: str = "alice") -> None:
        self.client.cookies.clear()
        principal = self.store.principal_for_fixture(subject)
        now = datetime.now(timezone.utc)
        tokens = UserTokens("user-access", "user-refresh", now + timedelta(hours=1), now + timedelta(days=7))
        raw = self.store.create_session(principal, tokens)
        self.client.cookies.set(SESSION_COOKIE, raw)

    def _upload(self, name: str, body: bytes) -> dict:
        meta = {"original_filename": name, "content_type": "application/octet-stream", "byte_size": len(body)}
        created = self.client.post("/files", json=meta)
        self.assertEqual(created.status_code, 201)
        file_id, vid = created.json()["file_id"], created.json()["id"]
        self.assertEqual(self.client.put(f"/files/{file_id}/versions/{vid}/blob", content=body).status_code, 200)
        ready = self.client.post(f"/files/{file_id}/versions/{vid}/finalize")
        self.assertEqual(ready.status_code, 200)
        return ready.json()

    def _complete_round(self, filename: str = "report.xlsx") -> str:
        version = self._upload(filename, b"target-bytes")
        draft = self.client.post("/drafts").json()["id"]
        saved = self.client.put(
            f"/drafts/{draft}",
            json={"rule_version_id": RULE_A, "items": [{"file_version_id": version["id"], "role": "target"}]},
        )
        self.assertEqual(saved.status_code, 200)
        accepted = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "feishu-writeback-1"})
        self.assertEqual(accepted.status_code, 201)
        claimed = self.store.claim_job("test-worker")
        self.assertIsNotNone(claimed)
        process_claimed(self.store, claimed, "test-worker")
        return accepted.json()["id"]

    def test_fixture_session_cannot_writeback_without_user_token(self):
        self._login_feishu_user()
        round_id = self._complete_round()
        self._login_fixture("alice")
        response = self.client.post(
            f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "feishu_user_token_missing")

    def test_pass_round_inserts_history_and_return_url(self):
        self._login_feishu_user()
        round_id = self._complete_round()
        response = self.client.post(
            f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["inserted"])
        self.assertFalse(body["commented"])
        self.assertEqual(body["round_id"], round_id)
        self.assertEqual(body["return_url"], f"http://127.0.0.1:4173/?round={round_id}")
        insert = next(call for call in self.feishu.calls if call[0] == "POST" and call[1].endswith("/children"))
        self.assertIn("/documents/docx-token/blocks/docx-token/children", insert[1])
        self.assertEqual(insert[2]["index"], 0)
        dumped = json.dumps(insert[2]["children"], ensure_ascii=False)
        self.assertIn("审核结果：PASS", dumped)
        self.assertIn(body["return_url"], dumped)
        self.assertFalse(any("/comments" in call[1] for call in self.feishu.calls))

    def test_ng_round_comments_and_err_does_not_write(self):
        self._login_feishu_user()
        round_id = self._complete_round("plan.xlsx")
        for row in self.store.findings[round_id].values():
            row["status"] = "NG"
            row["suggestions"] = "缺项"
            row["item"] = "零件号"
        response = self.client.post(
            f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["commented"])
        comment = next(call for call in self.feishu.calls if "/comments" in call[1])
        self.assertIn("docx-token", comment[1])
        self.assertIn("缺项", json.dumps(comment[2], ensure_ascii=False))

        self.feishu.calls.clear()
        for row in self.store.findings[round_id].values():
            row["status"] = "ERR"
        blocked = self.client.post(
            f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"}
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"], "round_has_errors")
        self.assertEqual(self.feishu.calls, [])

    def test_incomplete_and_cross_tenant_and_insert_failure(self):
        self._login_feishu_user()
        version = self._upload("queued.xlsx", b"bytes")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{draft}",
            json={"rule_version_id": RULE_A, "items": [{"file_version_id": version["id"], "role": "target"}]},
        )
        queued = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "feishu-writeback-queued"})
        queued_id = queued.json()["id"]
        incomplete = self.client.post(
            f"/integrations/feishu/rounds/{queued_id}/writeback", json={"file_token": "docx-token"}
        )
        self.assertEqual(incomplete.status_code, 409)
        self.assertEqual(incomplete.json()["error"], "round_incomplete")
        claimed = self.store.claim_job("test-worker")
        self.assertIsNotNone(claimed)
        process_claimed(self.store, claimed, "test-worker")

        round_id = self._complete_round()
        self.feishu.fail_insert = True
        failed = self.client.post(
            f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"}
        )
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(failed.json()["error"], "feishu_writeback_failed")

        self.feishu.fail_insert = False
        self._login_feishu_user("carol")
        denied = self.client.post(
            f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"}
        )
        self.assertEqual(denied.status_code, 403)

    def test_comment_failure_still_inserts(self):
        self._login_feishu_user()
        round_id = self._complete_round("plan.xlsx")
        for row in self.store.findings[round_id].values():
            row["status"] = "NG"
            row["suggestions"] = "缺项"
            row["item"] = "零件号"
        self.feishu.fail_comment = True
        response = self.client.post(
            f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["inserted"])
        self.assertFalse(response.json()["commented"])


if __name__ == "__main__":
    unittest.main()
