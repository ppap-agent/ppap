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

    def test_task_rail_has_only_task_content_and_folds_without_losing_the_task(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(5000)
            page.goto(PREVIEW + "/preview/audit.html?scene=restore-notification-tasks&task=preview-recovery-task")
            rail = page.get_by_role("complementary", name="审核任务", exact=True)
            listing = rail.get_by_role("navigation", name="审核任务列表", exact=True)
            expect(listing.get_by_role("heading", name="审核任务", exact=True)).to_be_visible()
            expect(rail.get_by_role("navigation", name="产品导航", exact=True)).to_have_count(0)
            expect(rail.get_by_role("button", name="联系我们", exact=True)).to_have_count(0)
            listing.get_by_role("textbox", name="搜索审核任务", exact=True).fill("恢复示例")
            listing.get_by_role("button", name="收起", exact=True).click()
            expect(listing).to_have_count(0)
            expand = rail.get_by_role("button", name="展开审核任务", exact=True)
            expect(expand).to_be_visible()
            expect(page.get_by_role("region", name="全部文件概览", exact=True)).to_be_visible()
            expand.click()
            expect(listing.get_by_role("textbox", name="搜索审核任务", exact=True)).to_have_value("恢复示例")
            expect(listing.get_by_role("button", name=re.compile(r"^PPAP-恢复示例(?:\s|$)"))).to_have_attribute("aria-current", "page")
            self.assertEqual(parse_qs(urlparse(page.url).query)["task"], ["preview-recovery-task"])
            browser.close()

    def test_task_rail_marks_the_current_task_and_keeps_search_across_tasks(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(5000)
            page.goto(PREVIEW + "/preview/audit.html?scene=restore-notification-tasks&task=preview-recovery-task")
            listing = page.get_by_role("complementary", name="审核任务", exact=True).get_by_role("navigation", name="审核任务列表", exact=True)
            rows = listing.get_by_role("list").get_by_role("button")
            expect(rows).to_have_count(2)
            current = listing.get_by_role("button", name=re.compile(r"^PPAP-恢复示例(?:\s|$)"))
            expect(current).to_have_attribute("aria-current", "page")
            expect(current).to_contain_text("共 3 份文件")
            search = listing.get_by_role("textbox", name="搜索审核任务", exact=True)
            search.fill("第二个任务")
            other = listing.get_by_role("button", name=re.compile(r"^PPAP-第二个任务(?:\s|$)"))
            expect(rows).to_have_count(1)
            other.click()
            expect(page).to_have_url(PREVIEW + "/preview/audit.html?scene=restore-notification-tasks&task=preview-second-task")
            expect(other).to_have_attribute("aria-current", "page")
            expect(search).to_have_value("第二个任务")
            # A filename shared by both tasks finds both.
            search.fill("控制计划.xlsx")
            expect(rows).to_have_count(2)
            expect(current).not_to_have_attribute("aria-current", "page")
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
            expect(trail.get_by_role("link", name="PPAP Audit", exact=True)).to_be_visible()
            result_link = trail.get_by_role("link", name="返回审核结果", exact=True)
            expect(result_link).to_have_text("PPAP-报告示例")
            expect(trail.get_by_text("审核报告", exact=True)).to_be_visible()
            expect(page.locator("#audit-main").get_by_role("button", name="PPAP Audit", exact=True)).to_have_count(0)
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
