"""Representative browser audit loop against isolated-dev stack."""

from __future__ import annotations

import io
import os
import re
import time
import unittest
import uuid
from urllib.parse import parse_qs, urljoin, urlsplit

from openpyxl import Workbook
from ppap_core.fixtures import RULE_A

from .conftest import isolated_dev_login

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover - optional until the e2e job installs Playwright
    sync_playwright = None
else:
    expect.set_options(timeout=20_000)


WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


def workbook_bytes(text):
    book = Workbook()
    book.active.append(["检查内容", text])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def upload_version(page, api, name, body, file_id=None):
    path = f"/files/{file_id}/versions" if file_id else "/files"
    created = page.request.post(api + path, data={"original_filename": name, "content_type": "application/octet-stream", "byte_size": len(body)})
    assert created.status == 201
    version = created.json()
    path = f"{api}/files/{version['file_id']}/versions/{version['id']}"
    assert page.request.put(path + "/blob", data=body).status == 200
    assert page.request.post(path + "/finalize").status == 200
    return version


def completed_round(page, api, round_id):
    deadline = time.monotonic() + 20
    while True:
        response = page.request.get(f"{api}/rounds/{round_id}")
        assert response.status == 200
        result = response.json()
        if result["execution"] in {"completed", "failed"}:
            assert result["execution"] == "completed", result.get("failure")
            assert result["findings"] and all(row["status"] == "PASS" for row in result["findings"])
            return result
        assert time.monotonic() < deadline, "The isolated presence Worker did not finish the accepted round"
        page.wait_for_timeout(50)


def accept_round(page, api, items, predecessor=None, draft_id=None):
    if draft_id:
        assert page.request.put(f"{api}/drafts/{draft_id}", data={"items": items}).status == 200
    else:
        response = page.request.post(f"{api}/drafts", data={"items": items, "predecessor_id": predecessor})
        assert response.status == 201
        draft_id = response.json()["id"]
    response = page.request.post(f"{api}/drafts/{draft_id}/accept", data={"idempotency_key": str(uuid.uuid4())})
    assert response.status == 201
    return completed_round(page, api, response.json()["id"])


@unittest.skipUnless(sync_playwright, "playwright not installed")
class WebAuditLoopTests(unittest.TestCase):
    def test_uploaded_versions_reopen_frozen_round_and_review_after_an_explicit_new_round(self):
        # UI upload + real Product API/DB/object storage/Worker + current history UI.
        # Presence is an integration fixture, not proof of PPAP rule quality or V3 matching.
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.set_default_timeout(5000)
            api = isolated_dev_login(page, WEB)
            stamp = uuid.uuid4().hex[:8]
            name, context = f"report-{stamp}.xlsx", f"context-{stamp}.txt"
            target_bytes, reference_bytes = workbook_bytes("第一版"), b"frozen reference"
            page.get_by_role("button", name="上传资料", exact=True).click()
            page.get_by_label("上传文件", exact=True).set_input_files(
                [
                    {"name": name, "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": target_bytes},
                    {"name": context, "mimeType": "text/plain", "buffer": reference_bytes},
                ]
            )
            table = page.get_by_role("table", name="审核资料", exact=True)
            expect(table.get_by_role("button", name="阅读 " + name, exact=True)).to_be_visible()
            expect(table.get_by_role("button", name="阅读 " + context, exact=True)).to_be_visible()
            draft_id = parse_qs(urlsplit(page.url).query)["case"][0]
            draft = page.request.get(f"{api}/drafts/{draft_id}").json()
            self.assertEqual(len(draft["items"]), 2)
            target = next(row for row in draft["items"] if row["original_filename"] == name)
            reference = next(row for row in draft["items"] if row["original_filename"] == context)
            original = accept_round(
                page,
                api,
                [
                    {"file_version_id": target["file_version_id"], "role": "target", "rule_version_id": RULE_A},
                    {"file_version_id": reference["file_version_id"], "role": "reference", "rule_version_id": None},
                ],
                draft_id=draft_id,
            )

            page.goto(f"{WEB}/audit?round={original['id']}")
            expect(page.get_by_role("heading", name="历史审核", exact=True)).to_be_visible()
            records = page.get_by_role("region", name="历史机器记录", exact=True)
            expect(records.get_by_text("原机器记录：满足", exact=True)).to_be_visible()
            page.get_by_role("tab", name="核对原件", exact=True).click()
            download = page.get_by_role("link", name="下载原件", exact=True)
            download_url = urljoin(page.url, download.get_attribute("href"))
            self.assertEqual(download_url, f"{api}/rounds/{original['id']}/files/{target['file_id']}/versions/{target['file_version_id']}/download")
            self.assertEqual(page.request.get(download_url).body(), target_bytes)
            page.get_by_role("tab", name="审核详情", exact=True).click()
            records.get_by_role("button", name="记录人工复核", exact=True).click()
            editor = page.get_by_role("dialog", name="人工复核", exact=True)
            editor.get_by_role("combobox", name="人工判断", exact=True).click()
            page.get_by_role("option", name="确认机器结果", exact=True).click()
            editor.get_by_role("textbox", name="判断理由", exact=True).fill("已核对第一版原件，记录不等于正式批准。")
            editor.get_by_role("textbox", name="后续建议", exact=True).fill("保留版本与原件。")
            with page.expect_response(lambda reply: reply.request.method == "PUT" and reply.url.endswith(f"/rounds/{original['id']}/reviews")) as saved:
                editor.get_by_role("button", name="保存人工复核", exact=True).click()
            self.assertEqual(saved.value.status, 200)
            expect(editor.get_by_text("人工复核已保存。", exact=True)).to_be_visible()
            editor.get_by_role("button", name="完成", exact=True).click()
            saved_original = page.request.get(f"{api}/rounds/{original['id']}").json()
            self.assertEqual(saved_original["findings"][0]["review"]["judgment"], "confirm")
            self.assertEqual(saved_original["findings"][0]["status"], "PASS")
            self.assertEqual(saved_original["inputs"], original["inputs"])

            newer_bytes = workbook_bytes("第二版")
            newer = upload_version(page, api, name, newer_bytes, target["file_id"])
            self.assertNotEqual(newer["id"], target["file_version_id"])
            second = accept_round(
                page,
                api,
                [
                    {"file_version_id": newer["id"], "role": "target", "rule_version_id": RULE_A},
                    {"file_version_id": reference["file_version_id"], "role": "reference", "rule_version_id": None},
                ],
                predecessor=original["id"],
            )
            self.assertEqual(second["predecessor_id"], original["id"])
            self.assertIsNone(second["findings"][0]["review"])
            page.goto(f"{WEB}/audit?round={second['id']}")
            page.get_by_role("tab", name="核对原件", exact=True).click()
            download = page.get_by_role("link", name="下载原件", exact=True)
            download_url = urljoin(page.url, download.get_attribute("href"))
            self.assertEqual(download_url, f"{api}/rounds/{second['id']}/files/{newer['file_id']}/versions/{newer['id']}/download")
            self.assertEqual(page.request.get(download_url).body(), newer_bytes)

            page.goto(WEB)
            page.goto(f"{WEB}/audit?round={original['id']}")
            expect(page.get_by_role("region", name="已有人工复核", exact=True)).to_contain_text("已核对第一版原件，记录不等于正式批准。")
            page.reload()
            expect(page.get_by_role("region", name="已有人工复核", exact=True)).to_contain_text("已核对第一版原件，记录不等于正式批准。")
            page.get_by_role("tab", name="核对原件", exact=True).click()
            self.assertEqual(page.request.get(urljoin(page.url, page.get_by_role("link", name="下载原件", exact=True).get_attribute("href"))).body(), target_bytes)
            self.assertEqual(page.request.get(f"{api}/rounds/{original['id']}").json(), saved_original)
            browser.close()

    def test_docs_addon_entry_reports_missing_host(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_viewport_size({"width": 560, "height": 600})
            page.goto(f"{WEB}/addon")
            page.wait_for_selector("text=当前环境不支持 docs-addon 登录")
            expect(page.get_by_test_id("addon-history")).to_be_visible()
            href = page.get_by_test_id("addon-history").get_attribute("href")
            self.assertTrue(href and href.rstrip("/").endswith("audit-files"))
            self.assertNotIn(".html", href)
            expect(page.get_by_test_id("addon-submit")).to_be_disabled()
            browser.close()

    def test_audit_files_path_loads_history_after_login(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_default_timeout(5000)
            api = isolated_dev_login(page, WEB)
            name = f"history-{uuid.uuid4().hex[:8]}.txt"
            created = page.request.post(f"{api}/files", data={"original_filename": name, "content_type": "text/plain", "byte_size": 7})
            self.assertEqual(created.status, 201)
            version = created.json()
            path = f"{api}/files/{version['file_id']}/versions/{version['id']}"
            uploaded = page.request.put(path + "/blob", data=b"history", headers={"Content-Type": "text/plain"})
            self.assertEqual(uploaded.status, 200)
            self.assertEqual(page.request.post(path + "/finalize").status, 200)
            draft = page.request.post(f"{api}/drafts", data={})
            self.assertEqual(draft.status, 201)
            draft_id = draft.json()["id"]
            saved = page.request.put(f"{api}/drafts/{draft_id}", data={"items": [{"file_version_id": version["id"], "role": "target", "rule_version_id": RULE_A}]})
            self.assertEqual(saved.status, 200)
            accepted = page.request.post(f"{api}/drafts/{draft_id}/accept", data={"idempotency_key": str(uuid.uuid4())})
            self.assertEqual(accepted.status, 201)
            round_id = accepted.json()["id"]

            requests = []
            page.on("request", lambda request: requests.append((request.method, urlsplit(request.url).path)))
            page.goto(f"{WEB}/audit-files")
            expect(page.get_by_role("heading", name="工作台", exact=True)).to_be_visible()
            page.get_by_role("tab", name="全部记录", exact=True).click()
            page.get_by_role("searchbox", name="搜索零件、供应商或文件", exact=True).fill(name)
            history = page.get_by_role("table", name="全部审核记录", exact=True)
            row = history.get_by_role("row").filter(has_text=name)
            expect(row).to_have_count(1)
            expect(row.get_by_role("link")).to_have_attribute("href", f"/audit-files?round={round_id}")
            expect(page.get_by_role("alert")).to_have_count(0)
            self.assertIn(("GET", urlsplit(api + "/rounds").path), requests)
            self.assertIn(("GET", urlsplit(api + "/drafts").path), requests)
            self.assertEqual([method for method, _ in requests if method in {"POST", "PUT", "PATCH", "DELETE"}], [])
            self.assertEqual(urlsplit(page.url).path, "/audit-files")
            browser.close()

    def test_return_from_requirements_restores_review_file_clause_filter_and_next_pending(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(5000)
            api = isolated_dev_login(page, WEB)
            stamp = uuid.uuid4().hex[:8]
            names = [f"alpha-{stamp}.xlsx", f"beta-{stamp}.xlsx"]
            versions = [upload_version(page, api, name, workbook_bytes(name)) for name in names]
            original = accept_round(page, api, [{"file_version_id": row["id"], "role": "target", "rule_version_id": RULE_A} for row in versions])
            page.goto(f"{WEB}/?round={original['id']}")
            expect(page.get_by_role("heading", name="人工复核", exact=True)).to_be_visible()
            page.get_by_role("button", name=re.compile("^条款目录")).click()
            directory = page.get_by_role("dialog", name="审核条款", exact=True)
            directory.get_by_role("radio", name="全部", exact=True).check()
            links = directory.get_by_role("navigation", name="审核条款", exact=True).get_by_role("link")
            expect(links).to_have_count(2)
            next_clause = parse_qs(urlsplit(links.nth(1).get_attribute("href")).query)["clause"][0]
            links.nth(1).click()
            article = page.get_by_role("article", name=re.compile("^当前条款 "))
            next_heading = article.get_by_role("heading").inner_text()
            page.get_by_role("button", name=re.compile("^条款目录")).click()
            links.first.click()
            expect(article.get_by_role("heading")).not_to_have_text(next_heading)
            heading = article.get_by_role("heading").inner_text()
            page.get_by_role("combobox", name="复核原件", exact=True).click()
            page.get_by_role("option", name=names[0], exact=True).click()
            page.get_by_role("combobox", name="复核原件", exact=True).click()
            page.get_by_role("option", name=names[1], exact=True).click()
            expect(page).to_have_url(re.compile(r"[?&]reviewFile="))
            expected = parse_qs(urlsplit(page.url).query)
            self.assertEqual(expected["round"], [original["id"]])
            self.assertEqual(expected["reviewFilter"], ["all"])
            self.assertIn(versions[1]["id"], expected["reviewFile"][0])
            page.get_by_role("link", name="工作台", exact=True).click()
            page.get_by_role("link", name="审核要求", exact=True).click()
            expect(page.get_by_role("heading", name="审核要求", exact=True)).to_be_visible()
            page.go_back()
            page.go_back()
            expect(page.get_by_role("heading", name="人工复核", exact=True)).to_be_visible()
            page.reload()
            expect(article.get_by_role("heading")).to_have_text(heading)
            expect(page.get_by_role("combobox", name="复核原件", exact=True)).to_contain_text(names[1])
            restored = parse_qs(urlsplit(page.url).query)
            for key in ("round", "clause", "reviewFile", "reviewFilter"):
                self.assertEqual(restored[key], expected[key])
            page.get_by_role("button", name=re.compile("^条款目录")).click()
            expect(directory.get_by_role("radio", name="全部", exact=True)).to_be_checked()
            active = directory.get_by_role("navigation", name="审核条款", exact=True).locator("[aria-current=page]")
            self.assertEqual(parse_qs(urlsplit(active.get_attribute("href")).query)["clause"], expected["clause"])
            page.keyboard.press("Escape")
            article.get_by_role("combobox", name=re.compile(" 人工判断$")).click()
            page.get_by_role("option", name="确认机器结果", exact=True).click()
            article.get_by_role("textbox", name=re.compile(" 复核依据$")).fill("返回后核对原轮文件并保存。")
            with page.expect_response(lambda reply: reply.request.method == "PUT" and reply.url.endswith(f"/rounds/{original['id']}/reviews")) as saved:
                article.get_by_role("button", name="保存并继续", exact=True).click()
            self.assertEqual(saved.value.status, 200)
            page.wait_for_function("(next) => new URL(location.href).searchParams.get('clause') === next", arg=next_clause)
            expect(article.get_by_role("heading")).to_have_text(next_heading)
            expect(article.get_by_role("combobox", name=re.compile(" 人工判断$"))).to_contain_text("尚未判断")
            updated = page.request.get(f"{api}/rounds/{original['id']}").json()
            reviewed = [row for row in updated["findings"] if row["review"]]
            self.assertEqual(len(reviewed), 1)
            self.assertEqual(reviewed[0]["item_key"], expected["clause"][0])
            self.assertEqual(reviewed[0]["review"]["rationale"], "返回后核对原轮文件并保存。")
            self.assertIsNone(next(row for row in updated["findings"] if row["item_key"] == next_clause)["review"])
            self.assertEqual(updated["inputs"], original["inputs"])
            self.assertEqual([row["status"] for row in updated["findings"]], ["PASS", "PASS"])
            self.assertEqual(parse_qs(urlsplit(page.url).query)["round"], [original["id"]])
            browser.close()


if __name__ == "__main__":
    unittest.main()
