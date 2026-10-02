"""Representative browser audit loop against isolated-dev stack."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from ppap_core.fixtures import RULE_A

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover - optional until the e2e job installs Playwright
    sync_playwright = None
else:
    expect.set_options(timeout=20_000)


WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


def isolated_dev_login(page):
    page.goto(f"{WEB}/dev")
    page.get_by_test_id("isolated-dev-login").click()
    page.wait_for_selector('[data-testid="session-user"]', state="attached")


def open_nav(page):
    menu = page.get_by_role("button", name="菜单")
    if menu.count() and menu.is_visible():
        menu.click()


def pc_nav(page, test_id):
    return page.get_by_test_id("pc-shell").get_by_test_id(test_id)


def mobile_nav(page, test_id):
    return page.locator(f'.mobile-tab-bar [data-testid="{test_id}"]')


def finding_list(page):
    return page.get_by_role("navigation", name="审核条款")


def current_finding(page):
    nav = finding_list(page)
    selected = nav.locator("[aria-current='true']")
    return selected if selected.count() else nav.get_by_role("button").first


def close_finding_sheet(page):
    detail = page.get_by_role("dialog", name="检查项详情")
    if detail.count() and detail.is_visible():
        detail.get_by_role("button", name="关闭").click()


@unittest.skipUnless(sync_playwright, "playwright not installed")
class WebAuditLoopTests(unittest.TestCase):
    def test_submit_leave_return_review_and_resubmit(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "report.xlsx"
            reference = Path(tmp) / "context.pdf"
            newer = Path(tmp) / "report-v2.xlsx"
            target.write_bytes(b"browser-target-v1")
            reference.write_bytes(b"browser-reference")
            newer.write_bytes(b"browser-target-v2")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                page.set_viewport_size({"width": 390, "height": 844})
                isolated_dev_login(page)
                page.get_by_test_id("source-upload").set_input_files(str(target))
                page.wait_for_selector('[data-testid="draft-items"]')
                page.get_by_test_id("item-rule").first.select_option(RULE_A)
                page.get_by_test_id("source-upload").set_input_files(str(reference))
                page.wait_for_function("() => document.querySelector('[data-testid=\"draft-items\"]').innerText.includes('context.pdf')")
                page.get_by_role("checkbox", name="参与审核：context.pdf").uncheck()
                page.wait_for_function("() => new URL(window.location.href).searchParams.get('draft')")
                save = page.get_by_test_id("save-draft")
                if save.is_enabled():
                    save.click()
                page.get_by_test_id("submit-audit").click()
                page.wait_for_function("() => new URL(window.location.href).searchParams.get('round')")
                round_id = page.evaluate("() => new URL(window.location.href).searchParams.get('round')")
                page.goto(WEB)
                expect(page.locator("#audit-preparation")).to_be_visible()
                page.goto(f"{WEB}/?round={round_id}")
                page.wait_for_selector('[data-testid="result-summary"]')
                status = page.get_by_test_id("finding-status").first
                if not status.is_visible():
                    current_finding(page).click()
                expect(page.get_by_test_id("finding-status").first).to_have_attribute("data-status", "PASS")
                expect(page.get_by_test_id("result-summary")).to_contain_text("通过")
                close_finding_sheet(page)
                original_tab = page.get_by_role("tab", name="原件", exact=True)
                expect(original_tab).to_be_visible()
                original_tab.click()
                expect(page.get_by_test_id("file-version-list")).to_contain_text("report.xlsx")
                expect(page.get_by_test_id("file-version-list")).to_contain_text("context.pdf")
                original = page.get_by_test_id("open-original")
                gap = page.get_by_test_id("locator-gap")
                if original.count() == 0:
                    expect(gap).to_be_visible()
                else:
                    href = original.get_attribute("href")
                    self.assertTrue(href)
                    downloaded = page.request.get(href)
                    self.assertEqual(downloaded.status, 200)
                    self.assertEqual(downloaded.body(), b"browser-target-v1")
                    expect(page.get_by_test_id("file-preview")).to_be_visible()
                    expect(page.get_by_test_id("file-preview-name")).to_contain_text("report.xlsx")
                    page.get_by_role("tab", name="检查与复核").click()
                if not page.get_by_test_id("finding-heading").first.is_visible():
                    current_finding(page).click()
                expect(page.get_by_test_id("finding-heading").first).to_be_visible()
                expect(page.get_by_test_id("finding-evidence").first).to_be_visible()
                expect(page.get_by_test_id("nav-audit-files").last).to_be_visible()
                self.assertEqual(page.locator("h1").count(), 1)
                page.get_by_role("tab", name="其他").click()
                page.get_by_test_id("review-judgment").focus()
                self.assertEqual(page.evaluate("() => document.activeElement?.getAttribute('data-testid')"), "review-judgment")
                page.keyboard.press("Tab")
                self.assertEqual(page.evaluate("() => document.activeElement?.getAttribute('data-testid')"), "review-rationale")
                next_finding = page.get_by_role("button", name="下一检查项")
                expect(next_finding).to_be_visible()
                if next_finding.is_enabled():
                    next_finding.focus()
                    self.assertEqual(page.evaluate("() => document.activeElement?.getAttribute('aria-label')"), "下一检查项")
                page.get_by_test_id("review-judgment").fill("需修改")
                page.get_by_test_id("review-rationale").fill("尺寸缺失")
                page.get_by_test_id("review-next").fill("补传")
                back_to_list = page.get_by_role("button", name="← 检查项")
                if back_to_list.is_visible():
                    back_to_list.click()
                current_finding(page).click()
                expect(page.get_by_role("dialog", name="保存修改后离开？")).to_have_count(0)
                expect(page.get_by_role("dialog", name="离开前处理待核对文件？")).to_have_count(0)
                expect(page.get_by_test_id("review-rationale")).to_have_value("尺寸缺失")
                expect(page.get_by_test_id("review-next")).to_have_value("补传")
                page.get_by_test_id("save-review").click()
                page.wait_for_selector("text=复核已保存")
                close_finding_sheet(page)
                page.get_by_test_id("prepare-again").click()
                page.wait_for_selector("#audit-preparation")
                page.wait_for_selector('[data-testid="submit-audit"]:not([disabled])')
                page.get_by_test_id("source-upload").set_input_files(str(newer))
                page.wait_for_function("""() => document.querySelector('[data-testid="draft-items"]').innerText.includes('report-v2.xlsx')""")
                page.get_by_test_id("item-rule").last.select_option(RULE_A)
                page.get_by_test_id("submit-audit").click()
                page.wait_for_selector('[data-testid="previous-round"]')
                expect(page.get_by_test_id("previous-round")).to_be_visible()
                page.get_by_test_id("previous-round").click()
                page.wait_for_selector('[data-testid="review-judgment"]')
                expect(page.get_by_test_id("review-judgment")).to_have_value("需修改")
                expect(page.get_by_test_id("nav-audit-files").last).to_be_visible()
                expect(page.get_by_test_id("finding-heading").first).to_be_visible()
                close_finding_sheet(page)
                page.get_by_role("tab", name="原件", exact=True).click()
                expect(page.get_by_test_id("file-version-list")).to_contain_text("report.xlsx")
                page.locator('[data-ui="mobile-account"] [data-ui="account-button"]').click()
                page.get_by_test_id("nav-usage").click()
                page.wait_for_url("**/usage")
                page.wait_for_function(
                    """() => ['usage-list', 'usage-empty', 'usage-error'].some((id) => {
                      return Boolean(document.querySelector('[data-testid=\"' + id + '\"]'));
                    })"""
                )
                usage_error = page.get_by_test_id("usage-error")
                if usage_error.count() and usage_error.is_visible():
                    self.fail(usage_error.inner_text())
                page.evaluate("() => document.querySelector('.mobile-tab-bar [data-testid=\"nav-audit-files\"]').click()")
                page.wait_for_url("**/audit-files")
                page.wait_for_function(
                    """() => ['history-list', 'history-empty', 'history-error'].some((id) => {
                      return Boolean(document.querySelector('[data-testid=\"' + id + '\"]'));
                    })"""
                )
                history_error = page.get_by_test_id("history-error")
                if history_error.count() and history_error.is_visible():
                    self.fail(history_error.inner_text())
                expect(page.get_by_test_id("history-list")).to_be_visible()
                expect(page.locator('[data-testid="history-result"]', has_text="通过").first).to_be_visible()
                expect(page.get_by_test_id("history-version").first).not_to_have_text("")
                browser.close()

    def test_docs_addon_entry_reports_missing_host(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_viewport_size({"width": 560, "height": 600})
            page.goto(f"{WEB}/addon")
            page.wait_for_selector("text=当前环境不支持 docs-addon 登录")
            expect(page.get_by_test_id("addon-history")).to_be_visible()
            href = page.get_by_test_id("addon-history").get_attribute("href")
            self.assertTrue(href and href.rstrip("/").endswith("audit-files"))
            self.assertNotIn(".html", href)
            expect(page.get_by_test_id("addon-submit")).to_be_disabled()
            browser.close()

    def test_audit_files_path_loads_history_after_login(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            isolated_dev_login(page)
            pc_nav(page, "nav-audit-files").click()
            page.wait_for_url("**/audit-files")
            page.wait_for_function(
                """() => ['history-list', 'history-empty', 'history-error'].some((id) => {
                  return Boolean(document.querySelector('[data-testid=\"' + id + '\"]'));
                })"""
            )
            error = page.get_by_test_id("history-error")
            if error.count() and error.is_visible():
                self.fail(error.inner_text())
            visible = page.get_by_test_id("history-list").or_(page.get_by_test_id("history-empty"))
            expect(visible).to_be_visible()
            self.assertTrue(page.url.rstrip("/").endswith("/audit-files"))
            browser.close()

    def test_return_from_rules_restores_file_finding_filter_and_review_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "alpha.xlsx"
            second = Path(tmp) / "beta.xlsx"
            first.write_bytes(b"browser-alpha")
            second.write_bytes(b"browser-beta")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                page.set_viewport_size({"width": 1440, "height": 900})
                isolated_dev_login(page)
                page.get_by_test_id("source-upload").set_input_files(str(first))
                page.wait_for_selector('[data-testid="draft-items"]')
                page.get_by_test_id("item-rule").first.select_option(RULE_A)
                page.get_by_test_id("source-upload").set_input_files(str(second))
                page.wait_for_function("() => document.querySelector('[data-testid=\"draft-items\"]').innerText.includes('beta.xlsx')")
                page.get_by_test_id("item-rule").last.select_option(RULE_A)
                page.wait_for_selector('[data-testid="submit-audit"]:not([disabled])')
                page.get_by_test_id("submit-audit").click()
                page.wait_for_function("() => new URL(window.location.href).searchParams.get('round')")
                round_id = page.evaluate("() => new URL(window.location.href).searchParams.get('round')")
                page.wait_for_selector('[data-testid="finding-status"]')
                page.wait_for_function("() => document.body.innerText.includes('检查完成')")
                page.wait_for_function(
                    """() => {
                      const sel = document.querySelector('[data-testid="file-version-list"]');
                      return Boolean(sel && sel.options.length >= 2);
                    }"""
                )
                default_file = page.get_by_test_id("file-version-list").evaluate("el => el.options[el.selectedIndex].text")
                self.assertIn("alpha.xlsx", default_file)
                expect(page.get_by_test_id("filter-all")).to_have_attribute("aria-pressed", "true")
                page.get_by_test_id("file-version-list").select_option(label="beta.xlsx")
                page.wait_for_function("() => document.querySelector('[data-testid=\"file-version-list\"]')?.selectedOptions[0]?.text.includes('beta.xlsx')")
                page.get_by_test_id("filter-PASS").click()
                expect(page.get_by_test_id("filter-PASS")).to_have_attribute("aria-pressed", "true")
                heading = page.get_by_test_id("finding-heading").inner_text()
                finding_key = page.get_by_test_id("finding-detail").get_attribute("data-finding-key")
                self.assertTrue(finding_key)
                page.get_by_test_id("context-link").click()
                page.wait_for_url("**/rules**")
                return_link = page.get_by_test_id("rule-return")
                expect(return_link).to_be_visible()
                expect(return_link).to_have_text("← 返回审核结果")
                return_link.click()
                page.wait_for_function("() => new URL(window.location.href).searchParams.get('round')")
                page.wait_for_selector('[data-testid="finding-status"]')
                restored_file = page.get_by_test_id("file-version-list").evaluate("el => el.options[el.selectedIndex].text")
                self.assertIn("beta.xlsx", restored_file)
                expect(page.get_by_test_id("filter-PASS")).to_have_attribute("aria-pressed", "true")
                expect(page.get_by_test_id("filter-all")).to_have_attribute("aria-pressed", "false")
                expect(page.get_by_test_id("finding-detail")).to_have_attribute("data-finding-key", finding_key)
                expect(page.get_by_test_id("finding-heading")).to_have_text(heading)
                page.goto(f"{WEB}/?round={round_id}&review=next")
                page.wait_for_selector('[data-testid="finding-status"]')
                expect(page.get_by_test_id("filter-unreviewed")).to_have_attribute("aria-pressed", "true")
                expect(page.get_by_test_id("filter-PASS")).to_have_attribute("aria-pressed", "false")
                next_file = page.get_by_test_id("file-version-list").evaluate("el => el.options[el.selectedIndex].text")
                next_key = page.get_by_test_id("finding-detail").get_attribute("data-finding-key")
                self.assertTrue(next_key)
                self.assertIn("xlsx", next_file)
                browser.close()


if __name__ == "__main__":
    unittest.main()
