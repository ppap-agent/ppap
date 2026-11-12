"""Issue #174 preview contract; controlled adapters do not prove external delivery."""

import os
import unittest

from .conftest import pick_audit_files  # noqa: E402

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")


@unittest.skipUnless(PREVIEW and sync_playwright, "requires the preview and e2e dependencies")
class AuditIntakeClosureTests(unittest.TestCase):
    def test_card_confirmation_recovery_keeps_the_pending_receipt_and_only_reads_it(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=restore-card-pending&task=preview-recovery-task")
            reason = "已收到卡片确认，正在核对用量，尚未受理。"
            expect(page.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
            expect(page.get_by_role("alert")).to_have_attribute("data-request-id", "preview-recovery-request")
            expect(page.get_by_role("alert")).to_contain_text(reason)
            expect(page.get_by_role("button", name="开始审核", exact=True)).to_be_disabled()
            page.reload()
            expect(page.get_by_role("alert")).to_contain_text(reason)
            expect(page.get_by_role("button", name="开始审核", exact=True)).to_be_disabled()
            page.get_by_role("button", name="读取提交结果", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text(reason)
            expect(page.get_by_role("alert")).to_have_attribute("data-request-id", "preview-recovery-request")
            expect(page.get_by_role("button", name="开始审核", exact=True)).to_be_disabled()
            page.get_by_role("button", name="读取提交结果", exact=True).click()
            expect(page.get_by_role("heading", name="审核结果", exact=True)).to_be_visible()
            browser.close()

    def test_card_confirmation_rejection_restores_the_reason_and_allows_explicit_confirmation(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=restore-card-rejected&task=preview-recovery-task")
            expect(page.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
            expect(page.get_by_role("alert")).to_contain_text("本次未被接收")
            expect(page.get_by_role("alert")).to_contain_text("文件或客户选择已变化，请打开页面核对。")
            expect(page.get_by_role("button", name="读取提交结果", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="开始审核", exact=True)).to_be_enabled()
            page.get_by_role("button", name="开始审核", exact=True).click()
            expect(page.get_by_role("heading", name="审核结果", exact=True)).to_be_visible()
            browser.close()

    def test_pending_uploads_keep_the_return_and_original_receipt_recovery(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            for fail_reply in (False, True):
                page.goto(PREVIEW + "/preview/audit.html?scene=upload-loading")
                pick_audit_files(page, {"name": "等待回执.pdf", "mimeType": "application/pdf", "buffer": b"fixture"})
                if fail_reply:
                    page.get_by_role("button", name="令示例读取 1 失败", exact=True).click()
                    expect(page.get_by_role("button", name="读取传输 等待回执.pdf", exact=True)).to_be_enabled()
                page.get_by_role("button", name="上一步", exact=True).click()
                # The upload step lists the draft; the pending transfer stays in it, still unconfirmed.
                draft = page.get_by_role("region", name="已选文件", exact=True)
                expect(draft.get_by_role("heading", name="已选 1 个文件", exact=True)).to_be_visible()
                expect(draft.get_by_role("listitem")).to_have_count(1)
                expect(draft.get_by_role("listitem").filter(has_text="等待回执.pdf")).to_contain_text("上传待确认" if fail_reply else "上传中")
                page.get_by_role("button", name="查看已选文件", exact=True).click()
                if not fail_reply:
                    page.get_by_role("button", name="返回示例读取 1", exact=True).click()
                else:
                    page.get_by_role("button", name="读取传输 等待回执.pdf", exact=True).click()
                expect(page.get_by_role("heading", name="等待文件检查 · 1 份", exact=True)).to_be_visible()
                expect(page.get_by_text("等待回执.pdf", exact=True)).to_be_visible()
            browser.close()

    def test_steps_do_not_confuse_a_terminal_task_with_ready_reports(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            # Once the audit runs, its status badge says where it stands; the intake steps would only repeat it.
            for scene, task in (
                ("restore-progress", "preview-recovery-task"),
                ("reports-failed", "preview-report-task"),
                ("reports-generating", "preview-report-task"),
                ("reports-all-ready", "preview-report-task"),
            ):
                page.goto(PREVIEW + f"/preview/audit.html?scene={scene}&task={task}")
                expect(page.get_by_role("heading", name="审核结果", exact=True)).to_be_visible()
                expect(page.get_by_role("list", name="审核步骤", exact=True)).to_have_count(0)
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
            page.get_by_role("button", name="查看 控制计划.xlsx 的条款", exact=True).click()
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

    def test_return_to_upload_keeps_selection_in_the_draft_list(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=populated")
            expect(page.get_by_label("客户（可选）", exact=True)).to_be_visible()
            page.get_by_role("button", name="使用三类文件示例", exact=True).click()
            expect(page.get_by_label("客户（可选）", exact=True)).to_have_count(0)
            page.get_by_role("checkbox", name="选择 PFMEA.xlsx", exact=True).uncheck()
            page.get_by_role("button", name="上一步", exact=True).click()
            # The upload step lists all draft files; the unchecked one stays in the draft but is marked unselected.
            draft = page.get_by_role("region", name="已选文件", exact=True)
            expect(draft.get_by_role("heading", name="已选 2 个文件", exact=True)).to_be_visible()
            expect(draft.get_by_role("listitem")).to_have_count(3)
            expect(draft.get_by_role("listitem").filter(has_text="PFMEA.xlsx")).to_contain_text("未选择")
            expect(draft.get_by_role("listitem").filter(has_text="控制计划.xlsx")).not_to_contain_text("未选择")
            expect(page.get_by_role("button", name="上传规则", exact=True)).to_have_count(0)
            page.get_by_role("button", name="查看已选文件", exact=True).click()
            expect(page.get_by_role("checkbox", name="选择 PFMEA.xlsx", exact=True)).not_to_be_checked()
            expect(page.get_by_role("checkbox", name="选择 控制计划.xlsx", exact=True)).to_be_checked()
            page.get_by_role("button", name="上一步", exact=True).click()
            page.get_by_role("button", name="从飞书云文档选择", exact=True).click()
            picker = page.get_by_role("dialog", name="从飞书选择文件", exact=True)
            picker.get_by_role("checkbox", name="选择飞书文件 控制计划（飞书表格）", exact=True).check()
            picker.get_by_role("button", name="添加 1 份文件", exact=True).click()
            expect(page.get_by_role("heading", name="核对本次文件", exact=True)).to_be_visible()
            expect(page.get_by_role("checkbox", name="选择 PFMEA.xlsx", exact=True)).not_to_be_checked()
            expect(page.get_by_role("heading", name="等待文件检查 · 1 份", exact=True)).to_be_visible()
            browser.close()

    def test_all_unselected_targets_can_end_without_another_recognition(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=with-reference")
            page.get_by_role("button", name="使用三类文件示例", exact=True).click()
            page.get_by_role("button", name="开始识别", exact=True).click()
            expect(page.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
            page.get_by_role("button", name="上一步", exact=True).click()
            page.get_by_role("checkbox", name="全选可审核文件", exact=True).uncheck()
            expect(page.get_by_role("button", name="开始识别", exact=True)).to_have_count(0)
            page.get_by_role("button", name="结束任务", exact=True).click()
            expect(page.get_by_role("heading", name="任务已结束", exact=True)).to_be_visible()
            expect(page.get_by_text("未选择待审文件，未开始审核", exact=True)).to_be_visible()
            expect(page.get_by_text("未产生审核费用，也没有生成报告。", exact=True)).to_be_visible()
            browser.close()
