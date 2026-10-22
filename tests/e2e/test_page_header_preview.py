"""Current page actions and reading space through the existing preview UI."""

import os
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")


@unittest.skipUnless(PREVIEW and sync_playwright, "requires the explicit UI preview and Playwright")
class PageHeaderPreviewTests(unittest.TestCase):
    def test_return_to_historical_round_removes_the_hidden_task_report_actions(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1057, "height": 853})
            page.set_default_timeout(5000)
            address = PREVIEW + "/preview/audit.html?scene=reports-ready&round=history-round"
            page.goto(address)
            expect(page.get_by_role("region", name="历史审核", exact=True)).to_be_visible()
            page.get_by_role("button", name="打开审核任务", exact=True).click()
            page.get_by_role("navigation", name="任务列表", exact=True).get_by_role("button").filter(has_text="PPAP-报告示例").click()
            page.get_by_role("button", name="报告中心", exact=True).click()
            bar = page.get_by_role("banner")
            expect(bar.get_by_role("button", name="读取报告状态", exact=True)).to_be_visible()
            page.go_back()
            expect(page).to_have_url(address)
            expect(page.get_by_role("region", name="历史审核", exact=True)).to_be_visible()
            expect(bar.get_by_role("button", name="读取报告状态", exact=True)).to_have_count(0)
            page.set_viewport_size({"width": 390, "height": 844})
            expect(bar.get_by_role("button", name="文件报告 · 3 份", exact=True)).to_have_count(0)
            page.go_forward()
            expect(page.get_by_role("region", name="当前报告", exact=True)).to_be_visible()
            expect(bar.get_by_role("button", name="读取报告状态", exact=True)).to_be_visible()
            browser.close()

    def test_report_reader_starts_under_the_page_bar_without_a_second_title_row(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1057, "height": 853})
            page.set_default_timeout(5000)
            page.goto(PREVIEW + "/preview/audit.html?scene=reports-ready&task=preview-report-task&report=preview-recovery-version-1")
            bar = page.get_by_role("banner")
            expect(bar.get_by_role("button", name="读取报告状态", exact=True)).to_be_visible()
            title = page.get_by_role("heading", name="审核报告", exact=True)
            self.assertLessEqual(title.bounding_box()["height"], 2)
            current = page.get_by_role("region", name="当前报告", exact=True)
            filename = current.get_by_role("heading", name="控制计划.xlsx-审核报告", exact=True)
            toolbar = current.get_by_role("toolbar", name="PDF 阅读工具", exact=True)
            expect(toolbar).to_be_visible()
            bottom = bar.bounding_box()["y"] + bar.bounding_box()["height"]
            self.assertLessEqual(filename.bounding_box()["y"] - bottom, 32)
            self.assertLessEqual(toolbar.bounding_box()["y"] - bottom, 90)
            page.set_viewport_size({"width": 390, "height": 844})
            expect(bar.get_by_role("button", name="文件报告 · 3 份", exact=True)).to_be_visible()
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            browser.close()

    def test_workbench_and_usage_share_the_page_bar_without_previous_page_actions(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1057, "height": 853})
            page.set_default_timeout(5000)
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-ready&view=usage")
            bar = page.get_by_role("banner")
            expect(bar.get_by_role("button", name="刷新用量", exact=True)).to_be_visible()
            self.assertLessEqual(page.get_by_role("heading", name="用量记录", exact=True).bounding_box()["height"], 2)
            expect(page.get_by_role("region", name="自然月用量汇总", exact=True)).to_be_visible()
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="工作台", exact=True).click()
            expect(bar.get_by_role("button", name="新建审核", exact=True)).to_be_visible()
            expect(bar.get_by_role("button", name="刷新用量", exact=True)).to_have_count(0)
            self.assertLessEqual(page.get_by_role("heading", name="工作台", exact=True).bounding_box()["height"], 2)
            page.set_viewport_size({"width": 390, "height": 844})
            expect(bar.get_by_role("button", name="新建审核", exact=True)).to_be_visible()
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            bar.get_by_role("button", name="新建审核", exact=True).click()
            expect(page.get_by_label("选择审核文件", exact=True)).to_be_attached()
            browser.close()

    def test_file_upload_moves_to_the_page_bar_and_report_actions_do_not_leak(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1057, "height": 853})
            page.set_default_timeout(5000)
            page.goto(PREVIEW + "/preview/audit.html?scene=reports-ready&task=preview-report-task&report=1")
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="审核文件", exact=True).click()
            bar = page.get_by_role("banner")
            expect(bar.get_by_role("button", name="上传文件", exact=True)).to_be_visible()
            expect(bar.get_by_role("button", name="读取报告状态", exact=True)).to_have_count(0)
            title = page.get_by_role("heading", name="审核文件", exact=True)
            self.assertLessEqual(title.bounding_box()["height"], 2)
            expect(page.get_by_text("12 份文件", exact=True)).to_have_count(0)
            expect(page.get_by_text("共 12 份文档", exact=True)).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            expect(bar.get_by_role("button", name="上传文件", exact=True)).to_be_visible()
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            bar.get_by_role("button", name="上传文件", exact=True).click()
            expect(page.get_by_label("选择审核文件", exact=True)).to_be_attached()
            expect(bar.get_by_role("button", name="上传文件", exact=True)).to_have_count(0)
            browser.close()
