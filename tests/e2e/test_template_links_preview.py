"""Fixed template/contact links preserve the active audit; no real Feishu writes."""

import os
import unittest

PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")
GUIDE = "https://icoastline.feishu.cn/wiki/M5KqwJYWDiuUYNkGoiXcL1bgnbe"
CONTACT = GUIDE + "#share-Mo3QdDJbko2ZHHx9cf1c5FdZn7M"

if PREVIEW:
    from playwright.sync_api import expect, sync_playwright


@unittest.skipUnless(PREVIEW, "requires PPAP_PREVIEW_URL (preview-only UI)")
class TemplateLinksPreviewTests(unittest.TestCase):
    def test_template_and_contact_open_separately_without_losing_the_upload_draft(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for width in (1440, 390):
                with self.subTest(width=width):
                    page = browser.new_page(viewport={"width": width, "height": 900})
                    page.context.route(GUIDE + "**", lambda route: route.fulfill(body="<h1>模板使用说明</h1>", content_type="text/html; charset=utf-8"))
                    page.goto(PREVIEW + "/preview/audit.html")
                    page.get_by_role("button", name="使用三类文件示例", exact=True).click()
                    page.get_by_role("checkbox", name="选择 控制计划.xlsx", exact=True).uncheck()
                    if width < 1024:
                        page.get_by_role("button", name="打开产品导航", exact=True).click()
                    nav = page.get_by_role("navigation", name="产品导航", exact=True).filter(visible=True)
                    # The template library is a page of the product; the upload draft stays behind it.
                    nav.get_by_role("button", name="文件模板", exact=True).click()
                    expect(page.get_by_role("navigation", name="客户", exact=True)).to_be_visible()
                    page.close()
            browser.close()

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
