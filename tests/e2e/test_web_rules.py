"""Customer rule review/publication against the actual isolated-dev stack."""
from __future__ import annotations

import json
import unittest
import uuid

from test_web_audit import WEB, isolated_dev_login, sync_playwright


@unittest.skipUnless(sync_playwright, "playwright not installed")
class RulePreparationTests(unittest.TestCase):
    def test_import_review_publish_and_revisit(self):
        from playwright.sync_api import expect

        title = "规则导入 " + uuid.uuid4().hex[:8]
        source = {
            "kind": "engine", "type": "控制计划", "rule_profile": "v3",
            "customers": ["测试客户"], "models": [], "parts": [],
            "rules": [{"id": "1", "category": "基本信息", "priority": None, "rule_to_check": "必须填写零件号", "type_of_rule": "table"}],
        }
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            isolated_dev_login(page)
            page.get_by_test_id("pc-shell").get_by_test_id("nav-rules").click()
            page.wait_for_url("**/rules")
            page.get_by_test_id("new-rule").click()
            page.get_by_test_id("rule-file").set_input_files({"name": "控制计划规则.json", "mimeType": "application/json", "buffer": json.dumps(source, ensure_ascii=False).encode()})
            expect(page.get_by_test_id("rule-title")).to_be_visible(timeout=30000)
            expect(page.get_by_test_id("rule-message")).to_contain_text("解析完成")
            expect(page.get_by_test_id("rule-items").locator("article")).to_have_count(1)
            page.get_by_test_id("rule-title").fill(title)
            page.locator('[data-field="rule_to_check"]').fill("必须填写零件号和版本")
            page.get_by_test_id("publish-rule").click()
            expect(page.locator("text=已保存规则")).to_be_visible()
            page.goto(f"{WEB}/rules")
            row = page.get_by_test_id("rule-versions").locator("tr", has_text=title)
            expect(row).to_have_count(1)
            row.get_by_role("link", name=title).click()
            expect(page.locator('[data-field="rule_to_check"]')).to_have_count(0)
            expect(page.get_by_test_id("rule-items")).to_contain_text("必须填写零件号和版本")
            expect(page.locator("#rule-metadata")).to_contain_text("测试客户")
            expect(page.locator("#rule-origin")).to_contain_text("版本")
            downloaded = page.request.get(page.locator("#rule-source").get_attribute("href"))
            self.assertEqual(downloaded.status, 200)
            self.assertEqual(json.loads(downloaded.body()), source)
            browser.close()


if __name__ == "__main__":
    unittest.main()
