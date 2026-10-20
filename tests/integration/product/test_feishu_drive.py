"""Feishu Drive/Docs import into common file versions. Mocked HTTP only."""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from ppap_api import create_app
from ppap_core.feishu import UserTokens
from ppap_core.feishu_drive import (
    RETRY_SLEEP_MS,
    Attempt,
    FetchPlan,
    PermanentFailure,
    export_tasks_url,
    files_download_url,
    import_drive_material,
    retry,
)
from ppap_core.fixtures import RULE_A
from ppap_core.identity import SESSION_COOKIE
from ppap_core.settings import Settings
from ppap_worker import process_claimed


def _clean_env(**extra: str):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))}
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class FetchPlanTests(unittest.TestCase):
    def test_xlsx_drive_file_calls_only_files_download(self):
        plan = FetchPlan.for_doc_type("file")
        urls = plan.main_fetch_urls("xlsx-token")
        self.assertEqual(urls, [files_download_url("xlsx-token")])
        self.assertTrue(all("export_tasks" not in url for url in urls))
        self.assertTrue(all("/blocks" not in url for url in urls))

    def test_sheet_exports_and_does_not_download_drive_file(self):
        plan = FetchPlan.for_doc_type("sheet")
        urls = plan.main_fetch_urls("sheet-token")
        self.assertEqual(urls, [export_tasks_url()])
        self.assertTrue(all("/files/sheet-token/download" not in url for url in urls))
        self.assertTrue(all("/blocks" not in url for url in urls))

    def test_old_doc_exports_without_docx_blocks(self):
        urls = FetchPlan.for_doc_type("doc").main_fetch_urls("doc-token")
        self.assertEqual(urls, [export_tasks_url()])
        self.assertTrue(all("/blocks" not in url for url in urls))

    def test_docx_may_scan_blocks_then_export(self):
        urls = FetchPlan.for_doc_type("docx").main_fetch_urls("docx-token")
        self.assertEqual(
            urls,
            [
                "https://open.feishu.cn/open-apis/docx/v1/documents/docx-token/blocks",
                "https://open.feishu.cn/open-apis/drive/v1/export_tasks",
            ],
        )

    def test_missing_meta_type_is_rejected_instead_of_guessing_a_download(self):
        with self.assertRaises(ValueError):
            FetchPlan.for_doc_type("")
        with self.assertRaises(ValueError):
            FetchPlan.for_doc_type(None)
        with self.assertRaises(ValueError):
            FetchPlan.for_doc_type("folder")


class DriveDownloadTests(unittest.TestCase):
    def test_transient_http_then_success_completes_in_one_retry_loop(self):
        calls = {"n": 0}

        def factory(attempt):
            del attempt
            calls["n"] += 1
            if calls["n"] == 1:
                return Attempt.http(500, '{"code":500}')
            if calls["n"] == 2:
                return Attempt.http(429, "rate limit")
            return Attempt.ok("bytes")

        body = retry(factory, 3, 0, lambda _ms: None)
        self.assertEqual(body, "bytes")
        self.assertEqual(calls["n"], 3)

    def test_permanent_404_does_not_consume_later_success(self):
        calls = {"n": 0}

        def factory(attempt):
            del attempt
            calls["n"] += 1
            if calls["n"] == 1:
                return Attempt.http(404, '{"code":99912345,"msg":"not found"}')
            return Attempt.ok("should-not-run")

        with self.assertRaises(PermanentFailure) as raised:
            retry(factory, 3, 0, lambda _ms: None)
        self.assertEqual(calls["n"], 1)
        message = str(raised.exception)
        self.assertIn("HTTP 404", message)
        self.assertTrue("99912345" in message or "not found" in message)
        self.assertNotIn("所有下载方式均失败", message)
        self.assertEqual(RETRY_SLEEP_MS, 2000)


class _DriveMock:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.drive_attempts = 0
        self.fail_drive_times = 0
        self.meta_by_token = {
            "xlsx-token": {"doc_type": "file", "title": "report.xlsx"},
            "sheet-token": {"doc_type": "sheet", "title": "cloud-sheet"},
            "folder-token": {"doc_type": "folder", "title": "a-folder"},
        }
        self.drive_bytes = {"xlsx-token": b"target-bytes"}
        self.export_bytes = {"sheet-token": b"exported-xlsx"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        method = request.method.upper()
        self.calls.append((method, url))
        auth = request.headers.get("authorization", "")
        if auth != "Bearer user-access":
            return httpx.Response(401, json={"code": 99991663, "msg": "unauthorized"})
        if method == "POST" and url.endswith("/drive/v1/metas/batch_query"):
            body = json.loads(request.content.decode())
            doc = body["request_docs"][0]
            token = doc["doc_token"]
            requested = doc["doc_type"]
            meta = self.meta_by_token.get(token)
            if meta is None or meta["doc_type"] != requested:
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"metas": [], "failed_list": [{"doc_token": token}]}},
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "metas": [
                            {
                                "doc_token": token,
                                "doc_type": meta["doc_type"],
                                "title": meta["title"],
                            }
                        ]
                    },
                },
            )
        if method == "GET" and "/drive/v1/files/" in url and url.endswith("/download"):
            token = url.rsplit("/files/", 1)[1].split("/download", 1)[0]
            self.drive_attempts += 1
            if self.drive_attempts <= self.fail_drive_times:
                return httpx.Response(500, json={"code": 500})
            data = self.drive_bytes.get(token)
            if data is None:
                return httpx.Response(404, json={"code": 99912345, "msg": "not found"})
            return httpx.Response(
                200,
                content=data,
                headers={
                    "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "Content-Disposition": 'attachment; filename="report.xlsx"',
                },
            )
        if method == "POST" and url.endswith("/drive/v1/export_tasks"):
            body = json.loads(request.content.decode())
            token = body["token"]
            if token not in self.export_bytes:
                return httpx.Response(200, json={"code": 1, "msg": "cannot export"})
            return httpx.Response(200, json={"code": 0, "data": {"ticket": "tik-" + token}})
        if method == "GET" and "/drive/v1/export_tasks/" in url and "token=" in url:
            token = url.split("token=", 1)[1]
            return httpx.Response(
                200,
                json={"code": 0, "data": {"job_status": 0, "result": {"file_token": "exp-" + token}}},
            )
        if method == "GET" and "/drive/v1/export_tasks/file/" in url and url.endswith("/download"):
            exported = url.rsplit("/file/", 1)[1].split("/download", 1)[0]
            original = exported.removeprefix("exp-")
            data = self.export_bytes.get(original)
            if data is None:
                return httpx.Response(404, json={"code": 1, "msg": "missing export"})
            return httpx.Response(
                200,
                content=data,
                headers={
                    "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "Content-Disposition": "attachment; filename*=UTF-8''cloud-sheet.xlsx",
                },
            )
        return httpx.Response(404, json={"code": 1, "msg": "unmocked " + url})


class FeishuImportTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory):
        self.auth_store = auth_store_factory

    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings(usage_charge_enabled=False)
        self.store = self.auth_store(self.settings)
        self.store.seed_isolated_dev()
        self.feishu = _DriveMock()
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
        tokens = UserTokens(
            "user-access",
            "user-refresh",
            now + timedelta(hours=1),
            now + timedelta(days=7),
        )
        raw = self.store.create_session(principal, tokens)
        self.client.cookies.set(SESSION_COOKIE, raw)

    def test_fixture_session_cannot_import_without_user_token(self):
        self._login_fixture("alice")
        response = self.client.post("/integrations/feishu/files/import", json={"file_token": "xlsx-token", "doc_type": "file"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "feishu_user_token_missing")

    def test_unauthenticated_import_rejected(self):
        self.client.cookies.clear()
        response = self.client.post("/integrations/feishu/files/import", json={"file_token": "xlsx-token"})
        self.assertEqual(response.status_code, 401)

    def test_folder_meta_is_rejected(self):
        self._login_feishu_user()
        response = self.client.post("/integrations/feishu/files/import", json={"file_token": "folder-token", "doc_type": "folder"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "unsupported_doc_type")

    def test_drive_file_import_becomes_common_version_and_round(self):
        self._login_feishu_user("alice")
        imported = self.client.post("/integrations/feishu/files/import", json={"file_token": "xlsx-token", "doc_type": "file"})
        self.assertEqual(imported.status_code, 201)
        version = imported.json()
        self.assertEqual(version["status"], "ready")
        self.assertEqual(version["original_filename"], "report.xlsx")
        self.assertEqual(version["byte_size"], len(b"target-bytes"))
        self.assertEqual(self.store.file_source(version["id"])["object_ref"], "xlsx-token")
        self.assertTrue(version["origin"]["available"])
        self.assertEqual(version["origin"]["object_kind"], "file")
        self.assertNotIn("object_ref", json.dumps(version))
        self.assertNotIn("xlsx-token", json.dumps(version))
        gotten = self.client.get(f"/files/{version['file_id']}/versions/{version['id']}")
        self.assertEqual(gotten.status_code, 200)
        self.assertTrue(gotten.json()["origin"]["available"])
        self.assertNotIn("xlsx-token", json.dumps(gotten.json()))
        self.assertTrue(any("/drive/v1/files/xlsx-token/download" in url for _method, url in self.feishu.calls))
        self.assertFalse(any("export_tasks" in url for _method, url in self.feishu.calls))
        draft = self.client.post("/drafts").json()["id"]
        saved = self.client.put(
            f"/drafts/{draft}",
            json={"items": [{"file_version_id": version["id"], "role": "target", "rule_version_id": RULE_A}]},
        )
        self.assertEqual(saved.status_code, 200)
        accepted = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "feishu-import-1"})
        self.assertEqual(accepted.status_code, 201)
        claimed = self.store.claim_job("test-worker")
        self.assertIsNotNone(claimed)
        process_claimed(self.store, claimed, "test-worker")
        round_id = accepted.json()["id"]
        body = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(body["execution"], "completed")
        original = body["findings"][0]
        download = self.client.get(f"/rounds/{round_id}/files/{original['locator']['file_id']}/versions/{original['locator']['file_version_id']}/download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, b"target-bytes")
        self._login_fixture("carol")
        denied = self.client.get(f"/files/{version['file_id']}/versions/{version['id']}/download")
        self.assertEqual(denied.status_code, 403)

    def test_sheet_export_import(self):
        self._login_feishu_user("alice")
        imported = self.client.post("/integrations/feishu/files/import", json={"file_token": "sheet-token", "doc_type": "sheet"})
        self.assertEqual(imported.status_code, 201)
        version = imported.json()
        self.assertEqual(version["original_filename"], "cloud-sheet.xlsx")
        self.assertEqual(version["byte_size"], len(b"exported-xlsx"))
        self.assertTrue(any(method == "POST" and url.endswith("/drive/v1/export_tasks") for method, url in self.feishu.calls))
        self.assertFalse(any("/files/sheet-token/download" in url for _method, url in self.feishu.calls))
        downloaded = self.client.get(f"/files/{version['file_id']}/versions/{version['id']}/download")
        self.assertEqual(downloaded.content, b"exported-xlsx")

    def test_import_drive_material_retries_transient_download(self):
        self.feishu.fail_drive_times = 2
        material = import_drive_material(
            self.http,
            "user-access",
            "xlsx-token",
            "file",
            sleeper=lambda _ms: None,
            sleep_ms=0,
        )
        self.assertEqual(material.data, b"target-bytes")
        self.assertEqual(self.feishu.drive_attempts, 3)

    def test_imported_targets_and_references_use_common_draft_and_replay(self):
        self._login_feishu_user("alice")
        target_import = self.client.post("/integrations/feishu/files/import", json={"file_token": "xlsx-token", "doc_type": "file"})
        reference_import = self.client.post("/integrations/feishu/files/import", json={"file_token": "sheet-token", "doc_type": "sheet"})
        self.assertEqual(target_import.status_code, 201)
        self.assertEqual(reference_import.status_code, 201)
        draft = self.client.post("/drafts").json()["id"]
        saved = self.client.put(
            f"/drafts/{draft}",
            json={
                "items": [
                    {"file_version_id": target_import.json()["id"], "role": "target", "rule_version_id": RULE_A},
                    {"file_version_id": reference_import.json()["id"], "role": "reference"},
                ]
            },
        )
        self.assertEqual(saved.status_code, 200)
        first = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "feishu-initiate-1"})
        self.assertEqual(first.status_code, 201)
        body = first.json()
        roles = {row["role"]: row["original_filename"] for row in body["inputs"]}
        self.assertEqual(roles["target"], "report.xlsx")
        self.assertEqual(roles["reference"], "cloud-sheet.xlsx")
        self.assertTrue(any(row["role"] == "target" for row in body["inputs"]))
        downloads_before = self.feishu.drive_attempts
        export_posts = sum(1 for method, url in self.feishu.calls if method == "POST" and url.endswith("/drive/v1/export_tasks"))
        self.assertEqual(export_posts, 1)
        replay = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "feishu-initiate-1"})
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json()["id"], body["id"])
        self.assertEqual(self.feishu.drive_attempts, downloads_before)
        self.assertEqual(sum(method == "POST" and url.endswith("/drive/v1/export_tasks") for method, url in self.feishu.calls), export_posts)
        claimed = self.store.claim_job("test-worker")
        self.assertIsNotNone(claimed)
        process_claimed(self.store, claimed, "test-worker")
        round_id = body["id"]
        done = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(done["execution"], "completed")
        target = next(row for row in done["inputs"] if row["role"] == "target")
        original = self.client.get(f"/rounds/{round_id}/files/{target['file_id']}/versions/{target['file_version_id']}/download")
        self.assertEqual(original.content, b"target-bytes")
        self._login_fixture("carol")
        denied = self.client.get(f"/rounds/{round_id}")
        self.assertEqual(denied.status_code, 403)

    def test_rule_required_at_accept_and_user_token_required_for_import(self):
        self._login_feishu_user("alice")
        imported = self.client.post("/integrations/feishu/files/import", json={"file_token": "xlsx-token", "doc_type": "file"})
        self.assertEqual(imported.status_code, 201)
        draft = self.client.post("/drafts").json()["id"]
        saved = self.client.put(
            f"/drafts/{draft}",
            json={
                "items": [
                    {"file_version_id": imported.json()["id"], "role": "target"},
                ]
            },
        )
        self.assertEqual(saved.status_code, 200)
        missing_rule = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "k"})
        self.assertEqual(missing_rule.status_code, 400)
        self.assertEqual(missing_rule.json()["error"], "rule_required")
        self._login_fixture("alice")
        fixture = self.client.post("/integrations/feishu/files/import", json={"file_token": "xlsx-token", "doc_type": "file"})
        self.assertEqual(fixture.status_code, 409)
        self.assertEqual(fixture.json()["error"], "feishu_user_token_missing")


if __name__ == "__main__":
    unittest.main()
