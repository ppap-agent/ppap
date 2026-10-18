"""Keyboard, focus, touch size, and reduced-motion checks for the product UI."""

import hashlib
import json
import os
import unittest
from urllib.parse import urlsplit

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


@unittest.skipUnless(sync_playwright, "playwright not installed")
class UiA11yTests(unittest.TestCase):
    def test_keyboard_focus_motion_and_viewports(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", has_touch=True)
            page = context.new_page()
            page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))
            page.route(
                "**/api/public-config",
                lambda route: route.fulfill(
                    json={
                        "git_sha": "fixture",
                        "api_url": WEB + "/api",
                        "profile": "isolated-dev",
                        "login": "isolated-dev",
                        "deep_offered": False,
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
            page.route("**/api/drafts?*", lambda route: route.fulfill(json={"drafts": [], "next_offset": None}))
            page.route("**/api/rounds?*", lambda route: route.fulfill(json={"rounds": [], "next_offset": None}))
            page.route("**/api/rules", lambda route: route.fulfill(json={"rules": []}))
            page.route("**/api/rule-types", lambda route: route.fulfill(json={"types": ["控制计划"]}))
            session = context.new_cdp_session(page)
            session.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
            page.goto(WEB)
            expect(page.get_by_role("heading", name="工作台", exact=True)).to_be_visible()
            expect(page.locator("aside").get_by_role("navigation", name="主要导航", exact=True)).to_be_visible()
            page.keyboard.press("Tab")
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
            self.assertTrue(page.evaluate("() => document.activeElement?.tagName === 'MAIN'"))
            media = page.evaluate("() => ({pointer: matchMedia('(pointer: coarse)').matches, motion: matchMedia('(prefers-reduced-motion: reduce)').matches})")
            self.assertTrue(media["pointer"])
            self.assertTrue(media["motion"])
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.locator("aside")).to_be_hidden()
            navigation = page.get_by_role("navigation", name="主要导航", exact=True)
            expect(navigation).to_be_visible()
            box = navigation.get_by_role("link", name="工作台", exact=True).bounding_box()
            self.assertIsNotNone(box)
            self.assertGreaterEqual(box["height"], 44)
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            self.assertEqual(page.locator("h1").count(), 1)
            context.close()
            browser.close()

    def test_rule_editor_keyboard_modal_and_single_heading(self):
        from playwright.sync_api import expect

        source_bytes = json.dumps(BODY).encode()
        rule = {
            "id": RULE,
            "title": "规则 A",
            "created_at": "2026-10-18T00:00:00Z",
            "body": BODY,
            "source_file_id": SOURCE,
            "source_file_version_id": VERSION,
            "predecessor_id": None,
        }
        source = {
            "file_id": SOURCE,
            "id": VERSION,
            "original_filename": "控制计划要求.json",
            "status": "ready",
            "content_type": "application/json",
            "byte_size": len(source_bytes),
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
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
                            "deep_offered": False,
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
                elif path == "/rule-types":
                    route.fulfill(json={"types": ["控制计划"]}, headers=cors)
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
                elif path == f"/files/{SOURCE}/versions/{VERSION}":
                    route.fulfill(json=source, headers=cors)
                elif path == "/drafts":
                    route.fulfill(json={"drafts": [], "next_offset": None}, headers=cors)
                elif path == "/rounds":
                    route.fulfill(json={"rounds": [], "next_offset": None}, headers=cors)
                else:
                    unexpected.append(path)
                    route.fulfill(status=404, json={"error": "unexpected_test_request"}, headers=cors)

            page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))
            page.route("**/api/**", respond)
            page.goto(WEB + "/?view=requirements&requirement=" + RULE)
            expect(page.get_by_role("heading", name="规则 A", exact=True)).to_be_visible()
            page.get_by_role("button", name="创建新版本", exact=True).click()
            title = page.get_by_role("textbox", name="要求名称", exact=True)
            expect(title).to_have_value("规则 A")
            self.assertEqual(page.locator("h1").count(), 1)
            title.focus()
            expect(title).to_be_focused()
            page.keyboard.press("Tab")
            expect(title).not_to_be_focused()
            self.assertTrue(page.evaluate("() => Boolean(document.activeElement?.closest('main'))"))
            original = page.get_by_role("button", name="核对出处", exact=True)
            original.focus()
            page.keyboard.press("Enter")
            sheet = page.get_by_role("dialog", name="条款 1 · 核对出处", exact=True)
            expect(sheet).to_be_visible()
            self.assertTrue(page.evaluate("() => Boolean(document.activeElement?.closest('[role=dialog]'))"))
            page.keyboard.press("Escape")
            expect(sheet).to_have_count(0)
            expect(original).to_be_focused()
            discard = page.get_by_role("button", name="放弃草稿", exact=True)
            discard.focus()
            page.keyboard.press("Space")
            dialog = page.get_by_role("dialog", name="放弃这个版本草稿？", exact=True)
            expect(dialog).to_be_visible()
            for _ in range(8):
                page.keyboard.press("Tab")
                self.assertTrue(page.evaluate("() => Boolean(document.activeElement?.closest('[role=dialog]'))"))
            for _ in range(4):
                page.keyboard.press("Shift+Tab")
                self.assertTrue(page.evaluate("() => Boolean(document.activeElement?.closest('[role=dialog]'))"))
            self.assertEqual(page.locator("h1").count(), 1)
            page.keyboard.press("Escape")
            expect(dialog).to_have_count(0)
            expect(discard).to_be_focused()
            expect(title).to_have_value("规则 A")
            page.keyboard.press("Enter")
            expect(dialog).to_be_visible()
            dialog.get_by_role("button", name="继续编辑", exact=True).focus()
            page.keyboard.press("Enter")
            expect(dialog).to_have_count(0)
            expect(discard).to_be_focused()
            expect(title).to_have_value("规则 A")
            self.assertEqual(writes, [])
            self.assertEqual(unexpected, [])
            browser.close()
