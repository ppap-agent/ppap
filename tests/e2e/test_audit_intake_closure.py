"""Issue #174 UI contract; controlled adapters do not prove external delivery."""

import os
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")


@unittest.skipUnless(PREVIEW and sync_playwright, "requires the preview and e2e dependencies")
class AuditIntakeClosureTests(unittest.TestCase):
    def test_steps_do_not_confuse_a_terminal_task_with_ready_reports(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            for scene, task, states in (
                ("restore-progress", "preview-recovery-task", ["complete", "complete", "current"]),
                ("reports-failed", "preview-report-task", ["complete", "complete", "current"]),
                ("reports-generating", "preview-report-task", ["complete", "complete", "current"]),
                ("reports-all-ready", "preview-report-task", ["complete", "complete", "complete"]),
            ):
                page.goto(PREVIEW + f"/preview/audit.html?scene={scene}&task={task}")
                expect(page.get_by_role("heading", name="审核结果", exact=True)).to_be_visible()
                nodes = page.get_by_role("list", name="审核步骤", exact=True).get_by_role("listitem")
                for index, state in enumerate(states):
                    expect(nodes.nth(index)).to_have_attribute("data-state", state)
            browser.close()

    def test_matching_observes_progress_without_a_manual_read_action(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=recognition-progress-removal")
            page.get_by_role("button", name="使用三类文件示例", exact=True).click()
            page.get_by_role("button", name="开始识别", exact=True).click()
            expect(page.get_by_text("识别中 2/3", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="读取识别结果", exact=True)).to_have_count(0)
            expect(page.get_by_text("识别每份文件适配规则，未匹配文件无法进入审核", exact=True)).to_be_visible()
            page.get_by_role("button", name="返回示例读取 1", exact=True).click()
            expect(page.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="开始审核", exact=True)).to_be_enabled()
            browser.close()

    def test_each_matched_file_opens_only_its_own_clause(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=with-reference")
            page.get_by_role("button", name="使用三类文件示例", exact=True).click()
            page.get_by_role("button", name="开始识别", exact=True).click()
            clauses = page.get_by_role("list", name="控制计划.xlsx · 匹配条款", exact=True)
            trigger = clauses.get_by_role("button").first
            title = trigger.inner_text()
            trigger.click()
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_role("heading", name=title, exact=True)).to_be_visible()
            expect(dialog.get_by_text("示例控制计划规则 · v2", exact=True)).to_be_visible()
            expect(dialog.get_by_text("控制计划.xlsx", exact=True)).to_be_visible()
            expect(dialog.get_by_text("示例 PFMEA 规则 · v1", exact=True)).to_have_count(0)
            page.keyboard.press("Escape")
            expect(trigger).to_be_focused()
            expect(page.get_by_role("list", name="特殊特性清单.xlsx · 匹配条款", exact=True)).to_have_count(0)
            expect(page.get_by_role("heading", name="匹配条款", exact=True)).to_have_count(0)
            browser.close()

    def test_return_to_upload_keeps_selection_but_shows_only_a_count(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=populated")
            expect(page.get_by_label("客户（可选）", exact=True)).to_be_visible()
            page.get_by_role("button", name="使用三类文件示例", exact=True).click()
            expect(page.get_by_label("客户（可选）", exact=True)).to_have_count(0)
            page.get_by_role("checkbox", name="选择 PFMEA.xlsx", exact=True).uncheck()
            page.get_by_role("button", name="上一步", exact=True).click()
            expect(page.get_by_text("已选 2 个文件", exact=True)).to_be_visible()
            expect(page.get_by_text("PFMEA.xlsx", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="上传规则", exact=True)).to_have_count(0)
            page.get_by_role("button", name="查看已选文件", exact=True).click()
            expect(page.get_by_role("checkbox", name="选择 PFMEA.xlsx", exact=True)).not_to_be_checked()
            expect(page.get_by_role("checkbox", name="选择 控制计划.xlsx", exact=True)).to_be_checked()
            page.get_by_role("button", name="上一步", exact=True).click()
            page.get_by_role("button", name="从飞书选择", exact=True).click()
            picker = page.get_by_role("dialog", name="从飞书选择文件", exact=True)
            picker.get_by_role("checkbox", name="选择飞书文件 控制计划（飞书表格）", exact=True).check()
            picker.get_by_role("button", name="添加 1 份文件", exact=True).click()
            expect(page.get_by_role("heading", name="核对本次文件", exact=True)).to_be_visible()
            expect(page.get_by_role("checkbox", name="选择 PFMEA.xlsx", exact=True)).not_to_be_checked()
            expect(page.get_by_role("heading", name="等待预检查 · 1 份", exact=True)).to_be_visible()
            browser.close()
