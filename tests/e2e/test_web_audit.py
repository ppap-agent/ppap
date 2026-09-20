"""Representative browser audit loop against isolated-dev stack."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "ppap_excel_audit_engine" / "src"),
    str(ROOT / "packages" / "ppap_docops" / "src"),
    str(ROOT / "packages" / "ppap_ocr_adapter" / "src"),
    str(ROOT / "packages" / "ppap_pdf_to_excel" / "src"),
    str(ROOT / "packages" / "ppap_vision" / "src"),
    str(ROOT / "packages"),
    str(ROOT / "services" / "api"),
    str(ROOT / "services" / "worker"),
]

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover - optional until the e2e job installs Playwright
    sync_playwright = None


WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


@unittest.skipUnless(sync_playwright, "playwright not installed")
class WebAuditLoopTests(unittest.TestCase):
    def test_submit_leave_return_review_and_resubmit(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "report.xlsx"
            reference = Path(tmp) / "context.pdf"
            newer = Path(tmp) / "report-v2.xlsx"
            target.write_bytes(b"browser-target-v1")
            reference.write_bytes(b"browser-reference")
            newer.write_bytes(b"browser-target-v2")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(WEB)
                page.get_by_test_id("isolated-dev-login").click()
                page.wait_for_selector("text=ou_alice")
                page.get_by_test_id("source-upload").set_input_files(str(target))
                page.get_by_test_id("prepare-audit").click()
                page.wait_for_function("() => document.getElementById('document-version-id').textContent.length > 0")
                page.get_by_test_id("reference-upload").set_input_files(str(reference))
                page.get_by_test_id("prepare-reference").click()
                page.wait_for_function("() => document.getElementById('draft-items').textContent.includes('context.pdf')")
                page.get_by_test_id("save-draft").click()
                page.wait_for_function("() => document.getElementById('draft-id').textContent.length > 0")
                page.get_by_test_id("submit-audit").click()
                page.wait_for_function("() => new URL(window.location.href).searchParams.get('round')")
                round_id = page.evaluate("() => new URL(window.location.href).searchParams.get('round')")
                page.goto(WEB)
                expect(page.get_by_test_id("round-id")).to_have_text("")
                page.goto(f"{WEB}/?round={round_id}")
                page.wait_for_selector('[data-testid="finding-status"]')
                expect(page.get_by_test_id("finding-status").first).to_have_text("PASS")
                coverage = page.get_by_test_id("coverage-panel").inner_text()
                self.assertIn("report.xlsx", coverage)
                self.assertIn("context.pdf", coverage)
                original = page.get_by_test_id("open-original")
                gap = page.get_by_test_id("locator-gap")
                if original.count() == 0:
                    expect(gap).to_be_visible()
                else:
                    expect(gap).to_be_hidden()
                    target_link = page.locator('[data-testid="open-original"]', has_text="report.xlsx")
                    href = target_link.get_attribute("href")
                    self.assertTrue(href)
                    downloaded = page.request.get(href)
                    self.assertEqual(downloaded.status, 200)
                    self.assertEqual(downloaded.body(), b"browser-target-v1")
                    target_link.click(no_wait_after=True)
                    expect(page.get_by_test_id("file-preview")).to_be_visible()
                    expect(page.get_by_test_id("file-preview-name")).to_contain_text("report.xlsx")
                    page.get_by_test_id("file-preview-close").click()
                    expect(page.get_by_test_id("file-preview")).to_be_hidden()
                expect(page.get_by_test_id("finding-suggestion").first).to_be_visible()
                expect(page.get_by_test_id("finding-evidence").first).to_be_visible()
                expect(page.get_by_test_id("result-summary")).to_contain_text("PASS")
                expect(page.get_by_test_id("pc-shell")).to_be_visible()
                page.get_by_test_id("review-judgment").fill("需修改")
                page.get_by_test_id("review-rationale").fill("尺寸缺失")
                page.get_by_test_id("review-next").fill("补传")
                page.get_by_test_id("save-review").click()
                page.wait_for_selector("text=saved")
                page.get_by_test_id("source-upload").set_input_files(str(newer))
                page.get_by_test_id("prepare-audit").click()
                page.wait_for_function(
                    "() => document.getElementById('document-version-id').textContent.length > 0"
                )
                page.get_by_test_id("resubmit-audit").click()
                page.wait_for_selector('[data-testid="previous-round"]:not(.hidden)')
                expect(page.get_by_test_id("previous-round")).to_be_visible()
                page.get_by_test_id("previous-round").click()
                page.wait_for_selector("text=需修改")
                expect(page.get_by_test_id("pc-shell")).to_be_visible()
                expect(page.get_by_test_id("finding-suggestion").first).to_be_visible()
                expect(page.get_by_test_id("file-version-list")).to_contain_text("report.xlsx")
                page.get_by_test_id("nav-usage").click()
                page.wait_for_url("**/usage")
                page.wait_for_function(
                    """() => ['usage-list', 'usage-empty', 'usage-error'].some((id) => {
                      const node = document.getElementById(id);
                      return Boolean(node && !node.classList.contains('hidden'));
                    })"""
                )
                usage_error = page.get_by_test_id("usage-error")
                if usage_error.is_visible():
                    self.fail(usage_error.inner_text())
                page.get_by_test_id("nav-audit-files").click()
                page.wait_for_url("**/audit-files")
                page.wait_for_function(
                    """() => ['history-list', 'history-empty', 'history-error'].some((id) => {
                      const node = document.getElementById(id);
                      return Boolean(node && !node.classList.contains('hidden'));
                    })"""
                )
                history_error = page.get_by_test_id("history-error")
                if history_error.is_visible():
                    self.fail(history_error.inner_text())
                expect(page.get_by_test_id("history-list")).to_be_visible()
                expect(page.locator('[data-testid="history-result"]', has_text="PASS").first).to_be_visible()
                expect(page.get_by_test_id("history-version").first).not_to_have_text("")
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
            page.goto(WEB)
            page.get_by_test_id("isolated-dev-login").click()
            page.wait_for_selector("text=ou_alice")
            page.get_by_test_id("open-history").click()
            page.wait_for_url("**/audit-files")
            page.wait_for_function(
                """() => ['history-list', 'history-empty', 'history-error'].some((id) => {
                  const node = document.getElementById(id);
                  return Boolean(node && !node.classList.contains('hidden'));
                })"""
            )
            error = page.get_by_test_id("history-error")
            if error.is_visible():
                self.fail(error.inner_text())
            visible = page.locator('[data-testid="history-list"]:not(.hidden)').or_(
                page.locator('[data-testid="history-empty"]:not(.hidden)')
            )
            expect(visible).to_be_visible()
            self.assertTrue(page.url.rstrip("/").endswith("/audit-files"))
            browser.close()


if __name__ == "__main__":
    unittest.main()
