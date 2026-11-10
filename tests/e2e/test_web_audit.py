"""Representative browser audit loop against isolated-dev stack."""

from __future__ import annotations

import io
import json
import os
import time
import unittest
import uuid
from types import SimpleNamespace
from urllib.parse import urlsplit
from zipfile import BadZipFile

import pytest
from openpyxl import Workbook
from product.fixtures import RULE_A

from .conftest import isolated_dev_login

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover - optional until the e2e job installs Playwright
    sync_playwright = None
else:
    expect.set_options(timeout=20_000)


WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


def upload_version(page, api, name, body, file_id=None):
    path = f"/files/{file_id}/versions" if file_id else "/files"
    created = page.request.post(api + path, data={"original_filename": name, "content_type": "application/octet-stream", "byte_size": len(body)})
    assert created.status == 201
    version = created.json()
    path = f"{api}/files/{version['file_id']}/versions/{version['id']}"
    assert page.request.put(path + "/blob", data=body).status == 200
    assert page.request.post(path + "/finalize").status == 200
    return version


PRESENCE_CHECKER = """import json
from io import BytesIO
from openpyxl import load_workbook
from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource

def check(context):
    book = load_workbook(BytesIO(context.sources[context.audit_file.audit_id]), read_only=True)
    try:
        sheet = book.active
        present = bool(sheet["A1"].value)
        locator = json.dumps({"kind": "xlsx_cell", "sheet": sheet.title, "cell": "A1"})
        return AuditResultItem(classification="资料", item="固定原件 A1 已填写", priority="P0",
            status="PASS" if present else "NG", suggestions="已核对固定原件 A1。", clause_id=context.clause_id, checks_performed=1,
            evidence=AuditEvidence(required="structure", used="structure", source_format="xlsx", prepared_format="xlsx",
                sources=(EvidenceSource(kind="structure", locator=locator),)))
    finally:
        book.close()
"""


def test_presence_fixture_checks_frozen_a1_and_does_not_fabricate_pass():
    namespace = {}
    exec(compile(PRESENCE_CHECKER, "presence-fixture.py", "exec"), namespace)
    for value, expected in (("Frozen original", "PASS"), (None, "NG")):
        book = Workbook()
        book.active["A1"] = value
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        context = SimpleNamespace(audit_file=SimpleNamespace(audit_id="original"), sources={"original": stream.getvalue()}, clause_id="presence")
        result = namespace["check"](context)
        assert result.status == expected
        assert result.checks_performed == 1
        assert result.clause_id == "presence"
        assert json.loads(result.evidence.sources[0].locator) == {"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1"}
    with pytest.raises(BadZipFile):
        namespace["check"](SimpleNamespace(audit_file=SimpleNamespace(audit_id="original"), sources={"original": b"broken workbook"}, clause_id="presence"))


def completed_round(page, api, round_id):
    deadline = time.monotonic() + 20
    while True:
        response = page.request.get(f"{api}/rounds/{round_id}")
        assert response.status == 200
        result = response.json()
        if result["execution"] in {"completed", "failed"}:
            assert result["execution"] == "completed", result.get("failure")
            assert result["findings"] and all(row["status"] == "PASS" for row in result["findings"])
            assert result["executor"] == "ppap-checker-core"
            assert all(row["locator"]["semantic"]["checks_performed"] == 1 for row in result["findings"])
            return result
        assert time.monotonic() < deadline, "The isolated Checker Core did not finish the accepted round"
        page.wait_for_timeout(50)


@unittest.skipUnless(sync_playwright, "playwright not installed")
class WebAuditLoopTests(unittest.TestCase):
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
            page.get_by_role("textbox", name="搜索审核任务", exact=True).fill(name)
            history = page.get_by_role("table", name="全部任务审核记录", exact=True)
            row = history.get_by_role("row").filter(has_text=name)
            expect(row).to_have_count(1)
            expect(row.get_by_role("link")).to_have_attribute("href", f"/audit?round={round_id}")
            expect(page.get_by_role("alert")).to_have_count(0)
            self.assertIn(("GET", urlsplit(api + "/rounds").path), requests)
            self.assertIn(("GET", urlsplit(api + "/drafts").path), requests)
            self.assertEqual([method for method, _ in requests if method in {"POST", "PUT", "PATCH", "DELETE"}], [])
            self.assertEqual(urlsplit(page.url).path, "/workbench")
            browser.close()


if __name__ == "__main__":
    unittest.main()
