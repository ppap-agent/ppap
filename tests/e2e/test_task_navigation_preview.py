"""Controlled navigation interactions, not backend or audit acceptance evidence."""

import os
import re
import unittest
from urllib.parse import parse_qs, urlparse

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")


@unittest.skipUnless(PREVIEW and sync_playwright, "requires PPAP_PREVIEW_URL and the e2e dependency group (preview-only UI)")
class TaskNavigationPreviewTests(unittest.TestCase):
    def test_notification_report_link_skips_a_leading_reference_but_keeps_explicit_identity(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(5000)
            address = PREVIEW + "/preview/audit.html?scene=reports-reference-first&task=preview-report-task&report=1"
            page.goto(address)
            current = page.get_by_role("region", name="当前报告", exact=True)
            expect(current.get_by_role("heading", name="控制计划.xlsx", exact=True)).to_be_visible()
            expect(current.get_by_role("toolbar", name="PDF 阅读工具", exact=True)).to_be_visible()
            table = page.get_by_role("list", name="逐文件报告清单", exact=True)
            expect(table.get_by_text("特殊特性清单.xlsx", exact=True)).to_have_count(0)
            page.reload()
            expect(current.get_by_role("heading", name="控制计划.xlsx", exact=True)).to_be_visible()
            page.goto(address.replace("report=1", "report=preview-recovery-version-0"))
            expect(page.get_by_text("所选报告不可用，请从清单中重新选择。", exact=True)).to_be_visible()
            expect(current.get_by_role("toolbar", name="PDF 阅读工具", exact=True)).to_have_count(0)
            page.go_back()
            expect(current.get_by_role("heading", name="控制计划.xlsx", exact=True)).to_be_visible()
            browser.close()

    def test_report_breadcrumb_clears_the_host_preview_report_address_before_reload(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(5000)
            page.goto(PREVIEW + "/__preview?page=audit&scene=reports-ready&task=preview-report-task&report=1&packageRequest=original-package")
            product = page.frame_locator("#product")
            product.get_by_role("link", name="返回审核结果", exact=True).click()
            expect(page).not_to_have_url(re.compile(r"[?&](?:report|packageRequest)=[^&]+"))
            self.assertEqual(parse_qs(urlparse(page.url).query)["task"], ["preview-report-task"])
            page.reload()
            expect(product.get_by_role("heading", name="审核报告", exact=True)).to_have_count(0)
            expect(product.get_by_role("heading", name="审核结果", exact=True)).to_be_visible()
            browser.close()

    def test_mobile_global_menu_and_task_switcher_have_separate_content_and_focus(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 768, "height": 1024})
            page.set_default_timeout(5000)
            page.goto(PREVIEW + "/preview/audit.html?scene=restore-notification-tasks&task=preview-recovery-task")
            menu_trigger = page.get_by_role("button", name="打开产品导航", exact=True)
            menu_trigger.click()
            menu = page.get_by_role("dialog", name="产品导航", exact=True)
            expect(menu.get_by_role("navigation", name="产品导航", exact=True)).to_be_visible()
            for label in ("工作台", "审核文件", "用量记录", "联系我们"):
                expect(menu.get_by_role("button", name=label, exact=True)).to_be_visible()
            expect(menu.get_by_role("link", name="文件模板（在飞书新窗口打开）", exact=True)).to_be_visible()
            expect(menu.get_by_role("navigation", name="任务列表", exact=True)).to_have_count(0)
            expect(menu.get_by_role("button", name="新建审核任务", exact=True)).to_have_count(0)
            page.keyboard.press("Escape")
            expect(menu_trigger).to_be_focused()
            task_trigger = page.get_by_role("button", name="打开审核任务", exact=True)
            task_trigger.click()
            switcher = page.get_by_role("dialog", name="审核任务", exact=True)
            expect(switcher.get_by_role("navigation", name="任务列表", exact=True)).to_be_visible()
            expect(switcher.get_by_role("navigation", name="产品导航", exact=True)).to_have_count(0)
            expect(switcher.get_by_role("button", name="联系我们", exact=True)).to_have_count(0)
            page.keyboard.press("Escape")
            expect(task_trigger).to_be_focused()
            browser.close()

    def test_task_switcher_shows_current_filenames_and_recent_tasks_and_keeps_search(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(5000)
            page.goto(PREVIEW + "/preview/audit.html?scene=restore-notification-tasks&task=preview-recovery-task")
            page.get_by_role("button", name="打开审核任务", exact=True).click()
            switcher = page.get_by_role("dialog", name="审核任务", exact=True)
            expect(switcher.get_by_role("heading", name="当前任务", exact=True)).to_be_visible()
            expect(switcher.get_by_role("heading", name="最近任务", exact=True)).to_be_visible()
            listing = switcher.get_by_role("navigation", name="任务列表", exact=True)
            expect(listing.get_by_role("button")).to_have_count(2)
            current = listing.get_by_role("button").filter(has_text="PPAP-恢复示例")
            for filename in ("PFMEA.xlsx", "控制计划.xlsx", "过程流程图.pdf"):
                expect(current.get_by_text(filename, exact=True)).to_be_visible()
            switcher.get_by_role("textbox", name="搜索任务文件", exact=True).fill("第二个任务")
            other = listing.get_by_role("button", name=re.compile(r"^PPAP-第二个任务(?:\s|$)"))
            expect(other).to_have_count(1)
            other.click()
            expect(switcher).not_to_be_visible()
            expect(page).to_have_url(PREVIEW + "/preview/audit.html?scene=restore-notification-tasks&task=preview-second-task")
            page.get_by_role("button", name="打开审核任务", exact=True).click()
            switcher.get_by_role("textbox", name="搜索任务文件", exact=True).fill("控制计划.xlsx")
            expect(listing.get_by_role("button")).to_have_count(2)
            page.keyboard.press("Escape")
            browser.close()

    def test_report_breadcrumb_returns_locally_without_discarding_result_context(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(5000)
            page.goto(
                PREVIEW + "/preview/audit.html?scene=reports-ready&task=preview-report-task&report=preview-recovery-version-1"
                "&packageRequest=original-package&file=preview-recovery-version-1&clause=2&focus=compare"
            )
            trail = page.get_by_role("navigation", name="当前位置", exact=True)
            expect(trail.get_by_role("link", name="工作台", exact=True)).to_be_visible()
            result_link = trail.get_by_role("link", name="返回审核结果", exact=True)
            expect(result_link).to_have_text("PPAP-报告示例")
            expect(trail.get_by_text("审核报告", exact=True)).to_be_visible()
            expect(page.locator("#audit-main").get_by_role("button", name="工作台", exact=True)).to_have_count(0)
            result_link.click()
            query = parse_qs(urlparse(page.url).query)
            self.assertNotIn("report", query)
            self.assertNotIn("packageRequest", query)
            self.assertEqual(query["task"], ["preview-report-task"])
            self.assertEqual(query["file"], ["preview-recovery-version-1"])
            self.assertEqual(query["clause"], ["2"])
            self.assertEqual(query["focus"], ["compare"])
            expect(page.get_by_role("heading", name="审核报告", exact=True)).to_have_count(0)
            expect(page.get_by_role("navigation", name="文件队列", exact=True)).to_be_visible()
            page.go_back()
            expect(trail.get_by_text("审核报告", exact=True)).to_be_visible()
            browser.close()
