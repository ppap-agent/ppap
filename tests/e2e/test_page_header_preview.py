"""Current page actions through the existing preview UI."""

import os
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")


@unittest.skipUnless(PREVIEW and sync_playwright, "requires the explicit UI preview and Playwright")
class PageHeaderPreviewTests(unittest.TestCase):
    def test_audit_and_usage_share_the_page_bar_without_previous_page_actions(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1057, "height": 853})
            page.set_default_timeout(5000)
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-ready&view=usage")
            bar = page.get_by_role("banner")
            expect(bar.get_by_role("button", name="刷新用量", exact=True)).to_be_visible()
            # The page keeps its own title and description below the shared bar.
            expect(page.get_by_role("heading", name="用量记录", exact=True)).to_be_visible()
            expect(page.get_by_role("region", name="自然月用量汇总", exact=True)).to_be_visible()
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="PPAP Audit", exact=True).click()
            # PPAP Audit is the new-audit page; the usage page's own action does not follow.
            expect(bar.get_by_role("navigation", name="当前位置", exact=True).get_by_text("新建审核", exact=True)).to_be_visible()
            expect(bar.get_by_role("button", name="刷新用量", exact=True)).to_have_count(0)
            expect(page.get_by_role("heading", name="用量记录", exact=True)).to_have_count(0)
            expect(page.get_by_label("选择审核文件", exact=True)).to_be_attached()
            browser.close()
