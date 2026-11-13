"""Controlled login interleavings through the product UI and HTTP boundary."""

import os
from urllib.parse import parse_qs, urlsplit

import pytest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    expect = sync_playwright = None

pytestmark = pytest.mark.skipif(sync_playwright is None, reason="requires the e2e dependency group")

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")
PRINCIPAL = {
    "user_id": "access-test-user",
    "tenant_id": "access-test-tenant",
    "membership": "member",
    "display_name": "登录恢复测试",
    "identity": {"provider": "feishu", "app_id": "test-app", "tenant_key": "test-tenant", "subject": "test-user"},
}


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(user_agent="Feishu/7.0 PPAP-access-test")
        page = context.new_page()
        page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))
        page.route("**/api/**", lambda route: route.fulfill(status=404, json={"error": "unexpected_test_request"}))
        page.route("**/api/audit-customers/default", lambda route: route.fulfill(json={"customer_preference": None, "revision": 0}))
        yield page
        context.close()
        browser.close()


def login_config(page, login="feishu"):
    page.route("**/api/public-config", lambda route: route.fulfill(json={"api_url": WEB + "/api", "profile": "isolated-dev", "login": login, "feishu_app_id": "test-app"}))


def test_customer_catalog_refresh_keeps_the_restored_task_and_file_selection(page):
    login_config(page)
    page.route("**/api/me", lambda route: route.fulfill(json=PRINCIPAL))
    page.route("**/api/audit-tasks?*", lambda route: route.fulfill(json={"tasks": [], "next_offset": None}))
    task = {
        "id": "original-task",
        "number": "PPAP-20261019-001",
        "state": "pending_confirm",
        "created_at": "2026-10-19T00:00:00Z",
        "updated_at": "2026-10-19T00:00:00Z",
        "source_draft_id": "original-task",
        "round_id": None,
        "customer_preference": None,
        "completion_reason": None,
        "estimated_points": 0,
        "input_revision": 0,
        "files": [
            {
                "file": {
                    "id": "version-one",
                    "file_id": "file-one",
                    "original_filename": "控制计划.xlsx",
                    "byte_size": 32,
                    "content_type": "application/octet-stream",
                    "status": "ready",
                    "sha256": None,
                },
                "selection": "selected",
                "removal_reason": None,
                "validation_id": "validation-one",
                "validation": {"state": "valid", "reasons": []},
                "match": {
                    "state": "unmatched",
                    "metadata": {"customer": None, "model": None, "part": None, "part_number": None},
                    "document_type": "控制计划",
                    "rule": None,
                    "gaps": ["matching_customer_required"],
                },
            }
        ],
    }
    page.route("**/api/audit-tasks/original-task", lambda route: route.fulfill(json=task))
    reads = []

    def catalog(route):
        reads.append(route.request.method)
        customers = [] if len(reads) == 1 else [{"customer_id": "customer-new", "name": "新客户"}]
        route.fulfill(json={"customers": customers})

    page.route("**/api/audit-customers", catalog)
    page.goto(WEB + "/audit?task=original-task")
    page.get_by_role("button", name="返回核对客户", exact=True).click()
    assert reads == []
    customer_picker = page.get_by_role("combobox", name="审核客户", exact=True)
    with page.expect_response(lambda response: response.url.endswith("/api/audit-customers")):
        customer_picker.click()
    expect(page.get_by_role("option", name="新客户", exact=True)).to_have_count(0)
    assert reads == ["GET"]
    page.keyboard.press("Escape")
    with page.expect_response(lambda response: response.url.endswith("/api/audit-customers")):
        customer_picker.click()
    page.get_by_role("option", name="新客户", exact=True).click()
    page.get_by_role("button", name="查看已选文件", exact=True).click()
    expect(page.get_by_role("checkbox", name="选择 控制计划.xlsx", exact=True)).to_be_checked()
    expect(page.get_by_role("heading", name="核对本次文件", exact=True)).to_be_visible()
    assert page.url == WEB + "/tasks/original-task"
    assert reads == ["GET", "GET"]

    expect(page.get_by_role("button", name="模板库", exact=True)).to_be_visible()


def test_disabled_login_has_no_dead_authorization_link_and_can_recheck_configuration(page):
    login_config(page, "disabled")
    page.route("**/api/me", lambda route: route.fulfill(status=401, json={"error": "unauthenticated"}))
    page.goto(WEB + "/files?archiveDoc=access-test")
    expect(page.get_by_text("当前无法使用飞书登录，请稍后重试。")).to_be_visible()
    expect(page.get_by_role("link", name="使用飞书登录")).to_have_count(0)
    login_config(page)
    page.get_by_role("button", name="重试").click()
    link = page.get_by_role("link", name="使用飞书登录")
    expect(link).to_be_visible()
    address = urlsplit(link.get_attribute("href"))
    assert address.path == "/api/login/feishu"
    assert parse_qs(address.query)["return_to"] == [WEB + "/files?archiveDoc=access-test"]


def test_initial_anonymous_probe_does_not_discard_a_successful_host_login(page):
    login_config(page)
    page.route("**/api/me", lambda route: route.fulfill(status=401, json={"error": "unauthenticated"}))
    page.add_init_script("window.__PPAP_FEISHU_HOST__ = {login: async () => 'controlled-host-code'}")
    codes = []

    def host_login(route):
        codes.append(route.request.post_data_json)
        route.fulfill(json=PRINCIPAL)

    page.route("**/api/login/feishu/host", host_login)
    page.goto(WEB + "/tasks/new")
    expect(page.get_by_role("button", name="登录恢复测试，账号菜单")).to_be_visible()
    expect(page.get_by_role("heading", name="登录 PPAP Audit")).to_have_count(0)
    assert codes == [{"code": "controlled-host-code"}]


def test_disabled_new_login_does_not_revoke_an_existing_authenticated_session(page):
    login_config(page, "disabled")
    page.route("**/api/me", lambda route: route.fulfill(json=PRINCIPAL))
    page.goto(WEB + "/tasks/new")
    expect(page.get_by_role("button", name="登录恢复测试，账号菜单")).to_be_visible()
    expect(page.get_by_role("link", name="使用飞书登录")).to_have_count(0)


def test_a_business_401_still_hides_the_authenticated_product_without_restarting_host_login(page):
    login_config(page)
    page.route("**/api/me", lambda route: route.fulfill(json=PRINCIPAL))
    # A further page of tasks gives the task rail an explicit business read to start.
    page.route("**/api/audit-tasks?*", lambda route: route.fulfill(json={"tasks": [], "next_offset": 50}))
    page.add_init_script("window.__PPAP_FEISHU_HOST__ = {login: async () => 'must-not-request'}")
    host_posts = []
    page.route("**/api/login/feishu/host", lambda route: (host_posts.append(route.request.url), route.fulfill(json=PRINCIPAL)))
    page.goto(WEB + "/tasks/new")
    expect(page.get_by_role("button", name="登录恢复测试，账号菜单")).to_be_visible()
    more = page.get_by_role("navigation", name="审核任务列表", exact=True).get_by_role("button", name="加载更多任务", exact=True)
    expect(more).to_be_enabled()
    before = page.url
    page.route("**/api/audit-tasks?*", lambda route: route.fulfill(status=401, json={"error": "unauthenticated"}))
    more.click()
    expect(page.get_by_role("heading", name="登录 PPAP Audit")).to_be_visible()
    expect(page.get_by_role("button", name="登录恢复测试，账号菜单")).to_have_count(0)
    expect(page.get_by_role("link", name="使用飞书登录")).to_be_visible()
    assert page.url == before
    assert host_posts == []


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("tenant_not_allowed", "当前企业尚未开通此审核服务，请切换飞书账号或联系管理员。"),
        ("untrusted-provider-text", "飞书登录未完成，请重新登录。"),
    ],
)
@pytest.mark.parametrize(
    "path,canonical",
    [("/audit?report=frozen-report", "/tasks/original-task?report=frozen-report#original-clause")],
)
def test_authorization_failure_keeps_task_and_requires_an_explicit_new_login(page, reason, message, path, canonical):
    login_config(page)
    page.route("**/api/me", lambda route: route.fulfill(status=401, json={"error": "unauthenticated"}))
    page.add_init_script("window.__PPAP_FEISHU_HOST__ = {login: async () => 'must-not-request'}")
    host_posts = []
    page.route("**/api/login/feishu/host", lambda route: (host_posts.append(route.request.url), route.fulfill(json=PRINCIPAL)))
    page.goto(WEB + path + "&task=original-task&auth_error=" + reason + "#original-clause")
    expect(page.get_by_role("alert")).to_contain_text(message)
    assert page.url == WEB + canonical
    link = page.get_by_role("link", name="使用飞书登录")
    expect(link).to_be_visible()
    assert parse_qs(urlsplit(link.get_attribute("href")).query)["return_to"] == [page.url]
    page.get_by_role("button", name="重试").click()
    expect(page.get_by_role("alert")).to_contain_text(message)
    assert host_posts == []


def test_native_account_usage_and_logout_keep_the_task_report_address(page):
    page.set_viewport_size({"width": 1280, "height": 720})
    login_config(page)
    page.route("**/api/me", lambda route: route.fulfill(json=PRINCIPAL))
    page.route("**/api/audit-customers", lambda route: route.fulfill(json={"customers": []}))
    page.route("**/api/audit-tasks?*", lambda route: route.fulfill(json={"tasks": [], "next_offset": None}))
    logged_out = []

    def logout(route):
        logged_out.append(route.request.method)
        route.fulfill(json={"ok": True})

    page.route("**/api/logout", logout)
    page.goto(WEB + "/audit?task=original-task&report=frozen-report#original-clause")
    account = page.get_by_role("button", name="登录恢复测试，账号菜单")
    expect(account).to_be_visible()
    account.click()
    page.get_by_role("menuitem", name="本机草稿", exact=True).click()
    records = page.get_by_role("dialog", name="本机记录", exact=True)
    expect(records).to_be_visible()
    page.keyboard.press("Escape")
    expect(records).to_have_count(0)
    assert page.url == WEB + "/tasks/original-task?report=frozen-report#original-clause"
    account.click()
    page.get_by_role("menuitem", name="用量记录").click()
    expect(page.get_by_role("heading", name="用量记录", exact=True)).to_be_visible()
    address = urlsplit(page.url)
    assert address.path == "/usage"
    assert parse_qs(address.query) == {}
    assert address.fragment == "original-clause"
    page.get_by_role("button", name="PPAP Audit", exact=True).click()
    assert page.url == WEB + "/tasks/original-task?report=frozen-report#original-clause"
    account.click()
    page.get_by_role("menuitem", name="退出登录").click()
    expect(page.get_by_role("heading", name="登录 PPAP Audit")).to_be_visible()
    assert logged_out == ["POST"]
    expect(account).to_have_count(0)
    link = page.get_by_role("link", name="使用飞书登录")
    assert parse_qs(urlsplit(link.get_attribute("href")).query)["return_to"] == [page.url]
    assert page.url == WEB + "/tasks/original-task?report=frozen-report#original-clause"
