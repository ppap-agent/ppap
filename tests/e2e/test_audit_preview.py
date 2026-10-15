"""UI-only V3.1 preview checks. Run with PPAP_PREVIEW_URL against preview Vite.

These checks do not prove real upload, matching, audit, billing or persistence.
The production browser suite remains separate and unchanged.
"""

import os
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")


@unittest.skipUnless(PREVIEW and sync_playwright, "requires PPAP_PREVIEW_URL and the e2e dependency group (preview-only UI)")
class AuditPreviewTests(unittest.TestCase):
    def test_return_to_upload_can_continue_without_adding_the_files_again(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html")
            page.get_by_role("button", name="使用三类文件示例").click()
            page.get_by_role("checkbox", name="深度模式", exact=True).check()
            page.get_by_role("button", name="上一步", exact=True).click()
            expect(page.get_by_role("heading", name="上传 PPAP 文件", exact=True)).to_be_visible()
            page.get_by_role("button", name="继续核对", exact=True).click()
            expect(page.get_by_role("checkbox", name="深度模式", exact=True)).to_be_checked()
            expect(page.get_by_role("checkbox", name="选择 PFMEA.xlsx", exact=True)).to_be_checked()
            expect(page.get_by_role("checkbox", name="选择 控制计划.xlsx", exact=True)).to_have_count(1)
            browser.close()

    def test_each_file_keeps_its_own_rule_and_selection_survives_back(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            business_requests = []
            page.on("request", lambda request: business_requests.append(request.url) if request.resource_type in {"fetch", "xhr"} else None)
            page.goto(PREVIEW + "/preview/audit.html")
            expect(page.get_by_role("heading", name="上传 PPAP 文件", exact=True)).to_be_visible()
            page.get_by_role("button", name="使用三类文件示例").click()
            expect(page.get_by_role("heading", name="核对本次文件", exact=True)).to_be_visible()
            page.get_by_role("checkbox", name="选择 控制计划.xlsx", exact=True).uncheck()
            page.get_by_role("button", name="开始识别", exact=True).click()
            expect(page.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
            expect(page.get_by_text("示例 PFMEA 规则 · v1", exact=True)).to_be_visible()
            expect(page.get_by_text("示例过程流程图规则 · v3", exact=True)).to_be_visible()
            expect(page.get_by_text("示例控制计划规则 · v2", exact=True)).to_have_count(0)
            page.get_by_role("button", name="上一步", exact=True).click()
            expect(page.get_by_role("checkbox", name="选择 控制计划.xlsx", exact=True)).not_to_be_checked()
            page.get_by_role("checkbox", name="选择 控制计划.xlsx", exact=True).check()
            page.get_by_role("button", name="开始识别", exact=True).click()
            expect(page.get_by_text("示例控制计划规则 · v2", exact=True)).to_be_visible()
            rule = page.get_by_role("button", name="查看 示例 PFMEA 规则 · v1 条款", exact=True)
            rule.click()
            expect(page.get_by_role("dialog", name="示例 PFMEA 规则 · v1", exact=True)).to_be_visible()
            expect(page.get_by_text("示例条款，仅用于界面走查", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(rule).to_be_focused()
            self.assertEqual(business_requests, [])
            browser.close()

    def test_exceptions_block_matching_and_zero_match_ends_without_an_audit(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=invalid")
            page.get_by_role("button", name="使用三类文件示例").click()
            expect(page.get_by_role("button", name="开始识别", exact=True)).to_be_disabled()
            expect(page.get_by_text("文件加密或异常，无法识别", exact=True)).to_be_visible()
            page.get_by_role("button", name="一键移除异常文件", exact=True).click()
            expect(page.get_by_role("button", name="开始识别", exact=True)).to_be_enabled()
            page.goto(PREVIEW + "/preview/audit.html?scene=unmatched")
            page.get_by_role("button", name="使用三类文件示例").click()
            page.get_by_role("button", name="开始识别", exact=True).click()
            page.get_by_role("button", name="结束任务", exact=True).click()
            expect(page.get_by_text("未匹配到可用规则，未开始审核", exact=True)).to_be_visible()
            expect(page.get_by_text("未产生审核费用，也没有生成报告。", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="开始审核", exact=True)).to_have_count(0)
            browser.close()

    def test_local_bytes_are_not_claimed_as_validated_and_narrow_layout_is_usable(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(PREVIEW + "/preview/audit.html")
            page.get_by_label("选择审核文件", exact=True).set_input_files({"name": "not-a-real-document.pdf", "mimeType": "application/pdf", "buffer": b"broken"})
            expect(page.get_by_text("待真实预检查：本预览只登记本机文件，尚未执行真实解析。", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="开始识别", exact=True)).to_be_disabled()
            self.assertEqual(page.locator("h1").count(), 1)
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            page.get_by_role("button", name="打开审核任务", exact=True).click()
            expect(page.get_by_role("dialog", name="审核任务", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.get_by_role("button", name="打开审核任务", exact=True)).to_be_focused()
            browser.close()

    def test_technical_match_failure_is_not_zero_match(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=error")
            page.get_by_role("button", name="使用三类文件示例").click()
            page.get_by_role("button", name="开始识别", exact=True).click()
            expect(page.get_by_text("暂时无法确认匹配结果", exact=True)).to_have_count(3)
            expect(page.get_by_role("button", name="结束任务", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="开始审核", exact=True)).to_be_disabled()
            page.get_by_role("button", name="上一步", exact=True).click()
            expect(page.get_by_role("checkbox", name="选择 PFMEA.xlsx", exact=True)).to_be_checked()
            browser.close()

    def test_workbench_theme_and_width_do_not_lose_the_current_step(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/__preview?page=audit&width=820&theme=light")
            product = page.frame_locator("#product")
            product.get_by_role("button", name="使用三类文件示例").click()
            product.get_by_role("button", name="开始识别", exact=True).click()
            page.get_by_role("button", name="切换明暗主题", exact=True).click()
            expect(product.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
            expect(product.locator("html")).to_have_class("dark")
            for width in (390, 820, 1440):
                page.get_by_label("自定义宽度（像素）").fill(str(width))
                page.get_by_label("自定义宽度（像素）").press("Tab")
                page.wait_for_function("target => Math.abs(document.querySelector('#product').contentDocument.documentElement.clientWidth - target) <= 2", arg=width)
                expect(product.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
                product.locator("html").evaluate("el => { el.style.scrollBehavior = 'auto' }")
                self.assertTrue(product.locator("html").evaluate("el => el.scrollWidth <= el.clientWidth"))
            self.assertEqual(page.context.cookies(), [])
            response = page.request.get(PREVIEW + "/api/me")
            self.assertEqual(response.status, 503)
            self.assertEqual(response.json()["error"], "preview_has_no_business_api")
            browser.close()

    def test_switching_tasks_retains_edits_but_a_new_task_defaults_to_standard(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(PREVIEW + "/preview/audit.html")
            page.get_by_role("button", name="使用三类文件示例").click()
            page.get_by_role("checkbox", name="深度模式", exact=True).check()
            page.get_by_role("button", name="开始识别", exact=True).click()
            page.get_by_role("button", name="上一步", exact=True).click()
            page.get_by_role("checkbox", name="选择 控制计划.xlsx", exact=True).uncheck()
            page.get_by_role("button", name="新建审核任务", exact=True).click()
            page.get_by_role("button", name="使用三类文件示例").click()
            expect(page.get_by_role("checkbox", name="深度模式", exact=True)).not_to_be_checked()
            page.get_by_role("button", name="开始识别", exact=True).click()
            navigation = page.get_by_role("navigation", name="任务列表", exact=True)
            expect(navigation.get_by_role("button")).to_have_count(2)
            navigation.get_by_role("button").filter(has_text="PPAP-示例-001").click()
            expect(page.get_by_role("heading", name="核对本次文件", exact=True)).to_be_visible()
            expect(page.get_by_role("checkbox", name="选择 控制计划.xlsx", exact=True)).not_to_be_checked()
            expect(page.get_by_role("checkbox", name="深度模式", exact=True)).to_be_checked()
            browser.close()
