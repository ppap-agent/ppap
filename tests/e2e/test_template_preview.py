"""#170 UI-only catalog and support interactions, not real Feishu integration."""

import os
import re
import unittest
import zipfile

PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")

if PREVIEW:
    from playwright.sync_api import expect, sync_playwright


@unittest.skipUnless(PREVIEW, "requires PPAP_PREVIEW_URL (preview-only UI)")
class TemplatePreviewTests(unittest.TestCase):
    def test_selecting_the_current_customer_keeps_the_visible_template_page(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(PREVIEW + "/preview/audit.html?view=templates")
            page.get_by_role("button", name="选择客户 示例客户 A", exact=True).click()
            expect(page.get_by_role("row", name=re.compile("示例控制计划模板.xlsx"))).to_be_visible()
            page.get_by_role("button", name="下一页模板", exact=True).click()
            page.get_by_role("button", name="选择客户 示例客户 A", exact=True).click()
            expect(page.get_by_text("共 12 份模板 · 第 2/2 页", exact=True)).to_be_visible()
            expect(page.get_by_role("row", name=re.compile("示例归档模板 11.xlsx"))).to_be_visible()
            expect(page.get_by_role("button", name="下载客户模板 ZIP", exact=True)).to_be_enabled()
            browser.close()

    def test_late_support_read_after_back_cannot_fill_a_reopened_dialog(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(PREVIEW + "/__preview?page=audit&scene=support-loading&width=390")
            preview = page.frame_locator("iframe")
            preview.get_by_label("选择审核文件", exact=True).set_input_files({"name": "本次未提交.xlsx", "mimeType": "application/octet-stream", "buffer": b"sample"})
            preview.get_by_role("button", name="查看客户模板", exact=True).click()
            preview.get_by_role("button", name="打开产品导航", exact=True).click()
            preview.get_by_role("button", name="联系我们", exact=True).click()
            dialog = preview.get_by_role("dialog", name="联系我们", exact=True)
            expect(dialog.get_by_role("status")).to_contain_text("正在读取服务信息")
            page.go_back()
            expect(dialog).to_have_count(0)
            preview.get_by_role("button", name="打开审核任务", exact=True).click()
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
            expect(page.get_by_role("heading", name="上传 PPAP 文件", exact=True)).to_be_visible()
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
            context = browser.new_context(viewport={"width": 390, "height": 844}, permissions=["clipboard-read", "clipboard-write"])
            page = context.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=support-ready")
            page.get_by_role("button", name="打开审核任务", exact=True).click()
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
            self.assertTrue(enlarged.evaluate("element => element.scrollWidth <= element.clientWidth"))
            page.keyboard.press("Escape")
            expect(enlarged).to_have_count(0)
            expect(dialog.get_by_role("button", name="放大服务二维码", exact=True)).to_be_focused()
            dialog.get_by_role("button", name="关闭", exact=True).click()
            expect(page.get_by_role("heading", name="上传 PPAP 文件", exact=True)).to_be_visible()
            browser.close()

    def test_browser_back_closes_support_and_preserves_pre_task_files(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(PREVIEW + "/preview/audit.html")
            page.get_by_label("选择审核文件", exact=True).set_input_files({"name": "保留本次文件.xlsx", "mimeType": "application/octet-stream", "buffer": b"sample"})
            page.get_by_role("combobox", name="客户（可选）").click()
            page.get_by_role("option", name="示例客户", exact=True).click()
            page.get_by_role("button", name="查看客户模板", exact=True).click()
            page.get_by_role("button", name="联系我们", exact=True).click()
            expect(page.get_by_role("dialog", name="联系我们", exact=True)).to_be_visible()
            page.go_back()
            expect(page.get_by_role("dialog", name="联系我们", exact=True)).to_have_count(0)
            expect(page.get_by_role("heading", name="核对本次文件", exact=True)).to_be_visible()
            expect(page.get_by_text("保留本次文件.xlsx", exact=True).first).to_be_visible()
            expect(page.get_by_role("combobox", name="客户（可选）")).to_contain_text("示例客户")
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

    def test_browser_back_closes_template_portal_without_losing_task_or_catalog_page(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(PREVIEW + "/preview/audit.html?scene=unmatched")
            page.get_by_role("button", name="使用三类文件示例", exact=True).click()
            page.get_by_role("button", name="开始识别", exact=True).click()
            page.get_by_role("button", name="标准模板", exact=True).first.click()
            page.get_by_role("button", name="下一页模板", exact=True).click()
            page.get_by_role("row", name=re.compile("示例归档模板 11.xlsx")).get_by_role("button", name="预览", exact=True).click()
            expect(page.get_by_role("dialog", name="模板预览", exact=True)).to_be_visible()
            page.go_back()
            expect(page.get_by_role("dialog", name="模板预览", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="结束任务", exact=True)).to_be_enabled()
            page.get_by_role("button", name="标准模板", exact=True).first.click()
            expect(page.get_by_text("共 12 份模板 · 第 2/2 页", exact=True)).to_be_visible()
            expect(page.get_by_role("dialog", name="模板预览", exact=True)).to_have_count(0)
            browser.close()

    def test_preview_harness_keeps_the_template_surface_after_refresh_and_theme_change(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/__preview?page=audit&scene=templates-ready&view=templates")
            preview = page.frame_locator("iframe")
            expect(preview.get_by_role("heading", name="客户模板库", exact=True)).to_be_visible()
            page.reload()
            expect(preview.get_by_role("heading", name="客户模板库", exact=True)).to_be_visible()
            page.get_by_role("button", name="切换明暗主题", exact=True).click()
            expect(preview.get_by_role("heading", name="客户模板库", exact=True)).to_be_visible()
            preview.get_by_role("button", name="返回审核", exact=True).click()
            expect(preview.get_by_role("heading", name="上传 PPAP 文件", exact=True)).to_be_visible()
            page.reload()
            expect(preview.get_by_role("heading", name="上传 PPAP 文件", exact=True)).to_be_visible()
            browser.close()

    def test_return_from_an_initial_template_deep_link_reads_the_original_task(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=restore-completed&view=templates&task=preview-recovery-task")
            page.get_by_role("button", name="返回审核", exact=True).click()
            expect(page.get_by_text("PPAP Audit / PPAP-恢复示例", exact=True)).to_be_visible()
            expect(page.get_by_role("region", name="审核详情", exact=True)).to_be_visible()
            expect(page.get_by_role("heading", name="正在恢复任务", exact=True)).to_have_count(0)
            browser.close()

    def test_unmatched_task_returns_from_templates_without_matching_or_accepting_again(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(PREVIEW + "/preview/audit.html?scene=unmatched")
            page.get_by_role("button", name="使用三类文件示例", exact=True).click()
            page.get_by_role("button", name="开始识别", exact=True).click()
            address = page.url
            page.get_by_role("button", name="标准模板", exact=True).first.click()
            page.get_by_role("button", name="选择客户 示例客户 B", exact=True).click()
            page.get_by_role("button", name="返回审核", exact=True).click()
            self.assertEqual(page.url, address)
            expect(page.get_by_text("未匹配到生效规则", exact=True)).to_have_count(3)
            expect(page.get_by_role("button", name="结束任务", exact=True)).to_be_enabled()
            expect(page.get_by_role("button", name="开始审核", exact=True)).to_have_count(0)
            browser.close()

    def test_mobile_catalog_shows_files_without_horizontal_page_overflow(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for width in (390, 1024, 1280):
                with self.subTest(width=width):
                    page = browser.new_page(viewport={"width": width, "height": 844})
                    page.goto(PREVIEW + "/preview/audit.html?view=templates")
                    row = page.get_by_role("row", name=re.compile("示例控制计划模板.xlsx"))
                    expect(row).to_be_visible()
                    expect(row.get_by_text("更新于 2026-10-16 08:00" if width < 1280 else "2026-10-16 08:00", exact=True)).to_be_visible()
                    expect(row.get_by_role("button", name="预览", exact=True)).to_be_visible()
                    expect(row.get_by_role("button", name="下载", exact=True)).to_be_visible()
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                    box = row.bounding_box()
                    self.assertLess(box["y"] + box["height"], 844)
                    page.close()
            browser.close()

    def test_preview_error_keeps_the_requested_template_version_and_a_download_exit(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?view=templates&scene=templates-preview-error")
            row = page.get_by_role("row", name=re.compile("示例控制计划模板.xlsx"))
            row.get_by_role("button", name="预览", exact=True).click()
            dialog = page.get_by_role("dialog", name="模板预览", exact=True)
            expect(dialog.get_by_role("alert")).to_contain_text("模板暂时无法读取")
            expect(dialog).to_have_attribute("data-file-version", "preview-template-a-1-v1")
            dialog.get_by_role("button", name="关闭", exact=True).click()
            with page.expect_download():
                row.get_by_role("button", name="下载", exact=True).click()
            browser.close()

    def test_catalog_back_keeps_pre_task_upload_and_customer_selection(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(PREVIEW + "/preview/audit.html")
            page.get_by_label("选择审核文件", exact=True).set_input_files(
                {"name": "未建任务.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"sample"}
            )
            page.get_by_role("combobox", name="客户（可选）").click()
            page.get_by_role("option", name="示例客户", exact=True).click()
            page.get_by_role("button", name="查看客户模板", exact=True).click()
            expect(page.get_by_role("heading", name="客户模板库", exact=True)).to_be_visible()
            page.go_back()
            expect(page.get_by_text("未建任务.xlsx", exact=True).first).to_be_visible()
            expect(page.get_by_role("combobox", name="客户（可选）")).to_contain_text("示例客户")
            page.get_by_role("button", name="查看客户模板", exact=True).click()
            page.get_by_role("button", name="返回审核", exact=True).click()
            expect(page.get_by_text("未建任务.xlsx", exact=True).first).to_be_visible()
            browser.close()

    def test_catalog_coverage_is_rule_type_union_and_template_pagination_is_independent(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(PREVIEW + "/preview/audit.html?view=templates")
            expect(page.get_by_text("规则覆盖 6/17 类文件", exact=True)).to_be_visible()
            expect(page.get_by_text("共 12 份模板 · 第 1/2 页", exact=True)).to_be_visible()
            expect(page.get_by_role("table", name="客户模板").get_by_role("row")).to_have_count(11)
            page.get_by_role("button", name="下一页模板", exact=True).click()
            expect(page.get_by_text("共 12 份模板 · 第 2/2 页", exact=True)).to_be_visible()
            row = page.get_by_role("row", name=re.compile("示例归档模板 11.xlsx"))
            row.get_by_role("button", name="预览", exact=True).click()
            dialog = page.get_by_role("dialog", name="模板预览", exact=True)
            expect(dialog).to_have_attribute("data-file-version", "preview-template-a-11-v1")
            expect(dialog.get_by_role("cell", name="A1：预览模板", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="关闭", exact=True).click()
            expect(page.get_by_text("共 12 份模板 · 第 2/2 页", exact=True)).to_be_visible()
            browser.close()

    def test_template_search_and_denied_access_never_substitute_a_file(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?view=templates&scene=templates-denied")
            expect(page.get_by_role("button", name="管理文件夹权限", exact=True)).to_be_disabled()
            expect(page.get_by_text("仅文件夹管理者可修改权限。", exact=True)).to_be_visible()
            row = page.get_by_role("row", name=re.compile("示例控制计划模板.xlsx"))
            expect(row.get_by_role("button", name="预览", exact=True)).to_be_disabled()
            expect(row.get_by_role("button", name="下载", exact=True)).to_be_disabled()
            page.get_by_role("textbox", name="搜索模板", exact=True).fill("不存在")
            expect(page.get_by_text("没有符合条件的模板。", exact=True)).to_be_visible()
            page.get_by_role("button", name="清除模板搜索", exact=True).click()
            expect(row).to_be_visible()
            browser.close()

    def test_late_customer_response_cannot_replace_current_customer(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(PREVIEW + "/preview/audit.html?view=templates&scene=templates-race")
            expect(page.get_by_text("模板清单 · 示例客户 A", exact=True)).to_be_visible()
            page.get_by_role("button", name="选择客户 示例客户 B", exact=True).click()
            expect(page.get_by_role("heading", name="示例客户 B", exact=True)).to_be_visible()
            expect(page.get_by_role("row", name=re.compile("示例客户B模板.xlsx"))).to_be_visible()
            page.get_by_role("button", name="返回示例读取 1", exact=True).click()
            expect(page.get_by_role("heading", name="示例客户 B", exact=True)).to_be_visible()
            expect(page.get_by_role("row", name=re.compile("示例控制计划模板.xlsx"))).to_have_count(0)
            browser.close()

    def test_downloads_have_actual_template_bytes_and_complete_customer_zip(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?view=templates")
            row = page.get_by_role("row", name=re.compile("示例控制计划模板.xlsx"))
            with page.expect_download() as single:
                row.get_by_role("button", name="下载", exact=True).click()
            with zipfile.ZipFile(single.value.path()) as workbook:
                self.assertIn("xl/workbook.xml", workbook.namelist())
            with page.expect_download() as package:
                page.get_by_role("button", name="下载客户模板 ZIP", exact=True).click()
            with zipfile.ZipFile(package.value.path()) as archive:
                self.assertEqual(len(archive.namelist()), 13)
                self.assertIn("示例控制计划模板.xlsx", archive.namelist())
                self.assertIn("示例归档模板 12.xlsx", archive.namelist())
                self.assertIn("preview-template-a-12-v1", archive.read("模板清单.txt").decode())
            expect(page.get_by_role("button", name="开始审核", exact=True)).to_have_count(0)
            browser.close()

    def test_incomplete_zip_is_not_downloaded_as_complete(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            downloads = []
            page.on("download", lambda download: downloads.append(download))
            page.goto(PREVIEW + "/preview/audit.html?view=templates&scene=templates-zip-incomplete")
            page.get_by_role("button", name="下载客户模板 ZIP", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("模板包暂时无法下载")
            self.assertEqual(downloads, [])
            browser.close()

    def test_failed_template_zip_rereads_the_same_list_before_an_explicit_new_download(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            downloads = []
            page.on("download", lambda download: downloads.append(download))
            page.goto(PREVIEW + "/preview/audit.html?scene=templates-zip-recovery")
            page.get_by_label("选择审核文件", exact=True).set_input_files({"name": "保留的审核草稿.xlsx", "mimeType": "application/octet-stream", "buffer": b"sample"})
            page.get_by_role("combobox", name="客户（可选）").click()
            page.get_by_role("option", name="示例客户", exact=True).click()
            page.get_by_role("button", name="查看客户模板", exact=True).click()
            page.get_by_role("textbox", name="搜索模板", exact=True).fill("示例")
            expect(page.get_by_role("button", name="下一页模板", exact=True)).to_be_enabled()
            page.get_by_role("button", name="下一页模板", exact=True).click()
            expect(page.get_by_text("共 12 份模板 · 第 2/2 页", exact=True)).to_be_visible()
            page.get_by_role("button", name="下载客户模板 ZIP", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("模板包暂时无法下载")
            self.assertEqual(downloads, [])
            page.get_by_role("button", name="重新读取模板清单", exact=True).click()
            expect(page.get_by_role("button", name="下载客户模板 ZIP", exact=True)).to_be_enabled()
            expect(page.get_by_role("textbox", name="搜索模板", exact=True)).to_have_value("示例")
            expect(page.get_by_text("共 12 份模板 · 第 2/2 页", exact=True)).to_be_visible()
            expect(page.get_by_role("row", name=re.compile("示例归档模板 11.xlsx"))).to_be_visible()
            self.assertEqual(downloads, [])
            with page.expect_download() as package:
                page.get_by_role("button", name="下载客户模板 ZIP", exact=True).click()
            with zipfile.ZipFile(package.value.path()) as archive:
                self.assertEqual(len(archive.namelist()), 13)
                self.assertIn("文件夹修订：preview-revision-2", archive.read("模板清单.txt").decode())
                self.assertIn("示例控制计划模板.xlsx", archive.namelist())
                self.assertIn("示例归档模板 12.xlsx", archive.namelist())
            page.get_by_role("button", name="返回审核", exact=True).click()
            expect(page.get_by_text("保留的审核草稿.xlsx", exact=True).first).to_be_visible()
            expect(page.get_by_role("combobox", name="客户（可选）")).to_contain_text("示例客户")
            browser.close()

    def test_global_support_is_available_on_mobile_and_preserves_current_upload(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(PREVIEW + "/preview/audit.html")
            page.get_by_label("选择审核文件", exact=True).set_input_files({"name": "保留草稿.xlsx", "mimeType": "application/octet-stream", "buffer": b"sample"})
            page.get_by_role("button", name="打开审核任务", exact=True).click()
            page.get_by_role("button", name="联系我们", exact=True).click()
            dialog = page.get_by_role("dialog", name="联系我们", exact=True)
            expect(dialog.get_by_text("服务信息暂不可用", exact=True)).to_be_visible()
            expect(dialog.get_by_role("textbox", name="页面参考编号", exact=True)).to_have_value(re.compile(r"^client-[a-zA-Z0-9-]+$"))
            dialog.get_by_role("button", name="关闭", exact=True).click()
            expect(page.get_by_text("保留草稿.xlsx", exact=True).first).to_be_visible()
            expect(page.get_by_role("button", name="打开审核任务", exact=True)).to_be_focused()
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            browser.close()
