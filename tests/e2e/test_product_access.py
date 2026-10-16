"""Controlled login interleavings through the product UI and HTTP boundary."""

import os
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

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
def test_authorization_failure_keeps_task_and_requires_an_explicit_new_login(page, reason, message):
    login_config(page)
    page.route("**/api/me", lambda route: route.fulfill(status=401, json={"error": "unauthenticated"}))
    page.add_init_script("window.__PPAP_FEISHU_HOST__ = {login: async () => 'must-not-request'}")
    host_posts = []
    page.route("**/api/login/feishu/host", lambda route: (host_posts.append(route.request.url), route.fulfill(json=PRINCIPAL)))
    page.goto(WEB + "/?view=workbench&task=original-task&auth_error=" + reason + "#original-clause")
    expect(page.get_by_role("alert")).to_contain_text(message)
    assert page.url == WEB + "/?view=workbench&task=original-task#original-clause"
    link = page.get_by_role("link", name="使用飞书登录")
    expect(link).to_be_visible()
    assert parse_qs(urlsplit(link.get_attribute("href")).query)["return_to"] == [page.url]
    page.get_by_role("button", name="重新读取登录状态").click()
    expect(page.get_by_role("alert")).to_contain_text(message)
    assert host_posts == []
