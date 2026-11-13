"""Current task read-only result interactions, using existing DTO fixtures."""

import os
import re
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None


PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")


@unittest.skipUnless(PREVIEW and sync_playwright, "requires the explicit UI preview and Playwright")
class AuditWorkspacePreviewTests(unittest.TestCase):
    def test_browser_back_and_forward_restore_overview_and_exact_file_report(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            page.goto(PREVIEW + "/preview/audit.html?scene=reports-ready&task=preview-report-task")
            page.get_by_role("navigation", name="文件队列", exact=True).get_by_role("button").nth(1).click()
            page.get_by_role("button", name="查看报告", exact=True).click()
            report_address = page.url
            expect(page.get_by_role("button", name="下载当前报告", exact=True)).to_be_enabled()
            page.go_back()
            expect(page.get_by_role("region", name="全部文件概览", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="下载当前报告", exact=True)).to_have_count(0)
            page.go_forward()
            expect(page).to_have_url(report_address)
            expect(page.get_by_role("button", name="下载当前报告", exact=True)).to_be_enabled()
            browser.close()

    def test_complete_task_starts_with_every_file_then_restores_focused_review(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            page.goto(PREVIEW + "/preview/audit.html?scene=reports-ready&task=preview-report-task")
            expect(page.get_by_role("region", name="全部文件概览", exact=True)).to_be_visible()
            expect(page.get_by_role("navigation", name="文件队列", exact=True).get_by_role("button")).to_have_count(3)
            expect(page.get_by_role("region", name="审核详情", exact=True)).to_have_count(0)
            page.get_by_role("navigation", name="文件队列", exact=True).get_by_role("button").first.click()
            page.get_by_role("button", name="逐条复核", exact=True).click()
            focus = page.get_by_role("region", name="专注复核", exact=True)
            expect(focus.get_by_role("combobox", name="条款目录", exact=True)).to_be_visible()
            focus.get_by_role("button", name="下一待复核", exact=True).click()
            expect(focus.get_by_role("heading", name="第 2 条 · 记录完整性", exact=True)).to_be_visible()
            address = page.url
            page.reload()
            expect(page).to_have_url(address)
            expect(focus.get_by_role("heading", name="第 2 条 · 记录完整性", exact=True)).to_be_visible()
            focus.get_by_role("combobox", name="选择对照原件", exact=True).click()
            page.get_by_role("option", name="控制计划.xlsx", exact=True).click()
            expect(focus.get_by_role("region", name="原件预览", exact=True)).to_contain_text("控制计划.xlsx")
            page.get_by_role("button", name="返回审核项", exact=True).click()
            expect(page.get_by_role("region", name="审核详情", exact=True).get_by_role("table")).to_be_visible()
            browser.close()

    def test_leaving_the_task_and_returning_preserves_visible_clause_filter_and_focus(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            page.goto(PREVIEW + "/preview/audit.html?scene=reports-ready&task=preview-report-task")
            page.get_by_role("navigation", name="文件队列", exact=True).get_by_role("button").first.click()
            page.get_by_role("group", name="审核结果筛选", exact=True).get_by_role("button", name=re.compile("^不符合")).click()
            page.get_by_role("button", name="逐条复核", exact=True).click()
            focus = page.get_by_role("region", name="专注复核", exact=True)
            focus.get_by_role("button", name="下一待复核", exact=True).click()
            expect(focus.get_by_role("heading", name="第 2 条 · 记录完整性", exact=True)).to_be_visible()
            address = page.url
            navigation = page.get_by_role("navigation", name="产品导航", exact=True)
            navigation.get_by_role("button", name="用量记录", exact=True).click()
            expect(page.get_by_role("heading", name="用量记录", exact=True)).to_be_visible()
            page.go_back()
            expect(page).to_have_url(address)
            expect(focus.get_by_role("heading", name="第 2 条 · 记录完整性", exact=True)).to_be_visible()
            # PPAP Audit in the product navigation returns to the same task position.
            navigation.get_by_role("button", name="审核文件", exact=True).click()
            expect(page.get_by_role("heading", name="审核文件", level=1, exact=True)).to_be_visible()
            navigation.get_by_role("button", name="PPAP Audit", exact=True).click()
            expect(page).to_have_url(address)
            expect(focus.get_by_role("heading", name="第 2 条 · 记录完整性", exact=True)).to_be_visible()
            page.get_by_role("button", name="返回审核项", exact=True).click()
            expect(page.get_by_role("group", name="审核结果筛选", exact=True).get_by_role("button", name=re.compile("^不符合"))).to_have_attribute("aria-pressed", "true")
            browser.close()

    def test_escape_closes_only_the_display_editor_and_returns_focus_to_file_info(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 844})
            page.goto(PREVIEW + "/preview/audit.html?scene=restore-display-ready&task=preview-recovery-task")
            page.get_by_role("navigation", name="文件队列", exact=True).get_by_role("button").first.click()
            page.get_by_role("button", name="文件信息", exact=True).click()
            info = page.get_by_role("dialog", name="文件信息", exact=True)
            edit = info.get_by_role("button", name="更正显示信息", exact=True)
            edit.focus()
            page.keyboard.press("Enter")
            editor = page.get_by_role("dialog", name="更正显示信息", exact=True)
            expect(editor.get_by_role("textbox", name="项目名称", exact=True)).to_be_focused()
            page.keyboard.press("Escape")
            expect(editor).to_have_count(0)
            expect(info).to_be_visible()
            expect(edit).to_be_focused()
            browser.close()

    def test_escape_from_manual_review_keeps_the_evidence_sheet_and_its_focus(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1079, "height": 853})
            page.goto(PREVIEW + "/preview/audit.html?scene=restore-review-edit&task=preview-recovery-task")
            page.get_by_role("navigation", name="文件队列", exact=True).get_by_role("button").first.click()
            page.get_by_role("button", name="查看依据 1.1", exact=True).click()
            basis = page.get_by_role("dialog").filter(has=page.get_by_role("tab", name="核对原件", exact=True))
            trigger = basis.get_by_role("button", name="记录人工复核", exact=True)
            trigger.click()
            editor = page.get_by_role("dialog", name="人工复核", exact=True)
            expect(editor.get_by_role("heading", name="人工复核", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(editor).to_have_count(0)
            expect(basis).to_be_visible()
            expect(trigger).to_be_focused()
            browser.close()

    def test_each_clause_opens_its_own_basis_and_restores_keyboard_focus(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1079, "height": 853})
            page.goto(PREVIEW + "/preview/audit.html?scene=reports-ready&task=preview-report-task")
            first = page.get_by_role("button", name="查看依据 1.1", exact=True)
            page.get_by_role("navigation", name="文件队列", exact=True).get_by_role("button").first.click()
            page.get_by_role("group", name="审核结果筛选", exact=True).get_by_role("button", name=re.compile("^全部")).click()
            first.click()
            basis = page.get_by_role("dialog").filter(has=page.get_by_role("tab", name="核对原件", exact=True))
            expect(basis.get_by_role("heading", name="第 1 条 · 频次一致性", exact=True)).to_be_visible()
            expect(basis.get_by_text("示例：控制计划与 PFMEA 的频次不同。", exact=False).first).to_be_visible()
            expect(basis.get_by_role("heading", name="审核规则原文", exact=True)).to_be_visible()
            basis.get_by_role("button", name="关闭", exact=True).click()
            expect(first).to_be_focused()
            second = page.get_by_role("button", name="查看依据 1.2", exact=True)
            second.click()
            expect(basis.get_by_role("heading", name="第 2 条 · 记录完整性", exact=True)).to_be_visible()
            expect(basis.get_by_text("示例：控制计划与 PFMEA 的频次不同。", exact=False)).to_have_count(0)
            page.keyboard.press("Escape")
            expect(basis).to_have_count(0)
            expect(second).to_be_focused()
            # Wide screens dock the evidence beside the list; the list stays usable.
            page.set_viewport_size({"width": 1440, "height": 960})
            first.click()
            expect(page.get_by_role("heading", name=re.compile(r"^第 1 条 · "))).to_be_visible()
            second.click()
            expect(page.get_by_role("heading", name="第 2 条 · 记录完整性", exact=True)).to_be_visible()
            page.keyboard.press("k")
            expect(page.get_by_role("heading", name=re.compile(r"^第 1 条 · "))).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.get_by_role("navigation", name="文件队列", exact=True)).to_be_visible()
            browser.close()

    def test_cross_file_original_keeps_the_selected_clause_and_report(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 853})
            page.goto(PREVIEW + "/preview/audit.html?scene=reports-ready&task=preview-report-task")
            page.get_by_role("navigation", name="文件队列", exact=True).get_by_role("button").first.click()
            page.get_by_role("button", name="查看依据 1.1", exact=True).click()
            basis = page.get_by_role("dialog").filter(has=page.get_by_role("tab", name="核对原件", exact=True))
            basis.get_by_role("button", name="核对 控制计划.xlsx", exact=True).click()
            original = basis.get_by_role("region", name="原件预览", exact=True)
            expect(original.get_by_text("控制计划.xlsx", exact=True).first).to_be_visible()
            basis.get_by_role("tab", name="审核详情", exact=True).click()
            expect(basis.get_by_role("heading", name="第 1 条 · 频次一致性", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.get_by_role("region", name="审核详情", exact=True).get_by_role("heading", name="PFMEA.xlsx", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="查看报告", exact=True)).to_be_enabled()
            page.get_by_role("button", name="审核报告", exact=True).click()
            expect(page.get_by_role("button", name="下载当前报告", exact=True)).to_be_enabled()
            page.get_by_role("link", name="返回审核结果", exact=True).click()
            # The file queue stays beside the open file.
            expect(page.get_by_role("navigation", name="文件队列", exact=True).get_by_role("button")).to_have_count(3)
            browser.close()
