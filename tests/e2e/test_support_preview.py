"""General support interactions; not real Feishu integration."""

import os
import re
import unittest

from .conftest import pick_audit_files  # noqa: E402

PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")

if PREVIEW:
    from playwright.sync_api import expect, sync_playwright


@unittest.skipUnless(PREVIEW, "requires PPAP_PREVIEW_URL (preview-only UI)")
class SupportPreviewTests(unittest.TestCase):
    def test_late_support_read_after_back_cannot_fill_a_reopened_dialog(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(PREVIEW + "/__preview?page=audit&scene=support-loading&width=1280")
            preview = page.frame_locator("iframe")
            pick_audit_files(preview, {"name": "本次未提交.xlsx", "mimeType": "application/octet-stream", "buffer": b"sample"})
            preview.get_by_role("button", name="用量记录", exact=True).click()
            preview.get_by_role("button", name="联系我们", exact=True).click()
            dialog = preview.get_by_role("dialog", name="联系我们", exact=True)
            expect(dialog.get_by_role("status")).to_contain_text("正在读取服务信息")
            page.go_back()
            expect(dialog).to_have_count(0)
            preview.get_by_role("button", name="联系我们", exact=True).click()
            expect(dialog.get_by_role("status")).to_contain_text("正在读取服务信息")
            page.get_by_role("button", name="返回一次示例读取", exact=True).click()
            expect(dialog.get_by_role("status")).to_contain_text("正在读取服务信息")
            expect(dialog.get_by_role("button", name="复制电话", exact=True)).to_have_count(0)
            page.get_by_role("button", name="返回一次示例读取", exact=True).click()
            expect(dialog.get_by_role("button", name="复制电话", exact=True)).to_be_enabled()
            dialog.get_by_role("button", name="关闭", exact=True).click()
            expect(preview.get_by_text("本次未提交.xlsx", exact=True).first).to_be_visible()
            browser.close()

    def test_support_image_failure_keeps_the_confirmed_contact_exit(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(PREVIEW + "/preview/audit.html?scene=support-qr-error")
            page.get_by_role("button", name="联系我们", exact=True).click()
            dialog = page.get_by_role("dialog", name="联系我们", exact=True)
            expect(dialog.get_by_text("二维码暂不可用，请稍后重试。", exact=True)).to_be_visible()
            expect(dialog.get_by_role("button", name="放大服务二维码", exact=True)).to_have_count(0)
            expect(dialog.get_by_role("button", name="复制电话", exact=True)).to_be_enabled()
            expect(dialog.get_by_role("link", name="support@example.invalid", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="关闭", exact=True).click()
            expect(page.get_by_role("heading", name="PPAP Audit", exact=True)).to_be_visible()
            browser.close()

    def test_support_read_failure_can_reread_the_configured_contacts(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(PREVIEW + "/preview/audit.html?scene=support-error")
            page.get_by_role("button", name="联系我们", exact=True).click()
            dialog = page.get_by_role("dialog", name="联系我们", exact=True)
            expect(dialog.get_by_text("服务信息暂不可用", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="重新读取服务信息", exact=True).click()
            expect(dialog.get_by_text("示例电话（不可拨打）", exact=True)).to_be_visible()
            expect(dialog.get_by_role("link", name="support@example.invalid", exact=True)).to_be_visible()
            browser.close()

    def test_configured_support_can_copy_contacts_and_enlarge_the_sample_image(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1280, "height": 900}, permissions=["clipboard-read", "clipboard-write"])
            page = context.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=support-ready")
            page.get_by_role("button", name="联系我们", exact=True).click()
            dialog = page.get_by_role("dialog", name="联系我们", exact=True)
            expect(dialog.get_by_text("示例电话（不可拨打）", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="复制电话", exact=True).click()
            self.assertEqual(page.evaluate("navigator.clipboard.readText()"), "示例电话（不可拨打）")
            dialog.get_by_role("button", name="复制邮箱", exact=True).click()
            self.assertEqual(page.evaluate("navigator.clipboard.readText()"), "support@example.invalid")
            expect(dialog.get_by_role("link", name="support@example.invalid", exact=True)).to_have_attribute("href", "mailto:support%40example.invalid")
            thumbnail = dialog.get_by_role("img", name="已配置的飞书服务二维码", exact=True)
            expect(thumbnail).to_be_visible()
            self.assertGreater(thumbnail.evaluate("image => image.naturalWidth"), 0)
            dialog.get_by_role("button", name="放大服务二维码", exact=True).click()
            enlarged = page.get_by_role("dialog", name="服务二维码", exact=True)
            expect(enlarged.get_by_role("img", name="放大的飞书服务二维码", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(enlarged).to_have_count(0)
            expect(dialog.get_by_role("button", name="放大服务二维码", exact=True)).to_be_focused()
            dialog.get_by_role("button", name="关闭", exact=True).click()
            expect(page.get_by_role("heading", name="PPAP Audit", exact=True)).to_be_visible()
            browser.close()

    def test_browser_back_closes_support_and_preserves_pre_task_files(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(PREVIEW + "/preview/audit.html")
            page.get_by_role("combobox", name="客户（可选）").click()
            page.get_by_role("option", name=re.compile(r"^示例客户 已支持 ")).click()
            pick_audit_files(page, {"name": "保留本次文件.xlsx", "mimeType": "application/octet-stream", "buffer": b"sample"})
            page.get_by_role("button", name="用量记录", exact=True).click()
            page.get_by_role("button", name="联系我们", exact=True).click()
            expect(page.get_by_role("dialog", name="联系我们", exact=True)).to_be_visible()
            page.go_back()
            expect(page.get_by_role("dialog", name="联系我们", exact=True)).to_have_count(0)
            expect(page.get_by_role("heading", name="核对本次文件", exact=True)).to_be_visible()
            expect(page.get_by_text("保留本次文件.xlsx", exact=True).first).to_be_visible()
            expect(page.get_by_role("combobox", name="客户（可选）")).to_have_count(0)
            page.get_by_role("button", name="上一步", exact=True).click()
            expect(page.get_by_text("已选 1 个文件", exact=True)).to_be_visible()
            expect(page.get_by_role("combobox", name="客户（可选）")).to_contain_text("示例客户")
            page.get_by_role("button", name="查看已选文件", exact=True).click()
            expect(page.get_by_text("保留本次文件.xlsx", exact=True).first).to_be_visible()
            browser.close()

    def test_empty_configured_support_has_an_explicit_unavailable_exit(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(PREVIEW + "/preview/audit.html?scene=support-empty")
            page.get_by_role("button", name="联系我们", exact=True).click()
            dialog = page.get_by_role("dialog", name="联系我们", exact=True)
            expect(dialog.get_by_text("服务信息暂不可用", exact=True)).to_be_visible()
            expect(dialog.get_by_role("button", name="重新读取服务信息", exact=True)).to_be_enabled()
            browser.close()

    def test_global_support_preserves_current_upload_and_returns_focus(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(PREVIEW + "/preview/audit.html")
            pick_audit_files(page, {"name": "保留草稿.xlsx", "mimeType": "application/octet-stream", "buffer": b"sample"})
            page.get_by_role("button", name="联系我们", exact=True).click()
            dialog = page.get_by_role("dialog", name="联系我们", exact=True)
            expect(dialog.get_by_text("服务信息暂不可用", exact=True)).to_be_visible()
            expect(dialog.get_by_role("textbox", name="页面参考编号", exact=True)).to_have_value(re.compile(r"^client-[a-zA-Z0-9-]+$"))
            dialog.get_by_role("button", name="关闭", exact=True).click()
            expect(page.get_by_text("保留草稿.xlsx", exact=True).first).to_be_visible()
            expect(page.get_by_role("button", name="联系我们", exact=True)).to_be_focused()
            browser.close()
