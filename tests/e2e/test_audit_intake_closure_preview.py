"""Issue #174 preview contract; controlled adapters do not prove external delivery."""

import os
import re
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
            expect(page.get_by_role("heading", name="确认规则", exact=True)).to_be_visible()
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
            expect(page.get_by_role("heading", name="确认规则", exact=True)).to_be_visible()
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
                # The pending transfer is a line among the files and recognition waits for it. A transfer still
                # running only needs time; an unconfirmed one needs the person.
                added = page.get_by_role("region", name="已添加", exact=True)
                line = added.get_by_role("listitem").filter(has_text="等待回执.pdf")
                expect(line).to_contain_text("结果未确认" if fail_reply else "传输中")
                expect(page.get_by_role("status").filter(has_text="请先处理或移除未完成的传输，再开始识别。" if fail_reply else "正在上传…（还有 1 份）")).to_be_visible()
                if not fail_reply:
                    page.get_by_role("button", name="返回示例读取 1", exact=True).click()
                else:
                    page.get_by_role("button", name="读取传输 等待回执.pdf", exact=True).click()
                # The original receipt turns the same line into the file, waiting for its check.
                expect(line).to_have_count(1)
                expect(line).to_contain_text("检查中")
                expect(line).not_to_contain_text("传输中")
                expect(page.get_by_role("button", name="读取传输 等待回执.pdf", exact=True)).to_have_count(0)
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
            # Recognition is observed on the confirm-rules page; one file is still being recognized.
            expect(page.get_by_role("heading", name="确认规则", exact=True)).to_be_visible()
            expect(page.get_by_role("status").filter(has_text=re.compile(r"^识别中 \d/3$"))).to_be_visible()
            expect(page.get_by_role("region", name="已添加", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="读取识别结果", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="重新读取识别结果", exact=True)).to_have_count(0)
            page.get_by_role("button", name="返回示例读取 1", exact=True).click()
            expect(page.get_by_role("status").filter(has_text=re.compile(r"^待确认$"))).to_be_visible()
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
            expect(page.get_by_role("combobox", name="审核客户", exact=True)).to_be_visible()
            # The upload page carries one step line, worded exactly.
            expect(page.get_by_role("list", name="审核步骤", exact=True)).to_have_count(1)
            expect(page.get_by_role("list", name="审核步骤", exact=True)).to_have_text(re.compile(r"^① 上传文件\s*›\s*② 预检并匹配规则\s*›\s*③ AI 审核$"))
            page.get_by_role("button", name="使用三类文件示例", exact=True).click()
            # Files stay on the upload page beside the customer choice.
            expect(page.get_by_role("combobox", name="审核客户", exact=True)).to_be_visible()
            page.get_by_role("checkbox", name="选择 PFMEA.xlsx", exact=True).uncheck()
            # The unchecked file stays in the draft; its box says it is not selected, and no recognition state is shown.
            added = page.get_by_role("region", name="已添加", exact=True)
            expect(added.get_by_role("listitem")).to_have_count(3)
            expect(page.get_by_role("checkbox", name="选择 PFMEA.xlsx", exact=True)).not_to_be_checked()
            expect(added).not_to_contain_text("待识别")
            expect(page.get_by_role("button", name="上传规则", exact=True)).to_have_count(0)
            page.get_by_role("button", name="从飞书云文档选择", exact=True).click()
            picker = page.get_by_role("dialog", name="从飞书选择文件", exact=True)
            picker.get_by_role("checkbox", name="选择飞书文件 控制计划（飞书表格）", exact=True).check()
            picker.get_by_role("button", name="添加 1 份文件", exact=True).click()
            # Adding a file keeps the earlier selection; the new file waits for its check.
            expect(added.get_by_role("listitem").filter(has_text="控制计划（飞书表格）.xlsx")).to_contain_text("检查中")
            expect(page.get_by_role("checkbox", name="选择 PFMEA.xlsx", exact=True)).not_to_be_checked()
            expect(page.get_by_role("checkbox", name="选择 控制计划.xlsx", exact=True)).to_be_checked()
            browser.close()

    def test_all_unselected_targets_can_end_without_another_recognition(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=with-reference")
            page.get_by_role("button", name="使用三类文件示例", exact=True).click()
            page.get_by_role("button", name="开始识别", exact=True).click()
            expect(page.get_by_role("heading", name="确认规则", exact=True)).to_be_visible()
            for name in ("PFMEA.xlsx", "控制计划.xlsx"):
                page.get_by_role("checkbox", name=f"送审 {name}", exact=True).uncheck()
            expect(page.get_by_role("button", name="重新识别", exact=True)).to_have_count(0)
            # Unsaved choices leave the count to the server; 开始审核 completes the task without a Round.
            expect(page.get_by_text("送审 待确认", exact=True)).to_be_visible()
            page.get_by_role("button", name="开始审核", exact=True).click()
            expect(page.get_by_role("heading", name="本次任务无送审文件", exact=True)).to_be_visible()
            expect(page.get_by_text("本次未勾选送审文件。未开始审核，未扣费，也没有生成审核报告。", exact=True)).to_be_visible()
            expect(page.get_by_role("heading", name="确认规则", exact=True)).to_have_count(0)
            page.get_by_role("button", name="返回上传新文件", exact=True).click()
            expect(page.get_by_role("button", name="选择本地文件", exact=True)).to_be_visible()
            browser.close()
