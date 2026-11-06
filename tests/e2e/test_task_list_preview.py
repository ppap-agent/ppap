"""Task-list scope ownership with a stopped clock and held adapter replies."""

import os
import re
import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")


def held_list(page, query="全部", state="all", offset=0):
    # These are synthetic service replies, not user actions behind the modal.
    return page.get_by_text(f"任务列表 · {query} · {state} · {offset}", exact=True).locator("..")


def release_list(page, query="全部", *, failed=False, offset=0):
    held = held_list(page, query, offset=offset)
    expect(held).to_have_count(1)
    label = r"^令示例读取 \d+ 失败$" if failed else r"^返回示例读取 \d+$"
    held.get_by_role("button", name=re.compile(label), include_hidden=True).dispatch_event("click")
    expect(held).to_have_count(0)


def task_row(listing, number):
    return listing.get_by_role("button", name=re.compile(r"^PPAP-" + number + r"(?:\s|$)"))


def expect_unresolved(listing):
    expect(listing.get_by_role("status", name="正在读取任务…", exact=True)).to_be_visible()
    expect(listing.get_by_text("没有符合条件的任务。", exact=True)).to_have_count(0)
    expect(listing.get_by_text("开始识别后，任务显示在这里。", exact=True)).to_have_count(0)
    expect(listing.get_by_role("button", name="清空条件", exact=True)).to_have_count(0)


def open_list(browser, *, paged=False):
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.set_default_timeout(5000)
    page.clock.install(time=datetime(2026, 10, 16, 2, tzinfo=timezone.utc))
    page.goto(PREVIEW + "/preview/audit.html?scene=task-list-controlled" + ("-paged" if paged else ""))
    page.get_by_role("button", name="打开审核任务", exact=True).click()
    expect(held_list(page)).to_have_count(1)
    # The first reply is held, so advancing here cannot complete or repeat the read.
    page.clock.pause_at(datetime(2026, 10, 16, 2, 1, tzinfo=timezone.utc))
    switcher = page.get_by_role("dialog", name="审核任务", exact=True)
    return page, switcher, switcher.get_by_role("navigation", name="任务列表", exact=True)


@unittest.skipUnless(PREVIEW and sync_playwright, "requires PPAP_PREVIEW_URL and the e2e dependency group (preview-only UI)")
class TaskListPreviewTests(unittest.TestCase):
    def test_debounce_and_held_read_never_claim_empty_before_opening_the_named_task(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page, switcher, listing = open_list(browser)
            expect_unresolved(listing)
            search = switcher.get_by_role("textbox", name="搜索任务文件", exact=True)
            search.fill("甲任务")
            expect(search).to_have_value("甲任务")
            expect_unresolved(listing)
            page.clock.run_for(199)
            expect(held_list(page, "甲任务")).to_have_count(0)
            expect_unresolved(listing)
            page.clock.run_for(1)
            expect(held_list(page, "甲任务")).to_have_count(1)
            expect_unresolved(listing)
            # A superseded full-list reply must not end the current query's pending state.
            release_list(page)
            expect_unresolved(listing)
            release_list(page, "甲任务")
            row = task_row(listing, "甲任务")
            expect(row).to_contain_text("待确认")
            expect(task_row(listing, "乙任务")).to_have_count(0)
            row.click()
            expect(switcher).not_to_be_visible()
            expect(page.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
            self.assertEqual(parse_qs(urlparse(page.url).query)["task"], ["preview-list-甲任务"])
            browser.close()

    def test_failed_search_is_not_empty_and_retry_success_alone_enables_clear(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page, switcher, listing = open_list(browser)
            release_list(page)
            expect(task_row(listing, "甲任务")).to_be_visible()
            search = switcher.get_by_role("textbox", name="搜索任务文件", exact=True)
            search.fill("不存在")
            expect_unresolved(listing)
            page.clock.run_for(200)
            expect(held_list(page, "不存在")).to_have_count(1)
            expect_unresolved(listing)
            release_list(page, "不存在", failed=True)
            expect(switcher.get_by_role("alert")).to_contain_text("任务列表读取失败")
            expect(listing.get_by_role("status")).to_have_count(0)
            expect(listing.get_by_text("没有符合条件的任务。", exact=True)).to_have_count(0)
            expect(listing.get_by_role("button", name="清空条件", exact=True)).to_have_count(0)
            switcher.get_by_role("button", name="重新读取任务列表", exact=True).click()
            expect(held_list(page, "不存在")).to_have_count(1)
            expect_unresolved(listing)
            release_list(page, "不存在")
            expect(switcher.get_by_role("alert")).to_have_count(0)
            expect(listing.get_by_text("没有符合条件的任务。", exact=True)).to_be_visible()
            listing.get_by_role("button", name="清空条件", exact=True).click()
            expect(search).to_have_value("")
            page.clock.run_for(1)
            release_list(page)
            expect(task_row(listing, "甲任务")).to_be_visible()
            expect(task_row(listing, "乙任务")).to_be_visible()
            browser.close()

    def test_superseded_success_and_error_cannot_replace_newer_search_outcome(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for failed in (False, True):
                with self.subTest(stale_reply_failed=failed):
                    page, switcher, listing = open_list(browser)
                    search = switcher.get_by_role("textbox", name="搜索任务文件", exact=True)
                    search.fill("甲任务")
                    page.clock.run_for(200)
                    expect(held_list(page, "甲任务")).to_have_count(1)
                    search.fill("乙任务")
                    # Even an old error during the new query's debounce belongs to its old scope.
                    release_list(page, failed=True)
                    expect(switcher.get_by_role("alert")).to_have_count(0)
                    expect_unresolved(listing)
                    page.clock.run_for(200)
                    release_list(page, "乙任务")
                    expect(task_row(listing, "乙任务")).to_be_visible()
                    release_list(page, "甲任务", failed=failed)
                    expect(search).to_have_value("乙任务")
                    expect(task_row(listing, "乙任务")).to_be_visible()
                    expect(task_row(listing, "甲任务")).to_have_count(0)
                    expect(switcher.get_by_role("alert")).to_have_count(0)
                    expect(listing.get_by_role("status")).to_have_count(0)
                    page.close()
            browser.close()

    def test_failed_refresh_keeps_cached_tasks_and_retries_the_same_scope(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page, switcher, listing = open_list(browser)
            release_list(page)
            expect(task_row(listing, "甲任务")).to_be_visible()
            page.clock.run_for(15000)
            release_list(page, failed=True)
            expect(switcher.get_by_role("alert")).to_contain_text("任务列表读取失败")
            expect(task_row(listing, "甲任务")).to_be_visible()
            expect(task_row(listing, "乙任务")).to_be_visible()
            expect(listing.get_by_text("开始识别后，任务显示在这里。", exact=True)).to_have_count(0)
            switcher.get_by_role("button", name="重新读取任务列表", exact=True).click()
            release_list(page)
            expect(switcher.get_by_role("alert")).to_have_count(0)
            expect(task_row(listing, "甲任务")).to_be_visible()
            browser.close()

    def test_pagination_keeps_loaded_tasks_and_does_not_publish_old_scope_pages(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page, switcher, listing = open_list(browser, paged=True)
            release_list(page)
            expect(task_row(listing, "甲任务")).to_be_visible()
            expect(task_row(listing, "乙任务")).to_have_count(0)
            switcher.get_by_role("button", name="加载更多任务", exact=True).click()
            expect(held_list(page, offset=1)).to_have_count(1)
            expect(task_row(listing, "甲任务")).to_be_visible()
            search = switcher.get_by_role("textbox", name="搜索任务文件", exact=True)
            search.fill("不存在")
            expect_unresolved(listing)
            page.clock.run_for(200)
            release_list(page, "不存在")
            expect(listing.get_by_text("没有符合条件的任务。", exact=True)).to_be_visible()
            release_list(page, offset=1)
            expect(search).to_have_value("不存在")
            expect(listing.get_by_text("没有符合条件的任务。", exact=True)).to_be_visible()
            expect(switcher.get_by_role("button", name="加载更多任务", exact=True)).to_have_count(0)
            browser.close()

    def test_opened_pages_refresh_atomically_and_failed_page_keeps_cached_tasks(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page, switcher, listing = open_list(browser, paged=True)
            release_list(page)
            switcher.get_by_role("button", name="加载更多任务", exact=True).click()
            release_list(page, offset=1)
            expect(task_row(listing, "甲任务")).to_be_visible()
            expect(task_row(listing, "乙任务")).to_be_visible()
            expect(switcher.get_by_role("button", name="加载更多任务", exact=True)).to_have_count(0)
            page.clock.run_for(15000)
            release_list(page)
            expect(held_list(page, offset=1)).to_have_count(1)
            expect(task_row(listing, "乙任务")).to_be_visible()
            release_list(page, offset=1, failed=True)
            expect(switcher.get_by_role("alert")).to_contain_text("任务列表读取失败")
            expect(task_row(listing, "甲任务")).to_be_visible()
            expect(task_row(listing, "乙任务")).to_be_visible()
            switcher.get_by_role("button", name="重新读取任务列表", exact=True).click()
            release_list(page)
            release_list(page, offset=1)
            expect(switcher.get_by_role("alert")).to_have_count(0)
            expect(task_row(listing, "甲任务")).to_be_visible()
            expect(task_row(listing, "乙任务")).to_be_visible()
            browser.close()

    def test_state_filter_waits_for_its_own_successful_result(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page, switcher, listing = open_list(browser)
            release_list(page)
            expect(task_row(listing, "甲任务")).to_be_visible()
            switcher.get_by_role("combobox", name="任务状态", exact=True).click()
            page.get_by_role("option", name="已完成", exact=True).click()
            expect_unresolved(listing)
            page.clock.run_for(1)
            held = held_list(page, state="completed")
            expect(held).to_have_count(1)
            expect_unresolved(listing)
            held.get_by_role("button", name=re.compile(r"^返回示例读取 \d+$"), include_hidden=True).dispatch_event("click")
            expect(listing.get_by_text("没有符合条件的任务。", exact=True)).to_be_visible()
            expect(task_row(listing, "甲任务")).to_have_count(0)
            browser.close()

    def test_workbench_does_not_count_a_pending_or_failed_search_as_zero_results(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(5000)
            page.clock.install(time=datetime(2026, 10, 16, 2, tzinfo=timezone.utc))
            page.goto(PREVIEW + "/preview/audit.html?scene=task-list-controlled&view=workbench")
            expect(held_list(page)).to_have_count(1)
            page.clock.pause_at(datetime(2026, 10, 16, 2, 1, tzinfo=timezone.utc))
            workbench = page.get_by_role("region", name="工作台", exact=True)
            loading = workbench.get_by_role("status", name="正在读取审核记录…", exact=True)
            expect(loading).to_be_visible()
            release_list(page)
            expect(workbench.get_by_role("tab", name=re.compile("^全部任务"))).to_contain_text("2")
            workbench.get_by_role("textbox", name="搜索审核任务", exact=True).fill("PFMEA")
            workbench.get_by_role("tab", name=re.compile("^已完成")).click()
            # Cached tasks belong to another queue; their presence cannot prove this queue empty.
            expect(loading).to_be_visible()
            expect(workbench.get_by_text("暂无已完成的记录。", exact=True)).to_have_count(0)
            workbench.get_by_role("tab", name=re.compile("^全部任务")).click()
            workbench.get_by_role("textbox", name="搜索审核任务", exact=True).fill("不存在")
            expect(loading).to_be_visible()
            expect(workbench.get_by_text("暂无全部任务的记录。", exact=True)).to_have_count(0)
            page.clock.run_for(200)
            release_list(page, "不存在", failed=True)
            expect(workbench.get_by_role("alert")).to_contain_text("任务列表读取失败")
            expect(workbench.get_by_text("暂无全部任务的记录。", exact=True)).to_have_count(0)
            expect(workbench.get_by_role("tab", name=re.compile("^全部任务"))).to_contain_text("—")
            workbench.get_by_role("button", name="刷新任务列表", exact=True).click()
            release_list(page, "不存在")
            expect(workbench.get_by_role("alert")).to_have_count(0)
            expect(workbench.get_by_role("tab", name=re.compile("^全部任务"))).to_contain_text("0")
            expect(workbench.get_by_role("table", name="全部任务审核记录", exact=True).get_by_text("暂无全部任务的记录。", exact=True)).to_be_visible()
            browser.close()
