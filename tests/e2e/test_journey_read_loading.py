"""Retained journey read placeholders at the controlled public HTTP boundary."""

import os
from urllib.parse import urlsplit

import pytest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    expect = sync_playwright = None

pytestmark = pytest.mark.skipif(sync_playwright is None, reason="requires the e2e dependency group")
WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")
PRINCIPAL = {"user_id": "loading-user", "tenant_id": "loading-tenant", "membership": "member", "display_name": "读取测试"}
ROUND = {
    "id": "loading-round",
    "execution": "completed",
    "predecessor_id": None,
    "analysis_mode": "standard",
    "inputs": [{"file_version_id": "loading-version", "original_filename": "已固定原件.xlsx", "role": "target", "rule": None}],
    "result_counts": {"PASS": 1},
    "reviewed_count": 0,
    "created_at": "2026-10-22T00:00:00Z",
    "case_title": "",
    "case_scope": "",
}


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))
        yield page
        context.close()
        browser.close()


def test_journey_login_read_has_a_named_skeleton_and_keeps_explicit_login(page):
    pending, writes = [], []

    def respond(route):
        path = urlsplit(route.request.url).path.removeprefix("/api")
        if route.request.method != "GET":
            writes.append(route.request.method)
            route.fulfill(status=405, json={"error": "unexpected_write"})
        elif path == "/public-config":
            route.fulfill(json={"profile": "isolated-dev", "login": "feishu", "feishu_app_id": "test-app"})
        elif path == "/me":
            pending.append(route)
        else:
            route.fulfill(status=404, json={"error": "unexpected_test_request"})

    page.route("**/api/**", respond)
    page.goto(WEB + "/dev")
    status = page.get_by_role("status", name="正在确认登录状态…", exact=True)
    expect(status).to_be_visible()
    expect(status.locator('[aria-hidden="true"] [data-slot="skeleton"]').first).to_be_visible()
    expect(page.get_by_role("link", name="使用飞书登录", exact=True)).to_have_count(0)
    assert len(pending) == 1
    pending[0].fulfill(status=401, json={"error": "unauthenticated"})
    expect(page.get_by_role("link", name="使用飞书登录", exact=True)).to_be_visible()
    expect(status).to_have_count(0)
    assert writes == []


@pytest.mark.parametrize("width", [1280, 390])
def test_journey_table_read_preserves_rows_on_refresh_and_read_failure(page, width):
    page.set_viewport_size({"width": width, "height": 900})
    pending, writes = [], []

    def respond(route):
        path = urlsplit(route.request.url).path.removeprefix("/api")
        if route.request.method != "GET":
            writes.append(route.request.method)
            route.fulfill(status=405, json={"error": "unexpected_write"})
        elif path == "/public-config":
            route.fulfill(json={"profile": "isolated-dev", "login": "feishu", "feishu_app_id": "test-app"})
        elif path == "/me":
            route.fulfill(json=PRINCIPAL)
        elif path in {"/drafts", "/rounds"}:
            pending.append((route, path))
        elif path == "/rules":
            route.fulfill(json={"rules": []})
        elif path == "/rule-types":
            route.fulfill(json={"types": []})
        else:
            route.fulfill(status=404, json={"error": "unexpected_test_request"})

    def finish_reads(*, fail_round=False):
        assert len(pending) == 2
        for route, path in pending:
            if path == "/rounds" and fail_round:
                route.fulfill(status=503, json={"error": "read_unavailable"})
            else:
                route.fulfill(json={"drafts": [], "next_offset": None} if path == "/drafts" else {"rounds": [ROUND], "next_offset": None})
        pending.clear()

    page.route("**/api/**", respond)
    page.goto(WEB + "/dev")
    page.get_by_role("tab", name="全部记录", exact=True).click()
    status = page.get_by_role("status", name="正在读取审核记录…", exact=True)
    expect(status).to_be_visible()
    expect(status.locator('[aria-hidden="true"] [data-slot="skeleton"]').first).to_be_visible()
    finish_reads()
    filename = page.get_by_role("button", name="阅读 已固定原件.xlsx", exact=True)
    expect(filename).to_be_visible()
    expect(status).to_have_count(0)
    refresh = page.get_by_role("button", name="刷新审核记录", exact=True)
    refresh.click()
    expect(status).to_be_visible()
    expect(filename).to_be_visible()
    finish_reads(fail_round=True)
    alert = page.get_by_role("alert")
    expect(alert).to_contain_text("部分状态未能更新")
    expect(filename).to_be_visible()
    expect(status).to_have_count(0)
    alert.get_by_role("button", name="重新读取", exact=True).click()
    expect(status).to_be_visible()
    expect(filename).to_be_visible()
    finish_reads()
    expect(alert).to_have_count(0)
    expect(filename).to_be_visible()
    assert writes == []
