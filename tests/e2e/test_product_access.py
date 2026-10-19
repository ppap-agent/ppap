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
        yield page
        context.close()
        browser.close()


def login_config(page, login="feishu"):
    page.route("**/api/public-config", lambda route: route.fulfill(json={"api_url": WEB + "/api", "profile": "isolated-dev", "login": login, "feishu_app_id": "test-app"}))


def test_disabled_login_has_no_dead_authorization_link_and_can_recheck_configuration(page):
    login_config(page, "disabled")
    page.route("**/api/me", lambda route: route.fulfill(status=401, json={"error": "unauthenticated"}))
    page.goto(WEB + "/?case=access-test&stage=decision")
    expect(page.get_by_text("当前无法使用飞书登录，请稍后重试。")).to_be_visible()
    expect(page.get_by_role("link", name="使用飞书登录")).to_have_count(0)
    login_config(page)
    page.get_by_role("button", name="重新读取登录状态").click()
    link = page.get_by_role("link", name="使用飞书登录")
    expect(link).to_be_visible()
    address = urlsplit(link.get_attribute("href"))
    assert address.path == "/api/login/feishu"
    assert parse_qs(address.query)["return_to"] == [WEB + "/?case=access-test&stage=decision"]


def test_initial_anonymous_probe_does_not_discard_a_successful_host_login(page):
    login_config(page)
    page.route("**/api/me", lambda route: route.fulfill(status=401, json={"error": "unauthenticated"}))
    page.add_init_script("window.__PPAP_FEISHU_HOST__ = {login: async () => 'controlled-host-code'}")
    codes = []

    def host_login(route):
        codes.append(route.request.post_data_json)
        route.fulfill(json=PRINCIPAL)

    page.route("**/api/login/feishu/host", host_login)
    page.goto(WEB + "/?view=workbench")
    expect(page.get_by_role("button", name="登录恢复测试，账号菜单")).to_be_visible()
    expect(page.get_by_role("heading", name="登录审核工作台")).to_have_count(0)
    assert codes == [{"code": "controlled-host-code"}]


def test_disabled_new_login_does_not_revoke_an_existing_authenticated_session(page):
    login_config(page, "disabled")
    page.route("**/api/me", lambda route: route.fulfill(json=PRINCIPAL))
    page.goto(WEB + "/?view=workbench")
    expect(page.get_by_role("button", name="登录恢复测试，账号菜单")).to_be_visible()
    expect(page.get_by_role("link", name="使用飞书登录")).to_have_count(0)


def test_a_business_401_still_hides_the_authenticated_product_without_restarting_host_login(page):
    login_config(page)
    page.route("**/api/me", lambda route: route.fulfill(json=PRINCIPAL))
    page.route("**/api/drafts?*", lambda route: route.fulfill(json={"drafts": [], "next_offset": None}))
    page.route("**/api/rounds?*", lambda route: route.fulfill(json={"rounds": [], "next_offset": None}))
    page.add_init_script("window.__PPAP_FEISHU_HOST__ = {login: async () => 'must-not-request'}")
    host_posts = []
    page.route("**/api/login/feishu/host", lambda route: (host_posts.append(route.request.url), route.fulfill(json=PRINCIPAL)))
    page.goto(WEB + "/?view=workbench")
    expect(page.get_by_role("button", name="登录恢复测试，账号菜单")).to_be_visible()
    refresh = page.get_by_role("button", name="刷新审核记录")
    expect(refresh).to_be_enabled()
    before = page.url
    page.route("**/api/drafts?*", lambda route: route.fulfill(status=401, json={"error": "unauthenticated"}))
    refresh.click()
    expect(page.get_by_role("heading", name="登录审核工作台")).to_be_visible()
    expect(page.get_by_role("button", name="登录恢复测试，账号菜单")).to_have_count(0)
    expect(page.get_by_role("link", name="使用飞书登录")).to_be_visible()
    assert page.url == before
    assert host_posts == []


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("cancelled", "已取消飞书授权。需要登录后才能继续审核。"),
        ("failed", "飞书登录未完成，请重新登录。"),
        ("tenant_not_allowed", "当前企业尚未开通此审核服务，请切换飞书账号或联系管理员。"),
        ("untrusted-provider-text", "飞书登录未完成，请重新登录。"),
        ("__proto__", "飞书登录未完成，请重新登录。"),
    ],
)
@pytest.mark.parametrize("path", ["/?view=workbench", "/audit?report=frozen-report"])
def test_authorization_failure_keeps_task_and_requires_an_explicit_new_login(page, reason, message, path):
    login_config(page)
    page.route("**/api/me", lambda route: route.fulfill(status=401, json={"error": "unauthenticated"}))
    page.add_init_script("window.__PPAP_FEISHU_HOST__ = {login: async () => 'must-not-request'}")
    host_posts = []
    page.route("**/api/login/feishu/host", lambda route: (host_posts.append(route.request.url), route.fulfill(json=PRINCIPAL)))
    page.goto(WEB + path + "&task=original-task&auth_error=" + reason + "#original-clause")
    expect(page.get_by_role("alert")).to_contain_text(message)
    assert page.url == WEB + path + "&task=original-task#original-clause"
    link = page.get_by_role("link", name="使用飞书登录")
    expect(link).to_be_visible()
    assert parse_qs(urlsplit(link.get_attribute("href")).query)["return_to"] == [page.url]
    page.get_by_role("button", name="重新读取登录状态").click()
    expect(page.get_by_role("alert")).to_contain_text(message)
    assert host_posts == []


@pytest.mark.parametrize("viewport", [(1280, 720), (390, 844)])
def test_native_account_usage_and_logout_keep_the_task_report_address(page, viewport):
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    login_config(page)
    page.route("**/api/me", lambda route: route.fulfill(json=PRINCIPAL))
    page.route("**/api/audit-template-catalog", lambda route: route.fulfill(json={"coverage_universe": [str(index) for index in range(17)], "customers": []}))
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
    expect(page.get_by_role("menuitem", name="本机草稿")).to_have_count(0)
    page.get_by_role("menuitem", name="用量记录").click()
    expect(page.get_by_role("heading", name="用量记录", exact=True)).to_be_visible()
    address = urlsplit(page.url)
    assert address.path == "/audit"
    assert parse_qs(address.query) == {"task": ["original-task"], "report": ["frozen-report"], "view": ["usage"]}
    assert address.fragment == "original-clause"
    if viewport[0] < 1024:
        page.get_by_role("button", name="打开产品导航").click()
    page.get_by_role("button", name="PPAP Audit", exact=True).click()
    assert page.url == WEB + "/audit?task=original-task&report=frozen-report#original-clause"
    account.click()
    page.get_by_role("menuitem", name="退出登录").click()
    expect(page.get_by_role("heading", name="登录审核工作台")).to_be_visible()
    assert logged_out == ["POST"]
    expect(account).to_have_count(0)
    link = page.get_by_role("link", name="使用飞书登录")
    assert parse_qs(urlsplit(link.get_attribute("href")).query)["return_to"] == [page.url]
    assert page.url == WEB + "/audit?task=original-task&report=frozen-report#original-clause"


def test_native_templates_can_read_provider_permissions_without_unverified_management(page):
    login_config(page)
    page.route("**/api/me", lambda route: route.fulfill(json=PRINCIPAL))
    page.route("**/api/audit-tasks?*", lambda route: route.fulfill(json={"tasks": [], "next_offset": None}))
    page.route(
        "**/api/audit-template-catalog",
        lambda route: route.fulfill(json={"coverage_universe": [str(index) for index in range(17)], "customers": [{"customer_id": "customer-a", "name": "客户 A", "effective_rule_types": ["1"]}]}),
    )
    page.route(
        "**/api/audit-templates?*",
        lambda route: route.fulfill(
            json={
                "query": {"customer_id": "customer-a", "search": "", "page": 1, "page_size": 10},
                "folder_id": "bound-folder",
                "folder_revision": "frozen-revision",
                "folder_template_count": 0,
                "creator_name": "创建人",
                "manage_access": {"state": "unknown", "reason": "管理权限未确认"},
                "zip_access": {"state": "allowed", "reason": None},
                "items": [],
                "total": 0,
            }
        ),
    )
    reads = []

    def permissions(route):
        query = parse_qs(urlsplit(route.request.url).query, keep_blank_values=True)
        assert route.request.method == "GET"
        assert query["customer_id"] == ["customer-a"] and query["folder_id"] == ["bound-folder"]
        reads.append(query["query"][0])
        route.fulfill(
            json={
                "folder": {"customer_id": "customer-a", "folder_id": "bound-folder"},
                "permission_revision": "permissions-one",
                "query": query["query"][0],
                "creator_name": "创建人",
                "manage_access": {"state": "unknown", "reason": "当前管理权限尚未确认。"},
                "members": [{"target": {"kind": "user", "id": "observed-user"}, "display_name": "实际协作者", "role": "可管理", "source": "unknown"}],
                "actions": [],
            }
        )

    page.route("**/api/audit-template-permissions?*", permissions)
    page.goto(WEB + "/audit?view=templates&task=original-task")
    page.get_by_role("button", name="查看文件夹权限", exact=True).click()
    dialog = page.get_by_role("dialog", name="文件夹权限", exact=True)
    expect(dialog.get_by_role("listitem", name="协作者 实际协作者")).to_contain_text("可管理")
    expect(dialog.get_by_text("来源待核对", exact=True)).to_be_visible()
    expect(dialog.get_by_text("继承权限", exact=True)).to_have_count(0)
    expect(dialog.get_by_role("combobox")).to_have_count(0)
    assert reads == [""]
    dialog.get_by_role("textbox", name="搜索协作者").fill("实际")
    with page.expect_response("**/api/audit-template-permissions?*"):
        dialog.get_by_role("button", name="搜索", exact=True).click()
    expect(dialog.get_by_role("button", name="重新读取当前权限")).to_be_enabled()
    assert reads == ["", "实际"]
    dialog.get_by_role("button", name="关闭", exact=True).click()
    expect(dialog).to_have_count(0)
    assert parse_qs(urlsplit(page.url).query) == {"view": ["templates"], "task": ["original-task"]}


def test_native_permission_change_recovers_the_original_request_after_page_reload(page):
    login_config(page)
    page.route("**/api/me", lambda route: route.fulfill(json=PRINCIPAL))
    page.route("**/api/audit-tasks?*", lambda route: route.fulfill(json={"tasks": [], "next_offset": None}))
    page.route(
        "**/api/audit-template-catalog",
        lambda route: route.fulfill(json={"coverage_universe": [str(index) for index in range(17)], "customers": [{"customer_id": "customer-a", "name": "客户 A", "effective_rule_types": ["1"]}]}),
    )
    page.route(
        "**/api/audit-templates?*",
        lambda route: route.fulfill(
            json={
                "query": {"customer_id": "customer-a", "search": "", "page": 1, "page_size": 10},
                "folder_id": "bound-folder",
                "folder_revision": "frozen-revision",
                "folder_template_count": 0,
                "creator_name": None,
                "manage_access": {"state": "allowed", "reason": None},
                "zip_access": {"state": "allowed", "reason": None},
                "items": [],
                "total": 0,
            }
        ),
    )
    action = {"id": "change-reader", "target": {"kind": "user", "id": "reader"}, "target_name": "协作者", "before": "可阅读", "after": "可编辑", "label": "设为可编辑"}
    calls, receipts = [], []

    def permissions(route):
        route.fulfill(
            json={
                "folder": {"customer_id": "customer-a", "folder_id": "bound-folder"},
                "permission_revision": "acl-current" if receipts else "acl-original",
                "query": "",
                "creator_name": None,
                "manage_access": {"state": "unknown" if receipts else "allowed", "reason": None},
                "members": [{"target": action["target"], "display_name": "协作者", "role": "可编辑" if receipts else "可阅读", "source": "direct"}],
                "actions": [] if receipts else [action],
                "change": receipts[0] if receipts else None,
            }
        )

    def apply(route):
        calls.append(route.request.method)
        receipts.append({"request": route.request.post_data_json, "state": "unknown", "result_revision": None, "reason": "未取得原修改回执。"})
        route.fulfill(json=receipts[0])

    def read(route):
        calls.append(route.request.method)
        assert route.request.url.endswith("/" + receipts[0]["request"]["request_id"])
        route.fulfill(json=receipts[0])

    page.route("**/api/audit-template-permissions?*", permissions)
    page.route("**/api/audit-template-permission-changes", apply)
    page.route("**/api/audit-template-permission-changes/*", read)
    page.goto(WEB + "/audit?view=templates&task=original-task")
    page.get_by_role("button", name="管理文件夹权限", exact=True).click()
    dialog = page.get_by_role("dialog", name="文件夹权限", exact=True)
    dialog.get_by_role("combobox", name="修改 协作者 的权限", exact=True).click()
    page.get_by_role("option", name="设为可编辑", exact=True).click()
    page.get_by_role("dialog", name="确认权限修改", exact=True).get_by_role("button", name="确认这项修改", exact=True).click()
    expect(dialog.get_by_role("status", name="权限变更结果")).to_contain_text("修改结果待确认")
    assert calls == ["POST"]
    page.reload()
    page.get_by_role("button", name="管理文件夹权限", exact=True).click()
    expect(dialog.get_by_role("status", name="权限变更结果")).to_contain_text("修改结果待确认")
    expect(dialog.get_by_role("combobox")).to_have_count(0)
    dialog.get_by_role("button", name="读取原修改回执", exact=True).click()
    expect(dialog.get_by_role("status", name="权限变更结果")).to_contain_text("修改结果待确认")
    assert calls == ["POST", "GET"]
