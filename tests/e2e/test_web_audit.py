"""Representative browser audit loop against isolated-dev stack."""

from __future__ import annotations

import os
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover - optional until the e2e job installs Playwright
    sync_playwright = None
else:
    expect.set_options(timeout=20_000)


WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


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
            # History opens the file archive page of the audit app.
            self.assertTrue(href and href.rstrip("/").endswith("/files"))
            expect(page.get_by_test_id("addon-submit")).to_be_disabled()
            browser.close()


if __name__ == "__main__":
    unittest.main()
