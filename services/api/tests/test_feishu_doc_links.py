"""List related Feishu files from the current cloud document. Mocked HTTP only."""
from __future__ import annotations

import json
import os
import unittest

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx


from fastapi.testclient import TestClient

from ppap_core.feishu import UserTokens
from ppap_core.feishu_drive import extract_doc_token_from_url, list_doc_links
from ppap_core.identity import SESSION_COOKIE
from ppap_core.settings import Settings
from ppap_api import create_app


def _clean_env(**extra: str):
    keep = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))
    }
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class ExtractTokenTests(unittest.TestCase):
    def test_extracts_docx_sheet_and_ignores_foreign_hosts(self):
        self.assertEqual(
            extract_doc_token_from_url("https://foo.feishu.cn/docx/DocTokenA"),
            ("DocTokenA", "docx"),
        )
        self.assertEqual(
            extract_doc_token_from_url("https://bar.larksuite.com/sheet/SheetTokenB?from=space"),
            ("SheetTokenB", "sheet"),
        )
        self.assertIsNone(extract_doc_token_from_url("https://example.com/docx/DocTokenA"))
        self.assertIsNone(extract_doc_token_from_url("/docx/DocTokenA"))


class _DocLinksMock:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.fail_blocks = False
        self.blocks_code = 0
        self.meta_by_token = {
            "sheet-token": {"doc_type": "sheet", "title": "compare-sheet"},
            "docx-token": {"doc_type": "docx", "title": "related-doc"},
            "file-token": {"doc_type": "file", "title": "attachment.xlsx"},
            "folder-token": {"doc_type": "folder", "title": "a-folder"},
            "page-two": {"doc_type": "bitable", "title": "second-page"},
        }
        self.pages = [
            {
                "items": [
                    {
                        "block_type": 2,
                        "text": {
                            "elements": [
                                {
                                    "mention_doc": {
                                        "token": "sheet-token",
                                        "title": "compare-sheet",
                                        "url": "https://foo.feishu.cn/sheet/sheet-token",
                                        "obj_type": "sheet",
                                    }
                                },
                                {
                                    "link": {
                                        "url": "https://foo.feishu.cn/docx/docx-token",
                                    }
                                },
                                {
                                    "mention_doc": {
                                        "token": "ignored-empty-url",
                                        "title": "no-host",
                                        "url": "",
                                    }
                                },
                            ]
                        },
                    },
                    {
                        "block_type": 4,
                        "heading2": {
                            "elements": [
                                {
                                    "text_run": {
                                        "content": "see file",
                                        "text_element_style": {
                                            "link": {"url": "https://foo.feishu.cn/file/file-token"}
                                        },
                                    }
                                }
                            ]
                        },
                    },
                    {
                        "block_type": 23,
                        "file": {
                            "token": "file-token",
                            "name": "attachment.xlsx",
                            "url": "https://foo.feishu.cn/file/file-token",
                        },
                    },
                    {
                        "block_type": 2,
                        "text": {
                            "elements": [
                                {
                                    "mention_doc": {
                                        "token": "folder-token",
                                        "title": "a-folder",
                                        "url": "https://foo.feishu.cn/drive/folder/folder-token",
                                    }
                                }
                            ]
                        },
                    },
                ],
                "has_more": True,
                "page_token": "page-2",
            },
            {
                "items": [
                    {
                        "block_type": 2,
                        "text": {
                            "elements": [
                                {
                                    "mention_doc": {
                                        "token": "page-two",
                                        "title": "second-page",
                                        "url": "https://foo.feishu.cn/base/page-two",
                                        "obj_type": "bitable",
                                    }
                                }
                            ]
                        },
                    }
                ],
                "has_more": False,
            },
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        method = request.method.upper()
        self.calls.append((method, url))
        auth = request.headers.get("authorization", "")
        if auth != "Bearer user-access":
            return httpx.Response(401, json={"code": 99991663, "msg": "unauthorized"})
        if method == "GET" and "/docx/v1/documents/" in url and "/blocks" in url:
            if self.fail_blocks:
                return httpx.Response(502, json={"code": 1, "msg": "blocks failed"})
            if self.blocks_code != 0:
                return httpx.Response(200, json={"code": self.blocks_code, "msg": "denied"})
            parsed = urlparse(url)
            page_token = (parse_qs(parsed.query).get("page_token") or [""])[0]
            page = self.pages[1] if page_token == "page-2" else self.pages[0]
            return httpx.Response(200, json={"code": 0, "data": page})
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
        return httpx.Response(404, json={"code": 1, "msg": "unmocked " + url})


class FeishuDocLinksTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory):
        self.auth_store = auth_store_factory

    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings()
        self.store = self.auth_store(self.settings)
        self.store.seed_isolated_dev()
        self.feishu = _DocLinksMock()
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

    def test_lists_mentions_hyperlinks_and_files_skipping_folders(self):
        self._login_feishu_user("alice")
        response = self.client.get("/integrations/feishu/docs/links", params={"file_token": "current-doc"})
        self.assertEqual(response.status_code, 200)
        links = response.json()["doc_links"]
        tokens = [row["token"] for row in links]
        self.assertEqual(tokens, ["sheet-token", "docx-token", "file-token", "page-two"])
        by_token = {row["token"]: row for row in links}
        self.assertEqual(by_token["sheet-token"]["doc_type"], "sheet")
        self.assertEqual(by_token["sheet-token"]["type"], "sheet")
        self.assertEqual(by_token["sheet-token"]["name"], "compare-sheet")
        self.assertEqual(by_token["docx-token"]["doc_type"], "docx")
        self.assertEqual(by_token["file-token"]["doc_type"], "file")
        self.assertEqual(by_token["page-two"]["doc_type"], "bitable")
        self.assertNotIn("folder-token", tokens)
        self.assertNotIn("ignored-empty-url", tokens)
        self.assertTrue(any("/docx/v1/documents/current-doc/blocks" in url for _method, url in self.feishu.calls))
        self.assertTrue(any("page_token=page-2" in url for _method, url in self.feishu.calls))

    def test_helper_matches_route_without_creating_a_ledger(self):
        links = list_doc_links(self.http, "user-access", "current-doc")
        self.assertEqual([row["token"] for row in links], ["sheet-token", "docx-token", "file-token", "page-two"])

    def test_unauthenticated_and_fixture_session_and_invalid_token(self):
        self.client.cookies.clear()
        unauth = self.client.get("/integrations/feishu/docs/links", params={"file_token": "current-doc"})
        self.assertEqual(unauth.status_code, 401)
        self._login_fixture("alice")
        fixture = self.client.get("/integrations/feishu/docs/links", params={"file_token": "current-doc"})
        self.assertEqual(fixture.status_code, 409)
        self.assertEqual(fixture.json()["error"], "feishu_user_token_missing")
        self._login_feishu_user("alice")
        missing = self.client.get("/integrations/feishu/docs/links")
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["error"], "invalid_file_token")
        slashed = self.client.get("/integrations/feishu/docs/links", params={"file_token": "a/b"})
        self.assertEqual(slashed.status_code, 400)

    def test_http_failure_is_502_and_feishu_code_is_empty_list(self):
        self._login_feishu_user("alice")
        self.feishu.fail_blocks = True
        failed = self.client.get("/integrations/feishu/docs/links", params={"file_token": "current-doc"})
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(failed.json()["error"], "feishu_doc_links_failed")
        self.feishu.fail_blocks = False
        self.feishu.blocks_code = 99991672
        empty = self.client.get("/integrations/feishu/docs/links", params={"file_token": "current-doc"})
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["doc_links"], [])


if __name__ == "__main__":
    unittest.main()
