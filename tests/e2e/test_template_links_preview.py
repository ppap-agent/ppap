"""Unmatched files offer the template and contact entries; no real Feishu writes."""

import os
import unittest

PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")

if PREVIEW:
    from playwright.sync_api import expect, sync_playwright


@unittest.skipUnless(PREVIEW, "requires PPAP_PREVIEW_URL (preview-only UI)")
class TemplateLinksPreviewTests(unittest.TestCase):
    def test_unmatched_files_use_the_same_template_and_contact_destinations(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=unmatched")
            page.get_by_role("button", name="使用三类文件示例", exact=True).click()
            page.get_by_role("button", name="开始识别", exact=True).click()
            expect(page.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
            for name in ("查看标准模板", "反馈对应模板"):
                # All three files are stopped for one reason, so they share one set of entries.
                expect(page.get_by_role("button", name=name, exact=True)).to_have_count(1)
            expect(page.get_by_role("button", name="结束任务", exact=True)).to_be_enabled()
            page.get_by_role("button", name="反馈对应模板", exact=True).click()
            expect(page.get_by_role("dialog", name="补充模板", exact=True)).to_be_visible()
            browser.close()
