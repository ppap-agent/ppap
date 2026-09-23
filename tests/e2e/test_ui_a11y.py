"""Desktop, mobile, keyboard, focus, touch, and reduced-motion checks for the UI vocabulary."""

import unittest

from tests.e2e.test_web_audit import WEB, sync_playwright


@unittest.skipUnless(sync_playwright, "playwright not installed")
class UiA11yTests(unittest.TestCase):
    def test_keyboard_focus_motion_and_viewports(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
            page = context.new_page()
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
            animation = page.evaluate("() => getComputedStyle(document.querySelector('[data-ui]')).animationName")
            self.assertEqual(animation, "none")
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.locator('[data-ui="mobile-bar"]')).to_be_visible()
            box = page.locator('[data-ui="nav-link"]').first.bounding_box()
            self.assertIsNotNone(box)
            self.assertGreaterEqual(box["height"], 44)
            context.close()
            browser.close()
