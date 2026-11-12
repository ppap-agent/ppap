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
    def test_loading_a_report_does_not_restore_the_duplicate_page_title(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1057, "height": 853})
            page.set_default_timeout(5000)
            page.goto(PREVIEW + "/preview/audit.html?scene=restore-loading&task=preview-recovery-task&report=preview-recovery-version-1")
            bar = page.get_by_role("banner")
            expect(bar.get_by_role("listitem").filter(has_text="审核报告")).to_be_visible()
            recovery = page.get_by_role("region", name="报告读取状态", exact=True)
            title = recovery.get_by_role("heading", name="审核报告", exact=True)
            self.assertLessEqual(title.bounding_box()["height"], 2)
            status = recovery.get_by_role("status")
            expect(status).to_have_text("正在打开报告…")
            bottom = bar.bounding_box()["y"] + bar.bounding_box()["height"]
            self.assertLessEqual(status.bounding_box()["y"] - bottom, 32)
            page.get_by_role("button", name="令示例读取 1 失败", exact=True).click()
            expect(recovery.get_by_role("heading", name="报告暂时无法打开", exact=True)).to_be_visible()
            self.assertGreater(recovery.get_by_role("heading", name="报告暂时无法打开", exact=True).bounding_box()["height"], 2)
            browser.close()

    def test_return_to_historical_round_removes_the_hidden_task_report_actions(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1057, "height": 853})
            page.set_default_timeout(5000)
            address = PREVIEW + "/preview/audit.html?scene=reports-ready&round=history-round"
            page.goto(address)
            expect(page.get_by_role("region", name="历史审核", exact=True)).to_be_visible()
            bar = page.get_by_role("banner")
            title = page.get_by_role("heading", name="历史审核", exact=True)
            self.assertLessEqual(title.bounding_box()["height"], 2)
            bar.get_by_role("button", name="历史操作", exact=True).click()
            expect(page.get_by_role("menuitem", name="返回工作台", exact=True)).to_have_attribute("href", "/workbench")
            page.get_by_role("menuitem", name="重新读取", exact=True).click()
            expect(page).to_have_url(address)
            page.get_by_role("button", name="打开审核任务", exact=True).click()
            page.get_by_role("navigation", name="任务列表", exact=True).get_by_role("button").filter(has_text="PPAP-报告示例").click()
            page.get_by_role("button", name="审核报告", exact=True).click()
            bar = page.get_by_role("banner")
            expect(bar.get_by_role("button", name="读取报告状态", exact=True)).to_be_visible()
            expect(bar.get_by_role("button", name="历史操作", exact=True)).to_have_count(0)
            page.go_back()
            expect(page).to_have_url(address)
            expect(page.get_by_role("region", name="历史审核", exact=True)).to_be_visible()
            expect(bar.get_by_role("button", name="读取报告状态", exact=True)).to_have_count(0)
            expect(page.get_by_role("navigation", name="当前位置", exact=True).get_by_text("历史审核", exact=True)).to_be_visible()
            expect(bar.get_by_role("link", name="返回审核结果", exact=True)).to_have_count(0)
            expect(bar.get_by_role("button", name="历史操作", exact=True)).to_be_visible()
            page.go_forward()
            expect(page.get_by_role("region", name="当前报告", exact=True)).to_be_visible()
            expect(bar.get_by_role("link", name="返回审核结果", exact=True)).to_have_text("PPAP-报告示例")
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
            # The page title heads the report list beside the reader, not a second row above both.
            title = page.get_by_role("heading", name="审核报告", exact=True)
            expect(title).to_be_visible()
            current = page.get_by_role("region", name="当前报告", exact=True)
            filename = current.get_by_role("heading", name="控制计划.xlsx", exact=True)
            toolbar = current.get_by_role("toolbar", name="PDF 阅读工具", exact=True)
            expect(toolbar).to_be_visible()
            bottom = bar.bounding_box()["y"] + bar.bounding_box()["height"]
            reader = current.bounding_box()
            self.assertLessEqual(reader["y"] - bottom, 32)
            # The list title and the reader start on the same row, side by side.
            self.assertLessEqual(abs(title.bounding_box()["y"] - reader["y"]), 16)
            self.assertLess(title.bounding_box()["x"] + title.bounding_box()["width"], reader["x"])
            # Only the reader's own header (report name, time, actions; it wraps at this width) sits above the PDF tools.
            self.assertLess(filename.bounding_box()["y"], toolbar.bounding_box()["y"])
            self.assertLessEqual(toolbar.bounding_box()["y"] - reader["y"], 128)
            browser.close()

    def test_workbench_and_usage_share_the_page_bar_without_previous_page_actions(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1057, "height": 853})
            page.set_default_timeout(5000)
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-ready&view=usage")
            bar = page.get_by_role("banner")
            expect(bar.get_by_role("button", name="刷新用量", exact=True)).to_be_visible()
            # The page keeps its own title and description below the shared bar.
            expect(page.get_by_role("heading", name="用量记录", exact=True)).to_be_visible()
            expect(page.get_by_text("记录每份文件的审核消耗，可按时间范围筛选并导出明细", exact=True)).to_be_visible()
            expect(page.get_by_role("region", name="自然月用量汇总", exact=True)).to_be_visible()
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="PPAP Audit", exact=True).click()
            expect(bar.get_by_role("button", name="新建审核", exact=True)).to_be_visible()
            expect(bar.get_by_role("button", name="刷新用量", exact=True)).to_have_count(0)
            self.assertLessEqual(page.get_by_role("heading", name="工作台", exact=True).bounding_box()["height"], 2)
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
            expect(page.get_by_role("heading", name="审核文件", level=1, exact=True)).to_be_visible()
            expect(page.get_by_text("12 份文件", exact=True)).to_have_count(0)
            expect(page.get_by_text("共 12 份文件", exact=True)).to_be_visible()
            bar.get_by_role("button", name="上传文件", exact=True).click()
            expect(page.get_by_label("选择审核文件", exact=True)).to_be_attached()
            expect(bar.get_by_role("button", name="上传文件", exact=True)).to_have_count(0)
            browser.close()
