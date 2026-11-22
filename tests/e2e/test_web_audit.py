"""The docs add-on entry page against the isolated-dev stack."""

from __future__ import annotations

import unittest

from playwright.sync_api import expect, sync_playwright

from .conftest import WEB


class DocsAddonEntryTests(unittest.TestCase):
    def test_docs_addon_entry_reports_missing_host(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_viewport_size({"width": 560, "height": 600})
            page.goto(f"{WEB}/addon")
            page.wait_for_selector("text=请在飞书云文档中打开 PPAP Audit。")
            expect(page.get_by_test_id("addon-history")).to_be_visible()
            href = page.get_by_test_id("addon-history").get_attribute("href")
            # History opens the file archive page of the audit app.
            self.assertTrue(href and href.rstrip("/").endswith("/files"))
            expect(page.get_by_test_id("addon-submit")).to_be_disabled()
            browser.close()
