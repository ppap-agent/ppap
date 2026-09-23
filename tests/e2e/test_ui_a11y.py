"""Keyboard, focus, touch size, and reduced-motion checks for the product UI."""

import unittest

from tests.e2e.test_rule_interactions import BODY, JOB, SOURCE, VERSION
from tests.e2e.test_web_audit import WEB, sync_playwright


@unittest.skipUnless(sync_playwright, "playwright not installed")
class UiA11yTests(unittest.TestCase):
    def test_keyboard_focus_motion_and_viewports(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce", has_touch=True)
            page = context.new_page()
            session = context.new_cdp_session(page)
            session.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
            page.goto(f"{WEB}/dev")
            page.wait_for_selector('[data-testid="isolated-dev-login"]')
            expect(page.locator('[data-ui="sidebar"]')).to_be_visible()
            expect(page.locator('[data-ui="mobile-bar"]')).to_be_hidden()
            page.keyboard.press("Tab")
            focused = page.evaluate(
                """() => {
                  const el = document.activeElement
                  const style = getComputedStyle(el)
                  return {ui: el?.getAttribute('data-ui'), outline: style.outlineStyle, width: parseFloat(style.outlineWidth)}
                }"""
            )
            self.assertEqual(focused["ui"], "skip")
            self.assertEqual(focused["outline"], "solid")
            self.assertGreaterEqual(focused["width"], 2)
            media = page.evaluate("() => ({pointer: matchMedia('(pointer: coarse)').matches, motion: matchMedia('(prefers-reduced-motion: reduce)').matches})")
            self.assertTrue(media["pointer"])
            self.assertTrue(media["motion"])
            measured = page.evaluate(
                """() => {
                  const host = document.createElement('div')
                  host.innerHTML = '<span data-ui="task-spin"></span><button data-ui="button" data-size="sm" type="button">小</button><button data-ui="button" type="button">常</button>'
                  document.body.append(host)
                  const spin = host.querySelector('[data-ui="task-spin"]')
                  const small = host.querySelector('[data-size="sm"]')
                  const regular = host.querySelector('[data-ui="button"]:not([data-size])')
                  return {
                    spin: getComputedStyle(spin).animationName,
                    small: parseFloat(getComputedStyle(small).minHeight),
                    regular: parseFloat(getComputedStyle(regular).minHeight),
                  }
                }"""
            )
            self.assertEqual(measured["spin"], "none")
            self.assertGreaterEqual(measured["small"], 44)
            self.assertGreaterEqual(measured["regular"], 44)
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.locator('[data-ui="mobile-bar"]')).to_be_visible()
            box = page.locator('[data-ui="nav-link"]').first.bounding_box()
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
                path = request.url.split("?", 1)[0]
                path = path[path.find("/api") + 4:] if "/api" in path else path.replace("http://127.0.0.1:8080", "")
                if path == "/public-config":
                    route.fulfill(json={"api_url": WEB + "/api", "profile": "isolated-dev"}, headers=cors)
                elif path == "/me":
                    route.fulfill(json={"display_name": "alice", "identity": {"subject": "fixture"}}, headers=cors)
                elif path == "/rule-types":
                    route.fulfill(json={"types": ["控制计划"]}, headers=cors)
                elif path == "/rules":
                    route.fulfill(json={"rules": []}, headers=cors)
                elif path.startswith("/rules/parse/"):
                    route.fulfill(json={"id": JOB, "status": "succeeded", "source_file_id": SOURCE, "source_file_version_id": VERSION, "document_type": "控制计划", "result": {"title": "规则 A", "body": BODY, "source_file_version_id": VERSION}}, headers=cors)
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
                  return Boolean(active && active.id !== 'rule-title' && active.closest('[data-ui="main"], main'))
                }"""
            )
            self.assertTrue(moved)
            page.get_by_test_id("rule-back").click()
            expect(page.locator('[data-ui="modal"]')).to_be_visible()
            inside = page.evaluate("() => Boolean(document.activeElement?.closest('[data-ui=\"modal\"]'))")
            self.assertTrue(inside)
            self.assertEqual(page.locator("h1").count(), 1)
            browser.close()
