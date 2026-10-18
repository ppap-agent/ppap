"""Keyboard, focus, touch size, and reduced-motion checks for the product UI."""

import os
import unittest
from urllib.parse import urlsplit

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional until the e2e job installs Playwright
    sync_playwright = None

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")
JOB = "00000000-0000-4000-8000-000000000001"
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
            page.route("**/api/public-config", lambda route: route.fulfill(json={"api_url": WEB + "/api", "profile": "isolated-dev"}))
            page.route("**/api/me", lambda route: route.fulfill(status=401, json={"error": "unauthenticated"}))
            session = context.new_cdp_session(page)
            session.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
            page.goto(f"{WEB}/dev")
            page.wait_for_selector('[data-testid="isolated-dev-login"]')
            expect(page.get_by_test_id("pc-shell")).to_be_visible()
            expect(page.locator(".mobile-tab-bar")).to_be_hidden()
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
            media = page.evaluate("() => ({pointer: matchMedia('(pointer: coarse)').matches, motion: matchMedia('(prefers-reduced-motion: reduce)').matches})")
            self.assertTrue(media["pointer"])
            self.assertTrue(media["motion"])
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.locator(".mobile-tab-bar")).to_be_visible()
            box = page.locator(".mobile-tab-bar a").first.bounding_box()
            self.assertIsNotNone(box)
            self.assertGreaterEqual(box["height"], 44)
            self.assertEqual(page.locator("h1").count(), 1)
            context.close()
            browser.close()

    def test_rule_editor_keyboard_modal_and_single_heading(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            cors = {"Access-Control-Allow-Origin": WEB, "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "content-type"}

            def respond(route):
                request = route.request
                if request.method == "OPTIONS":
                    route.fulfill(status=204, headers=cors)
                    return
                path = urlsplit(request.url).path.removeprefix("/api")
                if path == "/public-config":
                    route.fulfill(json={"api_url": WEB + "/api", "profile": "isolated-dev"}, headers=cors)
                elif path == "/me":
                    route.fulfill(json={"display_name": "alice", "identity": {"subject": "fixture"}}, headers=cors)
                elif path == "/rule-types":
                    route.fulfill(json={"types": ["控制计划"]}, headers=cors)
                elif path == "/rules":
                    route.fulfill(json={"rules": []}, headers=cors)
                elif path.startswith("/rules/parse/"):
                    route.fulfill(
                        json={
                            "id": JOB,
                            "status": "succeeded",
                            "source_file_id": SOURCE,
                            "source_file_version_id": VERSION,
                            "document_type": "控制计划",
                            "result": {"title": "规则 A", "body": BODY, "source_file_version_id": VERSION},
                        },
                        headers=cors,
                    )  # noqa: E501
                else:
                    route.fulfill(status=404, json={"error": "unexpected_test_request"}, headers=cors)

            page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))
            page.route("**/api/**", respond)
            page.goto(WEB + "/rules?parse=" + JOB)
            expect(page.get_by_test_id("rule-title")).to_have_value("规则 A")
            self.assertEqual(page.locator("h1").count(), 1)
            page.get_by_test_id("rule-title").focus()
            self.assertEqual(page.evaluate("() => document.activeElement?.id"), "rule-title")
            page.keyboard.press("Tab")
            moved = page.evaluate(
                """() => {
                  const active = document.activeElement
                  return Boolean(active && active.id !== 'rule-title' && active.closest('#main-content, main'))
                }"""
            )
            self.assertTrue(moved)
            page.get_by_test_id("rule-back").click()
            expect(page.get_by_role("dialog")).to_be_visible()
            inside = page.evaluate("() => Boolean(document.activeElement?.closest('[role=\"dialog\"]'))")
            self.assertTrue(inside)
            self.assertEqual(page.locator("h1").count(), 1)
            browser.close()
