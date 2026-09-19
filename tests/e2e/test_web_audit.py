"""Representative browser audit loop against isolated-dev stack."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages"), str(ROOT / "services" / "api"), str(ROOT / "services" / "worker")]

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
            newer = Path(tmp) / "report-v2.xlsx"
            target.write_bytes(b"browser-target-v1")
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
                original = page.get_by_test_id("open-original")
                gap = page.get_by_test_id("locator-gap")
                if original.count() == 0:
                    expect(gap).to_be_visible()
                else:
                    expect(gap).to_be_hidden()
                    href = original.first.get_attribute("href")
                    self.assertTrue(href)
                    downloaded = page.request.get(href)
                    self.assertEqual(downloaded.status, 200)
                    self.assertEqual(downloaded.body(), b"browser-target-v1")
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
                browser.close()


if __name__ == "__main__":
    unittest.main()
