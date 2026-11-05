"""Archive layout and navigation using the existing controlled UI fixtures.

These checks do not prove live audit execution, persistence or billing.
"""

import os
import re
import unittest
import zipfile
from urllib.parse import parse_qs, urlparse

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None


PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")


@unittest.skipUnless(PREVIEW and sync_playwright, "requires the explicit UI preview and Playwright")
class AuditArchivePreviewTests(unittest.TestCase):
    def test_natural_file_route_restores_list_and_exact_history_on_back_and_forward(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            for path in ("/files", "/files/"):
                with self.subTest(path=path):
                    page.goto(PREVIEW + "/preview/audit.html?scene=archive-ready&view=archive")
                    table = page.get_by_role("table", name="审核文件档案", exact=True)
                    expect(table).to_be_visible()
                    # Keep the controlled adapter, but exercise the public address without a legacy view query.
                    page.evaluate("path => history.replaceState(history.state, '', path)", path)
                    page.get_by_role("checkbox", name="选择 控制计划.xlsx 档案 A001", exact=True).check()
                    table.get_by_role("row", name=re.compile("档案 A001")).get_by_role("button", name="查看审核记录", exact=True).click()
                    expect(page.get_by_role("region", name="审核详情", exact=True)).to_be_visible()
                    reference = parse_qs(urlparse(page.url).query)["auditRef"]
                    page.go_back()
                    expect(page).to_have_url(PREVIEW + path)
                    expect(table).to_be_visible()
                    expect(page.get_by_role("region", name="文档历史审核", exact=True)).to_have_count(0)
                    expect(page.get_by_role("region", name="已选固定报告", exact=True)).to_contain_text("控制计划.xlsx · 第 3 次审核")
                    page.go_forward()
                    expect(page.get_by_role("region", name="审核详情", exact=True)).to_be_visible()
                    self.assertEqual(parse_qs(urlparse(page.url).query)["auditRef"], reference)
            browser.close()

    def test_natural_file_route_remembers_the_zip_request_without_a_legacy_view_query(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=archive-ready&view=archive")
            expect(page.get_by_role("table", name="审核文件档案", exact=True)).to_be_visible()
            page.evaluate("history.replaceState(history.state, '', '/files')")
            for archive in ("A001", "A002"):
                page.get_by_role("checkbox", name=f"选择 控制计划.xlsx 档案 {archive}", exact=True).check()
            page.get_by_role("button", name="打包所选 2 份历史报告", exact=True).click()
            expect(page.get_by_role("region", name="ZIP 工作", exact=True)).to_be_visible()
            expect(page).to_have_url(re.compile(r"/files\?packageRequest=[^&]+$"))
            request = parse_qs(urlparse(page.url).query)["packageRequest"]
            page.get_by_role("button", name="清空选择", exact=True).click()
            page.get_by_role("button", name="读取 ZIP 状态", exact=True).click()
            page.get_by_role("region", name="ZIP 工作", exact=True).get_by_role("button", name="打包记录", exact=True).click()
            record = page.get_by_role("dialog", name="打包记录", exact=True)
            expect(record.get_by_role("table", name="固定打包报告", exact=True).get_by_role("row")).to_have_count(3)
            with page.expect_download() as download:
                record.get_by_role("button", name="下载已就绪 ZIP", exact=True).click()
            with zipfile.ZipFile(download.value.path()) as package:
                self.assertEqual(len([name for name in package.namelist() if name.endswith(".pdf")]), 2)
                manifest = package.read("报告清单.txt").decode("utf-8")
                self.assertIn("preview-archive-task-A001-3", manifest)
                self.assertIn("preview-archive-task-A002-1", manifest)
                self.assertNotIn("preview-archive-task-A001-4", manifest)
            self.assertEqual(parse_qs(urlparse(page.url).query)["packageRequest"], request)
            browser.close()

    def test_explicit_history_reads_start_together_but_wait_for_selection_before_display(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=archive-ready&view=archive&archiveDoc=preview-archive-A001")
            expect(page.get_by_role("region", name="审核详情", exact=True)).to_be_visible()
            address = page.url.replace("scene=archive-ready", "scene=archive-history-loads")
            for first, fail_detail in (("历史审核详情", False), ("历史审核清单", False), ("历史审核详情", True)):
                with self.subTest(first=first, fail_detail=fail_detail):
                    page.goto(address)
                    events = page.get_by_role("complementary", name="预览事件控制", exact=True)
                    expect(events.get_by_text("历史审核清单", exact=True)).to_have_count(1)
                    expect(events.get_by_text("历史审核详情", exact=True)).to_have_count(1)
                    self.assertEqual(parse_qs(urlparse(page.url).query)["auditRef"], parse_qs(urlparse(address).query)["auditRef"])
                    event = events.get_by_text(first, exact=True).locator("..")
                    event.get_by_role("button", name=re.compile("令示例读取 .* 失败" if fail_detail else "返回示例读取 ")).click()
                    expect(page.get_by_role("region", name="审核详情", exact=True)).to_have_count(0)
                    events.get_by_role("button", name=re.compile("返回示例读取 ")).click()
                    if fail_detail:
                        expect(page.get_by_role("alert")).to_contain_text("这次审核暂时无法读取")
                        page.get_by_role("button", name="重新读取这次审核", exact=True).click()
                        expect(events.get_by_text("历史审核清单", exact=True)).to_have_count(0)
                        expect(events.get_by_text("历史审核详情", exact=True)).to_have_count(1)
                        events.get_by_role("button", name=re.compile("返回示例读取 ")).click()
                    expect(page.get_by_role("region", name="审核详情", exact=True)).to_be_visible()
                    # Only the displayed result starts the read of the earlier audit it is compared with.
                    expect(events.get_by_text("历史审核详情", exact=True)).to_have_count(1)
                    events.get_by_role("button", name=re.compile("返回示例读取 ")).click()
                    expect(page.get_by_role("region", name="与上次审核对比", exact=True)).to_contain_text("与第 2 次审核相比")
                    expect(events.get_by_role("button")).to_have_count(0)
            browser.close()

    def test_history_deep_link_does_not_mount_twice_or_read_the_hidden_archive(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=archive-history-loads&view=archive&archiveDoc=preview-archive-A001")
            events = page.get_by_role("complementary", name="预览事件控制", exact=True)
            expect(events.get_by_text("历史审核清单", exact=True)).to_have_count(1)
            expect(events.get_by_text("文件档案清单", exact=True)).to_have_count(0)
            expect(events.get_by_text("审核任务清单", exact=True)).to_have_count(0)
            events.get_by_role("button", name=re.compile("返回示例读取 ")).click()
            expect(events.get_by_text("历史审核详情", exact=True)).to_have_count(1)
            events.get_by_role("button", name=re.compile("返回示例读取 ")).click()
            expect(page.get_by_role("region", name="审核详情", exact=True)).to_be_visible()
            expect(events.get_by_text("历史审核详情", exact=True)).to_have_count(1)
            events.get_by_role("button", name=re.compile("返回示例读取 ")).click()
            expect(events.get_by_role("button")).to_have_count(0)
            page.get_by_role("button", name="返回文件档案", exact=True).click()
            expect(events.get_by_text("文件档案清单", exact=True)).to_have_count(1)
            events.get_by_role("button", name=re.compile("返回示例读取 ")).click()
            expect(page.get_by_role("table", name="审核文件档案", exact=True)).to_be_visible()
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="工作台", exact=True).click()
            expect(events.get_by_text("审核任务清单", exact=True)).to_have_count(1)
            expect(events.get_by_text("文件档案清单", exact=True)).to_have_count(0)
            browser.close()

    def test_file_table_owns_the_width_and_keeps_actions_on_one_line(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for width in (1440, 1079, 390):
                with self.subTest(width=width):
                    page = browser.new_page(viewport={"width": width, "height": 960})
                    page.goto(PREVIEW + "/preview/audit.html?scene=archive-ready&view=archive")
                    table = page.get_by_role("table", name="审核文件档案", exact=True)
                    expect(table).to_be_visible()
                    expect(page.get_by_role("navigation", name="车型与零件", exact=True)).to_have_count(0)
                    first = table.get_by_role("row", name=re.compile("档案 A001"))
                    filename = first.get_by_text("控制计划.xlsx", exact=True)
                    expect(filename).to_be_visible()
                    self.assertLess(filename.bounding_box()["height"], 30)
                    self.assertGreater(table.bounding_box()["width"] / page.get_by_role("main").bounding_box()["width"], 0.9)
                    self.assertGreater(first.get_by_role("cell").nth(1).bounding_box()["width"], 250 if width < 1280 else 400)
                    for action in table.get_by_role("button", name="查看审核记录", exact=True).all():
                        if action.is_visible():
                            self.assertEqual(action.evaluate("element => getComputedStyle(element).whiteSpace"), "nowrap")
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                    if width >= 1024:
                        # The shown audit time belongs to completed round 3, not active round 4.
                        expect(first.locator("time")).to_have_attribute("datetime", "2026-10-15T09:00:00+08:00")
                    page.close()
            browser.close()

    def test_classification_is_on_demand_and_returns_keyboard_focus(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1079, "height": 853})
            page.goto(PREVIEW + "/preview/audit.html?scene=archive-ready&view=archive")
            trigger = page.get_by_role("button", name="车型与零件", exact=True)
            trigger.focus()
            page.keyboard.press("Enter")
            panel = page.get_by_role("dialog", name="车型与零件", exact=True)
            panel.get_by_role("button", name="尾门导流板 2", exact=True).click()
            expect(panel).to_have_count(0)
            expect(trigger).to_be_focused()
            expect(page.get_by_role("table", name="审核文件档案", exact=True).get_by_role("row")).to_have_count(3)
            expect(page.get_by_role("button", name="移除分类筛选", exact=True)).to_contain_text("CETUS / 尾门导流板")
            page.get_by_role("button", name="移除分类筛选", exact=True).click()
            expect(page.get_by_text("共 12 份文档", exact=True)).to_be_visible()
            trigger.click()
            page.keyboard.press("Escape")
            expect(panel).to_have_count(0)
            expect(trigger).to_be_focused()
            browser.close()

    def test_filters_search_and_history_return_keep_selected_report_versions(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            page.goto(PREVIEW + "/preview/audit.html?scene=archive-ready&view=archive")
            page.get_by_role("checkbox", name="选择 控制计划.xlsx 档案 A001", exact=True).check()
            selected = page.get_by_role("region", name="已选固定报告", exact=True)
            expect(selected.get_by_text("控制计划.xlsx · 第 3 次审核", exact=True)).to_be_visible()
            page.get_by_role("button", name="下一页", exact=True).click()
            expect(page.get_by_text("第 2 页 · 每页 10 份", exact=True)).to_be_visible()
            page.get_by_role("button", name="更多筛选", exact=True).click()
            panel = page.get_by_role("dialog", name="筛选审核文件", exact=True)
            panel.get_by_role("combobox", name="全部客户", exact=True).click()
            page.get_by_role("option", name="示例客户 B", exact=True).click()
            panel.get_by_role("button", name="查看筛选结果", exact=True).click()
            expect(page.get_by_text("第 1 页 · 每页 10 份", exact=True)).to_be_visible()
            table = page.get_by_role("table", name="审核文件档案", exact=True)
            expect(table.get_by_role("row")).to_have_count(2)
            expect(table.get_by_role("checkbox", name="选择 控制计划.xlsx 档案 A002", exact=True)).not_to_be_checked()
            expect(selected.get_by_text("控制计划.xlsx · 第 3 次审核", exact=True)).to_be_visible()
            table.get_by_role("button", name="查看审核记录", exact=True).click()
            expect(page.get_by_text("原件修订：未提供修订标识", exact=True)).to_be_visible()
            page.get_by_role("button", name="返回文件档案", exact=True).click()
            expect(table.get_by_role("row")).to_have_count(2)
            expect(selected.get_by_text("控制计划.xlsx · 第 3 次审核", exact=True)).to_be_visible()
            page.get_by_role("button", name="移除客户筛选", exact=True).click()
            page.get_by_role("textbox", name="搜索文档名", exact=True).fill("PFMEA")
            page.get_by_role("button", name="搜索", exact=True).click()
            expect(table.get_by_role("row")).to_have_count(2)
            expect(table.get_by_text("PFMEA.xlsx", exact=True)).to_be_visible()
            expect(selected.get_by_role("button", name="下载所选 1 份历史报告", exact=True)).to_be_enabled()
            page.get_by_role("button", name="清除筛选", exact=True).click()
            expect(page.get_by_role("checkbox", name="选择 控制计划.xlsx 档案 A001", exact=True)).to_be_checked()
            browser.close()

    def test_history_uses_one_filename_and_a_full_width_result_reader(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for width in (1440, 1079, 390):
                with self.subTest(width=width):
                    page = browser.new_page(viewport={"width": width, "height": 960})
                    page.goto(PREVIEW + "/preview/audit.html?scene=archive-ready&view=archive&archiveDoc=preview-archive-A001")
                    heading = page.get_by_role("heading", name="控制计划.xlsx", exact=True)
                    expect(heading).to_be_visible()
                    expect(page.get_by_role("main").get_by_text("控制计划.xlsx", exact=True)).to_have_count(1)
                    expect(page.get_by_role("navigation", name="历次审核", exact=True)).to_have_count(0)
                    expect(page.get_by_role("button", name="历史审核记录", exact=True)).to_contain_text("第 3 次审核")
                    details = page.get_by_role("region", name="审核详情", exact=True)
                    expect(details.get_by_role("button", name="查看报告", exact=True)).to_be_enabled()
                    expect(details.get_by_text("不符合 0 · 符合 2", exact=True)).to_be_visible()
                    if width >= 1024:
                        self.assertLess(details.get_by_role("table").bounding_box()["y"] - heading.bounding_box()["y"], 420)
                    else:
                        self.assertGreater(heading.bounding_box()["width"], 240)
                        self.assertLess(heading.bounding_box()["height"], 32)
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                    page.close()
            browser.close()

    def test_history_selector_keeps_exact_old_reference_through_reload_and_report(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1079, "height": 853})
            page.goto(PREVIEW + "/preview/audit.html?scene=archive-task-allowed&view=archive&archiveDoc=preview-archive-A001")
            trigger = page.get_by_role("button", name="历史审核记录", exact=True)
            expect(trigger).to_contain_text("第 3 次审核")
            trigger.focus()
            page.keyboard.press("Enter")
            panel = page.get_by_role("dialog", name="历史审核记录", exact=True)
            expect(panel.get_by_role("navigation", name="历次审核", exact=True).get_by_role("button")).to_have_count(4)
            expect(panel.get_by_role("button", name="选择第 3 次审核", exact=True)).to_have_attribute("aria-pressed", "true")
            expect(panel.get_by_role("button", name="选择第 1 次审核", exact=True)).to_contain_text("审核异常")
            panel.get_by_role("button", name="选择第 2 次审核", exact=True).click()
            expect(panel).to_have_count(0)
            expect(trigger).to_be_focused()
            expect(page.get_by_text("原件修订：Rev B", exact=True)).to_be_visible()
            old_reference = parse_qs(urlparse(page.url).query)["auditRef"]
            page.reload()
            expect(page.get_by_text("原件修订：Rev B", exact=True)).to_be_visible()
            self.assertEqual(parse_qs(urlparse(page.url).query)["auditRef"], old_reference)
            page.get_by_role("button", name="查看报告", exact=True).click()
            report = page.get_by_role("region", name="历史报告", exact=True)
            preview = report.get_by_role("region", name="当前报告", exact=True)
            expect(preview.get_by_role("button", name="分享", exact=True)).to_be_visible()
            self.assertLess(preview.bounding_box()["y"] - report.bounding_box()["y"], 64)
            expect(report.get_by_text("第 2 次审核 · 原件修订 Rev B", exact=True)).to_have_count(0)
            info = preview.get_by_role("button", name="报告信息", exact=True)
            info.click()
            information = page.get_by_role("dialog", name="报告信息", exact=True)
            expect(information.get_by_text("第 2 次审核", exact=True)).to_be_visible()
            expect(information.get_by_text("Rev B", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(info).to_be_focused()
            for width in (390, 1079):
                page.set_viewport_size({"width": width, "height": 853})
                expect(preview.get_by_role("button", name="报告信息", exact=True)).to_be_visible()
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                self.assertEqual(parse_qs(urlparse(page.url).query)["auditRef"], old_reference)
            page.get_by_role("button", name="返回本次审核", exact=True).click()
            expect(trigger).to_contain_text("第 2 次审核")
            expect(page.get_by_role("button", name="打开来源任务", exact=True)).to_be_enabled()
            self.assertEqual(parse_qs(urlparse(page.url).query)["auditRef"], old_reference)
            browser.close()

    def test_mobile_filter_sheet_keeps_dates_and_controls_inside_the_viewport(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(PREVIEW + "/preview/audit.html?scene=archive-ready&view=archive&theme=dark")
            trigger = page.get_by_role("button", name="更多筛选", exact=True)
            trigger.click()
            panel = page.get_by_role("dialog", name="筛选审核文件", exact=True)
            panel.get_by_label("开始日期", exact=True).fill("2026-10-01")
            panel.get_by_label("结束日期", exact=True).fill("2026-10-20")
            self.assertTrue(panel.evaluate("element => element.scrollWidth <= element.clientWidth"))
            panel.get_by_role("button", name="查看筛选结果", exact=True).click()
            expect(trigger).to_contain_text("2")
            expect(page.get_by_role("button", name="移除开始日期筛选", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="移除结束日期筛选", exact=True)).to_be_visible()
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            browser.close()
