"""Docs-addon host login through real PostgreSQL/Core, with controlled HTTP and guest OS boundaries."""

from __future__ import annotations

import json
import os
import unittest
import uuid
from unittest.mock import patch

import httpx
import pytest
from api import create_app
from documents.ai import AIResult
from fastapi.testclient import TestClient
from product.feishu import TOKEN_URL, USER_INFO_URL, exchange_host_code
from product.fixtures import RULE_A
from product.settings import Settings
from worker import process_claimed


def _clean_env(**extra: str):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("PPAP_", "FEISHU_"))}
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class _AddonMock:
    def __init__(self, workbook: bytes):
        self.workbook = workbook
        self.calls: list[tuple[str, str]] = []
        self.host_codes: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        method = request.method.upper()
        self.calls.append((method, url))
        auth = request.headers.get("authorization", "")
        if method == "POST" and url.startswith(TOKEN_URL):
            body = json.loads(request.content.decode())
            self.host_codes.append(str(body.get("code") or ""))
            if body.get("client_id") != "cli_test" or body.get("code") != "host-code":
                return httpx.Response(200, json={"code": 1, "msg": "bad code"})
            if "redirect_uri" in body:
                return httpx.Response(200, json={"code": 1, "msg": "unexpected redirect"})
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "access_token": "user-access",
                    "refresh_token": "user-refresh",
                    "expires_in": 7200,
                    "refresh_token_expires_in": 2592000,
                },
            )
        if method == "GET" and url.startswith(USER_INFO_URL):
            if auth != "Bearer user-access":
                return httpx.Response(401, json={"code": 1, "msg": "unauthorized"})
            return httpx.Response(
                200,
                json={"code": 0, "data": {"tenant_key": "tk-addon", "open_id": "ou_addon", "name": "Addon"}},
            )
        if auth != "Bearer user-access":
            return httpx.Response(401, json={"code": 99991663, "msg": "unauthorized"})
        if method == "GET" and "/docx/v1/documents/" in url and "/blocks" in url:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "block_type": 2,
                                "text": {
                                    "elements": [
                                        {
                                            "mention_doc": {
                                                "token": "xlsx-token",
                                                "title": "report.xlsx",
                                                "url": "https://foo.feishu.cn/file/xlsx-token",
                                                "obj_type": "file",
                                            }
                                        },
                                        {
                                            "mention_doc": {
                                                "token": "sheet-token",
                                                "title": "cloud-sheet",
                                                "url": "https://foo.feishu.cn/sheet/sheet-token",
                                                "obj_type": "sheet",
                                            }
                                        },
                                    ]
                                },
                            }
                        ]
                    },
                },
            )
        if method == "POST" and url.endswith("/drive/v1/metas/batch_query"):
            body = json.loads(request.content.decode())
            doc = body["request_docs"][0]
            token = doc["doc_token"]
            requested = doc["doc_type"]
            metas = {
                "xlsx-token": {"doc_type": "file", "title": "report.xlsx"},
                "sheet-token": {"doc_type": "sheet", "title": "cloud-sheet"},
            }
            meta = metas.get(token)
            if meta is None or meta["doc_type"] != requested:
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"metas": [], "failed_list": [{"doc_token": token}]}},
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"metas": [{"doc_token": token, "doc_type": meta["doc_type"], "title": meta["title"]}]},
                },
            )
        if method == "GET" and url.endswith("/drive/v1/files/xlsx-token/download"):
            return httpx.Response(
                200,
                content=self.workbook,
                headers={
                    "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "Content-Disposition": 'attachment; filename="report.xlsx"',
                },
            )
        if method == "GET" and "/partner_ai/v1/tenant_ai_items/" in url:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"tenant_ai_item": {"enabled": True, "user_quota": 80, "ai_package_purchased": True}},
                },
            )
        if method == "POST" and "/docx/v1/documents/" in url and url.endswith("/children"):
            return httpx.Response(200, json={"code": 0, "data": {}})
        return httpx.Response(404, json={"code": 1, "msg": "unmocked " + url})


class HostCodeTests(unittest.TestCase):
    def test_host_exchange_omits_redirect_uri(self):
        captured = {}

        class FakeHttp:
            def post(self, url, **kwargs):
                captured["url"] = url
                captured["json"] = kwargs.get("json")

                class R:
                    status_code = 200

                    def json(self):
                        return {
                            "code": 0,
                            "access_token": "u-host",
                            "refresh_token": "ur-host",
                            "expires_in": 7200,
                            "refresh_token_expires_in": 2592000,
                        }

                return R()

        with _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret"):
            settings = Settings()
        tokens = exchange_host_code(FakeHttp(), settings, "host-code")
        self.assertEqual(tokens.access_token, "u-host")
        self.assertEqual(captured["url"], TOKEN_URL)
        self.assertNotIn("redirect_uri", captured["json"])
        self.assertEqual(captured["json"]["code"], "host-code")


class FeishuDocsAddonTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory, core_system):
        self.core_system = core_system
        self.auth_store = auth_store_factory

    def setUp(self):
        self._env = _clean_env(
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
            FEISHU_AI_ITEM_ID="ai-item-1",
            PPAP_BUILD_SHA="a" * 40,
        )
        self._env.start()
        self.settings = Settings()
        self.store = self.auth_store(self.settings)
        self.store.seed_isolated_dev()
        self.core_system.install(self.store)
        self.core_system.adopt_presence(self.store)
        self.feishu = _AddonMock(self.core_system.workbook())
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

    def _usage_events(self):
        return self.store.list_usage_events(self.store.principal_for_fixture("alice"))

    def _submit_imported_file(self) -> str:
        """Import the linked workbook, start a task that writes back to the current document, and confirm it; return its round."""
        imported = self.client.post("/integrations/feishu/files/import", json={"file_token": "xlsx-token", "doc_type": "file"})
        self.assertEqual(imported.status_code, 201)
        imported_id = imported.json()["id"]
        source = self.store.file_source(imported_id)
        self.assertEqual(source["object_ref"], "xlsx-token")
        self.assertEqual(source["object_kind"], "file")
        validation = str(uuid.uuid4())
        checked = self.client.post("/file-validations", json={"request_id": validation, "source_file_id": imported.json()["file_id"], "source_file_version_id": imported_id})
        self.assertEqual(checked.status_code, 202)
        process_claimed(self.store, self.store.claim_job("test-worker"), "test-worker")
        file = {"file_version_id": imported_id, "validation_id": validation, "selection": "selected", "removal_reason": None, "preferred_rule_version_id": RULE_A}
        target = {"provider": "feishu", "object_kind": "docx", "object_ref": "current-doc"}
        task = self.client.post("/audit-tasks", json={"request_id": str(uuid.uuid4()), "customer_preference": "理想", "files": [file], "writeback_target": target})
        self.assertEqual(task.status_code, 202, task.text)
        # Recognition's model answers the chosen rule's document type.
        model = lambda operation, **_request: AIResult({"document_type": "资料就绪"} if operation == "document_classification" else {})  # noqa: E731
        process_claimed(self.store, self.store.claim_job("test-worker"), "test-worker", rule_ai=model)
        path = f"/audit-tasks/{task.json()['id']}"
        shown = self.client.get(path).json()
        command = {"request_id": str(uuid.uuid4()), "expected_revision": shown["input_revision"], "expected_confirmation_basis_digest": shown["confirmation_basis_digest"]}
        confirmed = self.client.post(path + "/confirm", json=command)
        self.assertEqual(confirmed.status_code, 201, confirmed.text)
        return confirmed.json()["round_id"]

    def test_host_code_creates_feishu_session_without_oauth_redirect(self):
        logged = self.client.post("/login/feishu/host", json={"code": "host-code"})
        self.assertEqual(logged.status_code, 200)
        self.assertEqual(logged.json()["identity"]["subject"], "ou_addon")
        self.assertEqual(logged.json()["identity"]["tenant_key"], "tk-addon")
        self.assertEqual(self.client.get("/me").json()["identity"]["subject"], "ou_addon")
        self.assertEqual(self.feishu.host_codes, ["host-code"])
        links = self.client.get("/integrations/feishu/docs/links", params={"file_token": "current-doc"})
        self.assertEqual(links.status_code, 200)
        self.assertEqual(links.json()["doc_links"][0]["token"], "xlsx-token")

    def test_addon_host_session_lists_links_submits_common_round_and_writeback(self):
        logged = self.client.post("/login/feishu/host", json={"code": "isolated-dev:alice"})
        self.assertEqual(logged.status_code, 200)
        self.assertEqual(logged.json()["identity"]["subject"], "ou_alice")
        links = self.client.get("/integrations/feishu/docs/links", params={"file_token": "current-doc"})
        self.assertEqual(links.status_code, 200)
        tokens = [row["token"] for row in links.json()["doc_links"]]
        self.assertEqual(tokens, ["xlsx-token", "sheet-token"])
        quota = self.client.get("/integrations/feishu/usage/quota", params={"target_count": 1})
        self.assertEqual(quota.status_code, 200)
        self.assertTrue(quota.json()["quota_sufficient"])
        self.assertEqual(quota.json()["estimated_points"], 500)
        self.assertTrue(quota.json()["accept_allowed"])
        round_id = self._submit_imported_file()
        claimed = self.store.claim_job("test-worker")
        self.assertIsNotNone(claimed)
        guest_calls = self.core_system.calls
        process_claimed(self.store, claimed, "test-worker")
        self.assertEqual(self.core_system.calls, guest_calls + 1)
        done = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(done["execution"], "completed")
        self.assertEqual(done["findings"][0]["status"], "PASS")
        self.assertTrue(done["writeback"]["available"])
        self.assertNotIn("current-doc", json.dumps(done))
        writeback = self.client.post(f"/integrations/feishu/rounds/{round_id}/writeback")
        self.assertEqual(writeback.status_code, 200, writeback.text)
        self.assertTrue(writeback.json()["inserted"])
        self.assertTrue(writeback.json()["return_url"].endswith(f"/tasks/{done['source_draft_id']}"))
        self.assertTrue(any("/drive/v1/files/xlsx-token/download" in url for _method, url in self.feishu.calls))
        self.assertTrue(any("/documents/current-doc/" in url for _method, url in self.feishu.calls))
        self.assertFalse(any("/documents/xlsx-token/" in url for _method, url in self.feishu.calls))

    def test_preview_acceptance_does_not_write_usage(self):
        self.settings.usage_charge_enabled = False
        logged = self.client.post("/login/feishu/host", json={"code": "isolated-dev:alice"})
        self.assertEqual(logged.status_code, 200)
        before = self._usage_events()
        quota = self.client.get("/integrations/feishu/usage/quota", params={"target_count": 1})
        self.assertEqual(quota.status_code, 200)
        self.assertEqual(quota.json()["estimated_points"], 0)
        self.assertTrue(quota.json()["accept_allowed"])
        self._submit_imported_file()
        self.assertEqual(self._usage_events(), before)

    def test_empty_code_and_isolated_dev_fixture_host_login(self):
        missing = self.client.post("/login/feishu/host", json={})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["error"], "invalid_host_code")
        fixture = self.client.post("/login/feishu/host", json={"code": "isolated-dev:alice"})
        self.assertEqual(fixture.status_code, 200)
        self.assertEqual(fixture.json()["identity"]["subject"], "ou_alice")
        me = self.client.get("/me")
        self.assertEqual(me.status_code, 200)
        links = self.client.get("/integrations/feishu/docs/links", params={"file_token": "current-doc"})
        self.assertEqual(links.status_code, 200)

    def test_production_rejects_isolated_dev_host_code(self):
        with _clean_env(
            PPAP_PROFILE="production",
            PPAP_PG_API_PASSWORD="prod-api",
            PPAP_PG_WORKER_PASSWORD="prod-worker",
            PPAP_PG_MIGRATOR_PASSWORD="prod-migrator",
            PPAP_PG_BOOTSTRAP_PASSWORD="prod-boot",
            PPAP_S3_ACCESS_KEY="prod-access",
            PPAP_S3_SECRET_KEY="prod-secret",
            PPAP_S3_ENDPOINT="https://objects.example.internal",
            PPAP_MODEL_TEXT_BASE_URL="https://llm.example",
            PPAP_MODEL_TEXT_MODEL="x",
            PPAP_WEB_URL="https://app.example",
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
        ):
            settings = Settings()
        client = TestClient(create_app(settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.auth_store(settings), self.http))
        response = client.post("/login/feishu/host", json={"code": "isolated-dev:alice"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_host_code")


if __name__ == "__main__":
    unittest.main()
