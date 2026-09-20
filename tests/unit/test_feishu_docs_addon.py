"""Docs-addon host login through the common Feishu round. Mocked HTTP only."""
from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx

from tests import pathsetup

ROOT = pathsetup.ROOT

from fastapi.testclient import TestClient

from ppap_core.feishu import TOKEN_URL, USER_INFO_URL, exchange_host_code
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


class _AddonMock:
    def __init__(self):
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
                content=b"target-bytes",
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
    def setUp(self):
        self._env = _clean_env(
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
            FEISHU_AI_ITEM_ID="ai-item-1",
            PPAP_BUILD_SHA="a" * 40,
        )
        self._env.start()
        self.settings = Settings()
        self.store = MemoryAuthStore(self.settings)
        self.store.seed_isolated_dev()
        self.feishu = _AddonMock()
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

    def _draft_for_imported_file(self):
        imported = self.client.post(
            "/integrations/feishu/files/import", json={"file_token": "xlsx-token", "doc_type": "file"}
        )
        self.assertEqual(imported.status_code, 201)
        created = self.client.post("/drafts")
        self.assertEqual(created.status_code, 201)
        draft = created.json()["id"]
        saved = self.client.put(f"/drafts/{draft}", json={"items": [
            {"file_version_id": imported.json()["id"], "role": "target", "rule_version_id": RULE_A},
        ]})
        self.assertEqual(saved.status_code, 200)
        return draft

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
        quota = self.client.get("/integrations/feishu/usage/quota")
        self.assertEqual(quota.status_code, 200)
        self.assertTrue(quota.json()["quota_sufficient"])
        draft = self._draft_for_imported_file()
        submitted = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "addon-submit-1"})
        self.assertEqual(submitted.status_code, 201)
        round_id = submitted.json()["id"]
        claimed = self.store.claim_job("test-worker")
        self.assertIsNotNone(claimed)
        process_claimed(self.store, claimed, "test-worker")
        done = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(done["execution"], "completed")
        self.assertEqual(done["findings"][0]["status"], "PASS")
        writeback = self.client.post(
            f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "current-doc"}
        )
        self.assertEqual(writeback.status_code, 200)
        self.assertTrue(writeback.json()["inserted"])
        self.assertIn(f"/?round={round_id}", writeback.json()["return_url"])
        self.assertTrue(any("/drive/v1/files/xlsx-token/download" in url for _method, url in self.feishu.calls))

    def test_feishu_audit_records_trial_usage_once_per_round(self):
        logged = self.client.post("/login/feishu/host", json={"code": "isolated-dev:alice"})
        self.assertEqual(logged.status_code, 200)
        before = self.client.get("/integrations/feishu/usage/quota").json()
        self.assertEqual(before["remaining_trial_ai_points"], 10000)
        draft = self._draft_for_imported_file()
        submitted = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "addon-usage-1"})
        self.assertEqual(submitted.status_code, 201)
        after = self.client.get("/integrations/feishu/usage/quota").json()
        self.assertEqual(after["remaining_trial_ai_points"], 9500)
        replay = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "addon-usage-1"})
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json()["id"], submitted.json()["id"])
        still = self.client.get("/integrations/feishu/usage/quota").json()
        self.assertEqual(still["remaining_trial_ai_points"], 9500)
        events = [
            row
            for row in self.store.usage_events
            if row["trace_id"] == submitted.json()["id"]
        ]
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["is_free_trial"])
        self.assertEqual(events[0]["consumed_points"], 500)
        self.assertEqual(events[0]["external_status"], "none")

    def test_feishu_audit_splits_remaining_trial_then_pending_partner(self):
        logged = self.client.post("/login/feishu/host", json={"code": "isolated-dev:alice"})
        self.assertEqual(logged.status_code, 200)
        principal = self.store.principal_for_fixture("alice")
        self.store.record_usage_event(principal, "ai-item-1", 9800, True, trace_id="pre-trial")
        draft = self._draft_for_imported_file()
        submitted = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "addon-usage-split"})
        self.assertEqual(submitted.status_code, 201)
        round_id = submitted.json()["id"]
        trial = [row for row in self.store.usage_events if row["trace_id"] == round_id + ":trial"]
        partner = [row for row in self.store.usage_events if row["trace_id"] == round_id + ":partner"]
        self.assertEqual(trial[0]["consumed_points"], 200)
        self.assertTrue(trial[0]["is_free_trial"])
        self.assertEqual(trial[0]["external_status"], "none")
        self.assertEqual(partner[0]["consumed_points"], 300)
        self.assertFalse(partner[0]["is_free_trial"])
        self.assertEqual(partner[0]["external_status"], "pending")
        quota = self.client.get("/integrations/feishu/usage/quota").json()
        self.assertEqual(quota["remaining_trial_ai_points"], 0)

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
            LLM_BASE_URL="https://llm.example",
            LLM_MODEL="x",
            PPAP_WEB_URL="https://app.example",
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
        ):
            settings = Settings()
        client = TestClient(
            create_app(settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, MemoryAuthStore(settings), self.http)
        )
        response = client.post("/login/feishu/host", json={"code": "isolated-dev:alice"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_host_code")


class DocsAddonHostSdkTests(unittest.TestCase):
    def test_vendor_blockit_client_is_present_and_iframe_gated(self):
        vendor = (ROOT / "apps" / "web" / "block-docs-addon-api.js").read_text()
        self.assertIn("export {\n", vendor)
        self.assertIn("BlockitClient", vendor)
        self.assertIn("@lark-opdev/block-docs-addon-api@0.0.11", vendor)
        addon = (ROOT / "apps" / "web" / "addon.js").read_text()
        self.assertIn("window.self !== window.top", addon)
        self.assertIn("./block-docs-addon-api.js", addon)
        self.assertIn("__PPAP_DOCS_ADDON_HOST__", addon)
        self.assertIn("${api}/me", addon)
        self.assertIn("existingSession", addon)
        self.assertIn("ensureSession", addon)
        self.assertIn("writeCurrentDocument", addon)
        html = (ROOT / "apps" / "web" / "addon.html").read_text()
        self.assertIn('href="./audit-files"', html)
        self.assertNotIn("audit-files.html", html)


if __name__ == "__main__":
    unittest.main()
