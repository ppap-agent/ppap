"""#168 UI-only usage projection. No billing or external settlement effects."""

import os
import re
import unittest

PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")

if PREVIEW:
    from playwright.sync_api import expect, sync_playwright


@unittest.skipUnless(PREVIEW, "requires PPAP_PREVIEW_URL (preview-only UI)")
class UsagePreviewTests(unittest.TestCase):
    def test_returning_to_usage_unmounts_the_inactive_archive_history_sheet(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/__preview?page=audit&scene=usage-ready&view=usage&width=390")
            preview = page.frame_locator("iframe")
            preview.get_by_role("button", name="打开产品导航", exact=True).click()
            preview.get_by_role("navigation", name="产品导航").get_by_role("button", name="审核文件", exact=True).click()
            preview.get_by_role("row", name=re.compile("档案 A001")).get_by_role("button", name="查看审核记录", exact=True).click()
            expect(preview.get_by_role("heading", name="第 3 次审核", exact=True)).to_be_visible()
            preview.get_by_role("button", name="历史审核记录", exact=True).click()
            expect(preview.get_by_role("dialog", name="历史审核记录", exact=True)).to_be_visible()
            page.frames[1].evaluate("history.go(-2)")
            expect(preview.get_by_role("dialog", name="历史审核记录", exact=True)).to_have_count(0)
            expect(preview.get_by_role("table", name="逐文件用量记录", exact=True)).to_be_visible()
            page.get_by_role("button", name="切换明暗主题", exact=True).click()
            page.reload()
            expect(preview.get_by_role("table", name="逐文件用量记录", exact=True)).to_be_visible()
            self.assertIn("view=usage", page.url)
            browser.close()

    def test_service_notices_keep_the_three_confirmed_texts_and_open_shared_support(self):
        notices = {
            "usage-ai-disabled": "飞书AI包开关已关闭，请联系管理员在飞书管理后台启用或联系我们获取免费额度",
            "usage-no-package": "提示：试用额度不足，且未采购飞书AI包，请联系管理员在飞书管理后台进行充值或联系我们获取免费额度",
            "usage-paid-insufficient": "提示：飞书AI包额度不足，本次消耗将于充值后自动扣费，请联系管理员或联系我们获取免费额度",
        }
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(permissions=["clipboard-read", "clipboard-write"])
            for scene, notice in notices.items():
                page.goto(PREVIEW + f"/preview/audit.html?scene={scene}&view=usage")
                page.get_by_role("combobox", name="用量时间范围", exact=True).click()
                page.get_by_role("option", name="全部", exact=True).click()
                page.get_by_role("textbox", name="搜索文件名或发起人", exact=True).fill("示例")
                page.get_by_role("form", name="筛选用量记录", exact=True).get_by_role("button", name="搜索", exact=True).click()
                page.get_by_role("button", name="下一页用量", exact=True).click()
                expect(page.get_by_role("row", name="消费记录 示例消费-11", exact=True)).to_be_visible()
                address = page.url
                alert = page.get_by_role("alert")
                expect(alert).to_contain_text(notice)
                alert.get_by_role("button", name="联系我们", exact=True).click()
                dialog = page.get_by_role("dialog", name="联系我们", exact=True)
                expect(dialog).to_contain_text(notice)

                reference = dialog.get_by_label("参考编号", exact=True).input_value()
                self.assertTrue(reference.startswith("client-"))
                dialog.get_by_role("button", name="复制场景与编号", exact=True).click()
                expect(dialog.get_by_text("已复制。", exact=True)).to_be_visible()
                self.assertEqual(page.evaluate("navigator.clipboard.readText()"), f"用量记录 · {notice}\n{reference}")
                page.keyboard.press("Escape")
                self.assertEqual(page.url, address)
                expect(page.get_by_role("textbox", name="搜索文件名或发起人", exact=True)).to_have_value("示例")
                expect(page.get_by_role("combobox", name="用量时间范围", exact=True)).to_have_text("全部")
                expect(page.get_by_text("共 12 笔 · 第 2/2 页 · 每页10笔", exact=True)).to_be_visible()
                expect(page.get_by_role("row", name="消费记录 示例消费-11", exact=True)).to_be_visible()
                page.get_by_role("navigation", name="产品导航").get_by_role("button", name="模板库", exact=True).click()
                page.get_by_role("button", name="联系我们", exact=True).click()
                expect(dialog.get_by_text("当前场景：客户模板库", exact=True)).to_be_visible()
                expect(dialog).not_to_contain_text(notice)
                page.keyboard.press("Escape")
            browser.close()

    def test_read_failures_are_recoverable_and_stale_summary_is_explicit(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-error&view=usage")
            expect(page.get_by_role("alert")).to_contain_text("当前余额和结算状态待确认")
            expect(page.get_by_role("group", name="本月消费次数", exact=True)).to_contain_text("—")
            page.get_by_role("button", name="重新读取用量", exact=True).click()
            expect(page.get_by_role("row", name="消费记录 示例消费-1", exact=True)).to_be_visible()
            for scene in ("usage-refresh-error", "usage-foreign"):
                page.goto(PREVIEW + f"/preview/audit.html?scene={scene}&view=usage")
                expect(page.get_by_role("row", name="消费记录 示例消费-1", exact=True)).to_be_visible()
                page.get_by_role("button", name="刷新用量", exact=True).click()
                expect(page.get_by_role("alert")).to_contain_text("用量读取未完成")
                expect(page.get_by_text("以下是上次已读汇总，尚未确认当前状态。", exact=True)).to_be_visible()
                expect(page.get_by_role("row", name="消费记录 示例消费-1", exact=True).get_by_role("button", name="打开该次审核", exact=True)).to_be_disabled()
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-invalid&view=usage")
            expect(page.get_by_role("alert")).to_contain_text("用量读取未完成")
            expect(page.get_by_role("table", name="逐文件用量记录", exact=True)).to_have_count(0)
            browser.close()

    def test_deleted_denied_and_unmapped_history_keep_the_consumption_identity(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-unmapped&view=usage")
            for number, reason in ((1, "尚未确认文档关联"), (4, "当前账号无权读取这次审核"), (5, "这次审核记录已删除")):
                row = page.get_by_role("row", name=f"消费记录 示例消费-{number}", exact=True)
                expect(row).to_contain_text(reason)
                expect(row).to_contain_text("500 点")
                expect(row.get_by_role("button", name="打开该次审核", exact=True)).to_be_disabled()
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-ready&view=usage&archiveDoc=preview-archive-A001&auditRef=broken")
            expect(page.get_by_text("这次消费的审核记录暂不可用", exact=True)).to_be_visible()
            expect(page.get_by_role("heading", name=re.compile("第 .* 次审核"))).to_have_count(0)
            browser.close()

    def test_range_paging_empty_and_mobile_layout_keep_monthly_scope(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-ready&view=usage")
            for label in ("最近7天", "最近90天", "全部"):
                page.get_by_role("combobox", name="用量时间范围", exact=True).click()
                page.get_by_role("option", name=label, exact=True).click()
                expect(page.get_by_role("group", name="本月消费次数", exact=True)).to_contain_text("12")
            page.get_by_role("button", name="下一页用量", exact=True).click()
            expect(page.get_by_text("共 12 笔 · 第 2/2 页 · 每页10笔", exact=True)).to_be_visible()
            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
            page.get_by_role("textbox", name="搜索文件名或发起人", exact=True).fill("没有这个文件")
            page.get_by_role("form", name="筛选用量记录", exact=True).get_by_role("button", name="搜索", exact=True).click()
            expect(page.get_by_text("当前筛选下没有消费记录。", exact=True)).to_be_visible()
            expect(page.get_by_role("group", name="本月消费次数", exact=True)).to_contain_text("12")
            browser.close()

    def test_top_level_navigation_does_not_reuse_another_surfaces_history(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-ready&view=archive")
            page.get_by_role("row", name=re.compile("档案 A001")).get_by_role("button", name="查看审核记录", exact=True).click()
            expect(page.get_by_role("heading", name="第 3 次审核", exact=True)).to_be_visible()
            navigation = page.get_by_role("navigation", name="产品导航")
            navigation.get_by_role("button", name="用量记录", exact=True).click()
            expect(page.get_by_role("heading", name="用量记录", exact=True)).to_be_visible()
            page.get_by_role("row", name="消费记录 示例消费-1", exact=True).get_by_role("button", name="打开该次审核", exact=True).click()
            expect(page.get_by_role("heading", name="第 2 次审核", exact=True)).to_be_visible()
            navigation.get_by_role("button", name="用量记录", exact=True).click()
            expect(page.get_by_role("heading", name="用量记录", exact=True)).to_be_visible()
            page.get_by_role("row", name="消费记录 示例消费-1", exact=True).get_by_role("button", name="打开该次审核", exact=True).click()
            expect(page.get_by_role("heading", name="第 2 次审核", exact=True)).to_be_visible()
            navigation.get_by_role("button", name="审核文件", exact=True).click()
            expect(page.get_by_role("table", name="审核文件档案", exact=True)).to_be_visible()
            self.assertNotIn("auditRef=", page.url)
            browser.close()

    def test_cross_entry_browser_back_unmounts_mobile_history_portals(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/__preview?page=audit&scene=usage-ready&view=archive&width=390")
            preview = page.frame_locator("iframe")
            preview.get_by_role("button", name="打开产品导航", exact=True).click()
            preview.get_by_role("navigation", name="产品导航").get_by_role("button", name="用量记录", exact=True).click()
            preview.get_by_role("row", name="消费记录 示例消费-1", exact=True).get_by_role("button", name="打开该次审核", exact=True).click()
            preview.get_by_role("button", name="历史审核记录", exact=True).click()
            expect(preview.get_by_role("dialog", name="历史审核记录", exact=True)).to_be_visible()
            page.frames[1].evaluate("history.go(-2)")
            expect(preview.get_by_role("dialog", name="历史审核记录", exact=True)).to_have_count(0)
            expect(preview.get_by_role("table", name="审核文件档案", exact=True)).to_be_visible()
            page.get_by_role("button", name="切换明暗主题", exact=True).click()
            page.reload()
            expect(preview.get_by_role("table", name="审核文件档案", exact=True)).to_be_visible()
            browser.close()

    def test_returning_to_usage_rechecks_authority_instead_of_reusing_a_hidden_account(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-revoked&view=usage")
            expect(page.get_by_role("row", name="消费记录 示例消费-1", exact=True)).to_be_visible()
            page.get_by_role("navigation", name="产品导航").get_by_role("button", name="PPAP Audit", exact=True).click()
            page.get_by_role("navigation", name="产品导航").get_by_role("button", name="用量记录", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("当前账号无权读取企业用量。")
            expect(page.get_by_role("table", name="逐文件用量记录", exact=True)).to_have_count(0)
            browser.close()

    def test_unknown_quota_and_settlement_are_not_zero_or_confirmed_charged(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-quota-unknown&view=usage")
            expect(page.get_by_role("group", name="本月已扣点数", exact=True)).to_contain_text("—")
            expect(page.get_by_role("group", name="本月待扣点数", exact=True)).to_contain_text("—")
            expect(page.get_by_role("group", name="免费额度剩余/总额", exact=True)).to_contain_text("— / —")
            row = page.get_by_role("row", name="消费记录 示例消费-3", exact=True)
            expect(row).to_contain_text("待核对")
            expect(row).to_contain_text("扣费结果待确认。")
            expect(row).to_contain_text("尚未取得飞书订单号。")
            expect(page.get_by_role("button", name="重试扣费", exact=True)).to_have_count(0)
            browser.close()

    def test_late_read_cannot_replace_a_new_observation_of_the_same_query(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-race&view=usage")
            expect(page.get_by_text("旧用量查询", exact=True)).to_be_visible()
            page.get_by_role("form", name="筛选用量记录", exact=True).get_by_role("button", name="搜索", exact=True).click()
            charged = page.get_by_role("group", name="本月已扣点数", exact=True)
            expect(charged).to_contain_text("5,000")
            page.evaluate("window.postMessage({type: 'ppap-preview-read', failed: false}, location.origin)")
            expect(page.get_by_text("旧用量查询", exact=True)).to_have_count(0)
            expect(charged).to_contain_text("5,000")
            expect(page.get_by_role("row", name="消费记录 示例消费-3", exact=True)).to_contain_text("示例飞书订单-3")
            browser.close()

    def test_preview_harness_preserves_usage_filters_history_and_theme(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/__preview?page=audit&scene=usage-ready&view=usage")
            preview = page.frame_locator("iframe")
            expect(preview.get_by_role("heading", name="用量记录", exact=True)).to_be_visible()
            preview.get_by_role("button", name="下一页用量", exact=True).click()
            expect(preview.get_by_text("共 12 笔 · 第 2/2 页 · 每页10笔", exact=True)).to_be_visible()
            page.get_by_role("button", name="切换明暗主题", exact=True).click()
            page.reload()
            expect(preview.get_by_text("共 12 笔 · 第 2/2 页 · 每页10笔", exact=True)).to_be_visible()
            preview.get_by_role("row", name="消费记录 示例消费-11", exact=True).get_by_role("button", name="打开该次审核", exact=True).click()
            expect(preview.get_by_role("heading", name="第 1 次审核", exact=True)).to_be_visible()
            page.reload()
            expect(preview.get_by_role("heading", name="第 1 次审核", exact=True)).to_be_visible()
            preview.get_by_role("button", name="返回用量记录", exact=True).click()
            expect(preview.get_by_text("共 12 笔 · 第 2/2 页 · 每页10笔", exact=True)).to_be_visible()
            preview.get_by_role("row", name="消费记录 示例消费-11", exact=True).get_by_role("button", name="打开该次审核", exact=True).click()
            expect(preview.get_by_role("heading", name="第 1 次审核", exact=True)).to_be_visible()
            page.frames[1].evaluate("history.back()")
            expect(preview.get_by_text("共 12 笔 · 第 2/2 页 · 每页10笔", exact=True)).to_be_visible()
            page.get_by_role("button", name="切换明暗主题", exact=True).click()
            page.reload()
            expect(preview.get_by_text("共 12 笔 · 第 2/2 页 · 每页10笔", exact=True)).to_be_visible()
            browser.close()

    def test_revocation_clears_previously_read_account_data_instead_of_claiming_zero(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-revoked&view=usage")
            expect(page.get_by_role("row", name="消费记录 示例消费-1", exact=True)).to_be_visible()
            page.get_by_role("button", name="刷新用量", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("当前账号无权读取企业用量。")
            expect(page.get_by_role("table", name="逐文件用量记录", exact=True)).to_have_count(0)
            expect(page.get_by_role("group", name="本月消费次数", exact=True)).to_contain_text("—")
            browser.close()

    def test_usage_opens_the_charged_historical_version_and_returns_to_the_same_filter(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-ready&view=usage")
            row = page.get_by_role("row", name="消费记录 示例消费-1", exact=True)
            row.get_by_role("button", name="打开该次审核", exact=True).click()
            expect(page.get_by_role("heading", name="第 2 次审核", exact=True)).to_be_visible()
            self.assertIn("auditRef=", page.url)
            page.reload()
            expect(page.get_by_role("heading", name="第 2 次审核", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="打开来源任务", exact=True)).to_be_disabled()
            page.get_by_role("button", name="返回用量记录", exact=True).click()
            expect(row).to_contain_text("第 2 次审核")
            page.get_by_role("textbox", name="搜索文件名或发起人", exact=True).fill("示例王工")
            page.get_by_role("form", name="筛选用量记录", exact=True).get_by_role("button", name="搜索", exact=True).click()
            page.get_by_role("row", name="消费记录 示例消费-2", exact=True).get_by_role("button", name="打开该次审核", exact=True).click()
            page.go_back()
            expect(page.get_by_role("textbox", name="搜索文件名或发起人", exact=True)).to_have_value("示例王工")
            expect(page.get_by_role("row", name="消费记录 示例消费-1", exact=True)).to_have_count(0)
            browser.close()

    def test_file_audits_count_once_and_monthly_cards_do_not_follow_detail_filters(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(PREVIEW + "/preview/audit.html?scene=usage-ready&view=usage")
            expect(page.get_by_role("heading", name="用量记录", exact=True)).to_be_visible()
            monthly = page.get_by_role("region", name="自然月用量汇总", exact=True)
            expect(monthly.get_by_role("group", name="本月消费次数", exact=True)).to_contain_text("12")
            expect(page.get_by_role("table", name="逐文件用量记录", exact=True).get_by_role("row")).to_have_count(11)
            expect(page.get_by_role("row", name="消费记录 示例消费-1", exact=True)).to_contain_text("免费 200 · 付费 300")
            page.get_by_role("textbox", name="搜索文件名或发起人", exact=True).fill("示例王工")
            page.get_by_role("form", name="筛选用量记录", exact=True).get_by_role("button", name="搜索", exact=True).click()
            expect(page.get_by_role("row", name="消费记录 示例消费-2", exact=True)).to_be_visible()
            expect(monthly.get_by_role("group", name="本月消费次数", exact=True)).to_contain_text("12")
            expect(page.get_by_role("button", name="本月待扣点数", exact=True)).to_have_count(0)
            browser.close()
