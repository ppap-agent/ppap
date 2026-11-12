"""Representative browser audit loop against isolated-dev stack."""

from __future__ import annotations

import io
import json
import os
import time
import unittest
from types import SimpleNamespace
from zipfile import BadZipFile

import pytest
from openpyxl import Workbook

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


if __name__ == "__main__":
    unittest.main()
