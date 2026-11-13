"""Positive browser hint journey. Controlled HTTP, not persistence acceptance."""

import os
import re
import unittest

PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")


@unittest.skipUnless(PREVIEW, "requires the explicit UI preview")
class ReviewHintsPreviewTests(unittest.TestCase):
    def test_review_suggestion_opens_comparison_and_records_an_ignore(self):
        from playwright.sync_api import expect, sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get("PPAP_TEST_CHROMIUM"))
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            recorded = []

            def respond(route):
                body = route.request.post_data_json
                recorded.append(body)
                route.fulfill(json={**body, "round_id": route.request.url.split("/rounds/")[1].split("/")[0], "user_id": "alice", "recorded_at": "2026-11-07T00:00:00Z"})

            page.route("**/rounds/*/review-hints", respond)
            page.goto(PREVIEW + "/preview/audit.html?scene=reports-review-hints&task=preview-report-task")
            page.get_by_role("navigation", name="文件队列", exact=True).get_by_role("button", name=re.compile("PFMEA")).click()
            details = page.get_by_role("region", name="审核详情", exact=True)
            expect(details.get_by_role("group", name="审核结果筛选", exact=True).get_by_role("button", name="建议复核", exact=True)).to_have_accessible_description("1")
            details.get_by_role("group", name="审核结果筛选", exact=True).get_by_role("button", name="建议复核", exact=True).click()
            details.get_by_role("row").get_by_role("button", name="建议复核", exact=True).click()
            comparison = page.get_by_role("region", name="建议复核对比", exact=True)
            expect(comparison).to_contain_text("同一份文件、同一检查版本和条款")
            expect(comparison.get_by_role("link", name="打开对比审核与依据")).to_have_attribute("href", re.compile(r"round=previous-round.*clause=1.1.*basis=1"))
            comparison.get_by_role("button", name="忽略此提示", exact=True).click()
            expect(comparison).to_have_count(0)
            assert recorded[0]["action"] == "ignored"
            assert recorded[0]["hint"]["compared_round_id"] == "previous-round"
            browser.close()
