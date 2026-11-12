"""Task categories and browser navigation at the public read-only HTTP boundary."""

import copy
import os
import re
from urllib.parse import urlsplit

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")
TASK = "00000000-0000-4000-8000-000000000001"
ROUND = "00000000-0000-4000-8000-000000000002"


def task_routes(page, partial_error):
    clause = {"id": "1", "category": "签字", "priority": None, "rule_to_check": "原件必须有签字", "type_of_rule": "text"}
    rule = {"id": "fixed-rule", "title": "原签字要求", "body": {"kind": "engine", "type": "控制计划", "rule_profile": "v3", "customers": [], "models": [], "parts": [], "rules": [clause]}}
    version = {
        "id": "original-version",
        "file_id": "original-file",
        "original_filename": "原控制计划.xlsx",
        "status": "ready",
        "content_type": "application/octet-stream",
        "byte_size": 8,
        "sha256": None,
    }
    finding = {"item_key": "1", "status": "NG", "item": "签字为空", "classification": "签字", "suggestions": "", "priority": None, "locator": None, "review": None}
    audit = {
        "round_id": ROUND,
        "file_version_id": version["id"],
        "result_attempt": 1,
        "rule": rule,
        "execution": "completed",
        "progress": {"completed": 1, "total": 1},
        "failure": None,
        "conclusion": "NG",
        "counts": {"pass_count": 0, "ng_count": 1, "unknown_count": 0, "not_covered_count": 0, "error_count": 0},
        "checks": [{"rule_item": clause, "execution": "completed", "coverage": "executed", "finding": finding, "evidence": [], "scope": None, "evidence_verified": False, "review": None}],
        "unmapped_findings": [],
        "summary": {"conclusion": "NG", "text": "不符合 1 项。", "corrective_actions": [], "extended_suggestions": []},
        "report": {"state": "not_eligible", "report_id": None, "reason": "本测试不读取报告。"},
    }
    file = {
        "file": version,
        "selection": "selected",
        "removal_reason": None,
        "validation": {"state": "valid", "reasons": []},
        "match": {"state": "matched", "document_type": "控制计划", "metadata": {"customer": None, "model": None, "part": None, "part_number": None}, "rule": rule, "gaps": []},
        "audit": audit,
    }
    task = {
        "id": TASK,
        "number": "PPAP-状态核对",
        "state": "completed",
        "execution": "completed",
        "has_execution_error": partial_error,
        "created_at": "2026-10-20T00:00:00Z",
        "updated_at": "2026-10-20T00:00:00Z",
        "input_revision": 0,
        "source_draft_id": TASK,
        "round_id": ROUND,
        "customer_preference": None,
        "confirmation_basis_digest": None,
        "confirmation_scope": None,
        "completion_reason": "processed",
        "estimated_points": 500,
        "acceptance": {"request_id": "original-confirmation", "state": "accepted", "reason": None},
        "files": [file],
    }
    if partial_error:
        failed = copy.deepcopy(file)
        failed["file"].update(id="failed-version", file_id="failed-file", original_filename="读取失败的控制计划.xlsx")
        failed["audit"].update(
            file_version_id="failed-version",
            execution="review_failed",
            conclusion="UNKNOWN",
            summary=None,
            counts={"pass_count": 0, "ng_count": 0, "unknown_count": 0, "not_covered_count": 0, "error_count": 1},
            failure={"code": "file_execution_error", "message": "本文件执行失败。", "retryable": False},
        )
        failed["audit"]["checks"][0].update(execution="failed", coverage="execution_error", finding=None)
        task["files"].append(failed)
    summary = {key: task[key] for key in ("id", "number", "state", "execution", "has_execution_error", "created_at", "updated_at", "input_revision", "round_id", "completion_reason")}
    summary.update(
        result_counts={"PASS": 0, "NG": 1, "ERR": 0, "UNABLE": 0, "NA": 0},
        uncovered_count=int(partial_error),
        reviewed_count=0,
        files=[{"file": row["file"], "selection": row["selection"]} for row in task["files"]],
    )
    calls, held = [], []
    page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))

    def respond(route):
        path = urlsplit(route.request.url).path.removeprefix("/api")
        calls.append((route.request.method, path))
        if route.request.method != "GET":
            route.fulfill(status=405, json={"error": "read_only_test"})
        elif path == "/public-config":
            route.fulfill(json={"profile": "isolated-dev", "login": "isolated-dev", "current_access": "allowed", "usage_charge_enabled": False})
        elif path == "/me":
            route.fulfill(json={"display_name": "测试用户", "tenant_id": "test-tenant", "user_id": "test-user", "membership": "member"})
        elif path == "/audit-tasks":
            route.fulfill(json={"tasks": [summary], "next_offset": None})
        elif path in {"/drafts", "/rounds"}:
            route.fulfill(json={path.removeprefix("/"): [], "next_offset": None})
        elif path == "/audit-tasks/" + TASK:
            route.fulfill(json=task)
        else:
            route.fulfill(status=404, json={"error": "unexpected_test_request"})

    page.route("**/api/**", respond)
    return calls, summary, held


@pytest.mark.parametrize("partial_error", [False, True], ids=["normal-ng", "execution-error"])
@pytest.mark.parametrize("width", [1440, 390])
def test_category_and_explicit_queue_survive_task_back_and_reload(partial_error, width):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 960})
        page.set_default_timeout(5000)
        calls, summary, held = task_routes(page, partial_error)
        page.goto(WEB + "/workbench")
        expect(page.get_by_role("tab", name=re.compile("^全部任务"))).to_have_attribute("aria-selected", "true")
        expected, other = ("需要处理", "已完成") if partial_error else ("已完成", "需要处理")

        def row_for(label):
            return page.get_by_role("table").get_by_role("row").filter(has_text="PPAP-状态核对") if width > 720 else page.get_by_role("list", name=label + "审核记录卡片", exact=True)

        page.get_by_role("tab", name=re.compile("^" + other)).click()
        expect(row_for(other)).to_have_count(0)
        page.get_by_role("tab", name=re.compile("^" + expected)).click()

        def check_selected():
            expect(page.get_by_role("tab", name=re.compile("^" + expected))).to_have_attribute("aria-selected", "true")
            row = row_for(expected)
            expect(row).to_contain_text("不符合 1")
            if partial_error:
                expect(row).to_contain_text("存在执行异常")
                expect(row).to_contain_text("读取失败的控制计划.xlsx")
            return row

        check_selected().get_by_role("link", name="查看异常" if partial_error else "查看结果", exact=True).click()
        expect(page.get_by_role("region", name="全部文件概览", exact=True)).to_be_visible()
        # Hold the list response so the detail-derived summary cannot be masked
        # by a second server summary. No component state or audit result is mocked.
        page.route("**/api/audit-tasks?*", lambda route: held.append(route))
        page.go_back()
        expect(page).to_have_url(WEB + "/workbench")
        check_selected()
        page.unroute("**/api/audit-tasks?*")
        for route in held:
            route.fulfill(json={"tasks": [summary], "next_offset": None})
        page.reload()
        check_selected()
        assert calls.count(("GET", "/audit-tasks/" + TASK)) == 1
        assert all(method == "GET" for method, _path in calls)
        browser.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_queue_tracks_history_entries_without_leaving_the_workbench(width):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 960})
        page.set_default_timeout(5000)
        calls, _summary, _held = task_routes(page, False)
        page.goto(WEB + "/workbench")
        completed = page.get_by_role("tab", name=re.compile("^已完成"))
        attention = page.get_by_role("tab", name=re.compile("^需要处理"))
        completed.click()
        menu = page.get_by_role("button", name="打开产品导航", exact=True)
        if menu.is_visible():
            menu.click()
        page.get_by_role("button", name="工作台", exact=True).click()
        attention.click()
        page.go_back()
        expect(completed).to_have_attribute("aria-selected", "true")
        page.go_forward()
        expect(attention).to_have_attribute("aria-selected", "true")
        assert not any(path == "/audit-tasks/" + TASK for _method, path in calls)
        assert all(method == "GET" for method, _path in calls)
        browser.close()


def test_opened_task_remains_visible_until_its_workbench_list_is_complete():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        calls, summary, _held = task_routes(page, False)
        other = {**summary, "id": "00000000-0000-4000-8000-000000000003", "number": "PPAP-另一任务"}

        def read_tasks(route):
            calls.append((route.request.method, "/audit-tasks"))
            last = "offset=50" in route.request.url
            route.fulfill(json={"tasks": [summary if last else other], "next_offset": None if last else 50})

        page.route("**/api/audit-tasks?*", read_tasks)
        page.goto(WEB + "/tasks/" + TASK)
        expect(page.get_by_role("region", name="全部文件概览", exact=True)).to_be_visible()
        page.get_by_role("navigation", name="当前位置", exact=True).get_by_role("link", name="工作台", exact=True).click()
        expect(page.get_by_role("heading", name="工作台", exact=True)).to_be_visible()
        table = page.get_by_role("table", name="全部任务审核记录", exact=True)
        expect(table.get_by_role("row").filter(has_text="PPAP-另一任务")).to_be_visible()
        expect(table.get_by_role("row").filter(has_text="PPAP-状态核对")).to_be_visible()
        expect(page.get_by_role("tab", name="全部任务", exact=False)).to_have_text("全部任务")
        page.get_by_role("button", name="加载更多记录", exact=True).click()
        expect(page.get_by_role("tab", name="全部任务", exact=False)).to_have_text("全部任务2")
        expect(table.get_by_role("row").filter(has_text="PPAP-状态核对")).to_be_visible()
        assert calls.count(("GET", "/audit-tasks/" + TASK)) == 1
        assert all(method == "GET" for method, _path in calls)
        browser.close()
