"""Keyboard, focus, touch size, and reduced-motion checks for the product UI."""

import os
import unittest
from urllib.parse import urlsplit

from .conftest import wait_until

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional until the e2e job installs Playwright
    sync_playwright = None

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")
RULE = "00000000-0000-4000-8000-000000000001"
SOURCE = "00000000-0000-4000-8000-000000000002"
VERSION = "00000000-0000-4000-8000-000000000003"
BODY = {
    "kind": "engine",
    "type": "控制计划",
    "rule_profile": "v3",
    "customers": [],
    "models": [],
    "parts": [],
    "rules": [{"id": "1", "category": "A", "priority": None, "rule_to_check": "检查 A", "type_of_rule": "table"}],
}  # noqa: E501


def focus_within(page, selector):
    """The focused element when it is, or sits inside, `selector`; retrying assertions read it until focus settles."""
    return page.locator(f"{selector}:focus, {selector} :focus")


@unittest.skipUnless(sync_playwright, "playwright not installed")
class UiA11yTests(unittest.TestCase):
    def test_keyboard_focus_and_motion(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", has_touch=True)
            page = context.new_page()
            page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))
            # Every read is controlled here; nothing reaches a server behind the dev proxy, whose 401 would sign the page out.
            page.route("**/api/**", lambda route: route.fulfill(status=404, json={"error": "unexpected_test_request"}))
            page.route("**/api/audit-customers/default", lambda route: route.fulfill(json={"customer_preference": None, "revision": 0}))
            page.route("**/api/audit-customers", lambda route: route.fulfill(json={"customers": []}))
            page.route(
                "**/api/public-config",
                lambda route: route.fulfill(
                    json={
                        "git_sha": "fixture",
                        "api_url": WEB + "/api",
                        "profile": "isolated-dev",
                        "login": "isolated-dev",
                        "feishu_app_id": "",
                        "current_access": "allowed",
                        "usage_charge_enabled": False,
                    }
                ),
            )
            page.route(
                "**/api/me",
                lambda route: route.fulfill(
                    json={
                        "display_name": "alice",
                        "tenant_id": "test-tenant",
                        "user_id": "test-user",
                        "membership": "member",
                        "identity": {"provider": "isolated-dev", "app_id": "test-app", "tenant_key": "test-tenant", "subject": "fixture"},
                    }
                ),
            )
            page.route("**/api/audit-tasks?*", lambda route: route.fulfill(json={"tasks": [], "next_offset": None}))
            page.route("**/api/rules", lambda route: route.fulfill(json={"rules": []}))
            session = context.new_cdp_session(page)
            session.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
            page.goto(WEB)
            # The home address opens PPAP Audit, the new-audit page.
            expect(page.get_by_role("heading", name="新建审核", level=1, exact=True)).to_be_visible()
            expect(page.locator("aside").get_by_role("navigation", name="产品导航", exact=True)).to_be_visible()
            page.keyboard.press("Tab")
            expect(page.locator(":focus")).to_contain_text("跳到主要内容")
            focused = page.evaluate(
                """() => {
                  const el = document.activeElement
                  const style = el ? getComputedStyle(el) : null
                  return {
                    text: el?.textContent,
                    outline: style?.outlineStyle,
                    width: style ? parseFloat(style.outlineWidth) : 0,
                    shadow: Boolean(style?.boxShadow && style.boxShadow !== 'none'),
                  }
                }"""
            )
            self.assertIn("跳到主要内容", focused["text"] or "")
            self.assertTrue(focused["shadow"] or (focused["outline"] != "none" and focused["width"] >= 1))
            page.keyboard.press("Enter")
            expect(page.locator("main:focus")).to_have_count(1)
            expect(page.get_by_role("heading", level=1)).to_have_count(1)
            directory = []
            page.route("**/api/rules", lambda route: directory.append(route))
            page.goto(WEB + "/requirements")
            expect(page.get_by_role("region", name="审核规则库", exact=True)).to_be_visible()
            loading = page.get_by_role("status", name="正在读取客户规则…", exact=True)
            expect(loading).to_be_visible()
            self.assertTrue(loading.evaluate("el => el.getAnimations({subtree: true}).every(animation => animation.playState !== 'running')"))
            expect(page.get_by_text("还没有客户规则", exact=True)).to_have_count(0)
            wait_until(page, lambda: directory, "The rule list read did not reach its route handler")
            while directory:
                directory.pop().fulfill(status=503, json={"error": "unavailable"})
            expect(loading).to_have_count(0)
            expect(page.get_by_role("alert")).to_contain_text("客户规则未能读取")
            page.get_by_role("button", name="重新读取", exact=True).click()
            expect(loading).to_be_visible()
            wait_until(page, lambda: directory, "The rule list was not read again")
            while directory:
                directory.pop().fulfill(json={"rules": []})
            expect(page.get_by_text("还没有客户规则", exact=True)).to_be_visible()
            expect(loading).to_have_count(0)
            context.close()
            browser.close()

    def test_a_denied_rule_list_is_not_an_empty_library(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_context().new_page()
            page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))
            page.route(
                "**/api/public-config",
                lambda route: route.fulfill(
                    json={
                        "git_sha": "fixture",
                        "api_url": WEB + "/api",
                        "profile": "isolated-dev",
                        "login": "isolated-dev",
                        "feishu_app_id": "",
                        "current_access": "allowed",
                        "usage_charge_enabled": False,
                    }
                ),
            )
            page.route(
                "**/api/me",
                lambda route: route.fulfill(
                    json={
                        "display_name": "alice",
                        "tenant_id": "test-tenant",
                        "user_id": "test-user",
                        "membership": "member",
                        "identity": {
                            "provider": "isolated-dev",
                            "app_id": "test-app",
                            "tenant_key": "test-tenant",
                            "subject": "fixture",
                        },
                    }
                ),
            )
            page.route("**/api/rules", lambda route: route.fulfill(json={"rules": []}))
            page.route("**/api/audit-customers", lambda route: route.fulfill(status=403, json={"error": "access_denied"}))
            page.goto(WEB + "/requirements")
            expect(page.get_by_role("alert")).to_contain_text("没有访问这份资料的权限。")
            expect(page.get_by_text("还没有客户规则", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="重新读取", exact=True)).to_be_visible()
            browser.close()

    def test_rule_detail_is_read_only_with_a_single_heading(self):
        from playwright.sync_api import expect

        rule = {
            "id": RULE,
            "title": "规则 A",
            "created_at": "2026-10-18T00:00:00Z",
            "body": BODY,
            "source_file_id": SOURCE,
            "source_file_version_id": VERSION,
            "predecessor_id": None,
        }
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_default_timeout(5000)
            cors = {"Access-Control-Allow-Origin": WEB, "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "content-type"}
            writes, unexpected = [], []

            def respond(route):
                request = route.request
                if request.method == "OPTIONS":
                    route.fulfill(status=204, headers=cors)
                    return
                if request.method != "GET":
                    writes.append(request.method)
                    route.fulfill(status=405, json={"error": "unexpected_test_write"}, headers=cors)
                    return
                path = urlsplit(request.url).path.removeprefix("/api")
                if path == "/public-config":
                    route.fulfill(
                        json={
                            "git_sha": "fixture",
                            "api_url": WEB + "/api",
                            "profile": "isolated-dev",
                            "login": "isolated-dev",
                            "feishu_app_id": "",
                            "current_access": "allowed",
                            "usage_charge_enabled": False,
                        },
                        headers=cors,
                    )
                elif path == "/me":
                    route.fulfill(
                        json={
                            "display_name": "alice",
                            "tenant_id": "test-tenant",
                            "user_id": "test-user",
                            "membership": "member",
                            "identity": {"provider": "isolated-dev", "app_id": "test-app", "tenant_key": "test-tenant", "subject": "fixture"},
                        },
                        headers=cors,
                    )
                elif path == "/audit-customers":
                    route.fulfill(json={"customers": [], "unregistered": []}, headers=cors)
                elif path == "/audit-customers/default":
                    route.fulfill(json={"customer_preference": None, "revision": 0}, headers=cors)
                elif path == "/audit-tasks":
                    route.fulfill(json={"tasks": [], "next_offset": None}, headers=cors)
                elif path == "/rules":
                    route.fulfill(
                        json={
                            "rules": [
                                {
                                    "id": RULE,
                                    "title": "规则 A",
                                    "kind": "engine",
                                    "created_at": rule["created_at"],
                                    "predecessor_id": None,
                                    "type": "控制计划",
                                    "rule_profile": "v3",
                                    "customers": [],
                                    "models": [],
                                    "parts": [],
                                }
                            ]
                        },
                        headers=cors,
                    )
                elif path == "/rules/" + RULE:
                    route.fulfill(json=rule, headers=cors)
                elif path == "/rule-questionnaires":
                    route.fulfill(json={"questionnaires": []}, headers=cors)
                else:
                    unexpected.append(path)
                    route.fulfill(status=404, json={"error": "unexpected_test_request"}, headers=cors)

            page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))
            page.route("**/api/**", respond)
            page.goto(WEB + "/requirements?systemRule=" + RULE)
            expect(page.get_by_role("heading", name="规则 A", exact=True, level=1)).to_be_visible()
            expect(page.get_by_role("heading", level=1)).to_have_count(1)
            expect(page.get_by_role("article", name="第 1 条", exact=True)).to_contain_text("检查 A")
            # Rules are delivered by the platform: the page offers nothing to upload, edit or publish.
            expect(page.get_by_role("main").get_by_role("textbox")).to_have_count(0)
            for name in ("上传规则", "创建新版本", "保存版本", "放弃草稿", "采用"):
                expect(page.get_by_role("button", name=name, exact=True)).to_have_count(0)
            self.assertEqual(writes, [])
            self.assertEqual(unexpected, [])
            browser.close()
