"""Deterministic browser/API interleavings; no live backend or model calls."""
from pathlib import Path
import unittest

from tests.e2e.test_web_audit import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
WEB = "http://127.0.0.1:4173"
JOB = "00000000-0000-4000-8000-000000000001"
SOURCE = "00000000-0000-4000-8000-000000000002"
VERSION = "00000000-0000-4000-8000-000000000003"
BODY = {"kind": "engine", "type": "控制计划", "rule_profile": "v3", "rules": [{"id": "1", "category": "A", "rule_to_check": "检查 A", "type_of_rule": "table"}]}


@unittest.skipUnless(sync_playwright, "playwright not installed")
class RuleInteractionTests(unittest.TestCase):
    def route(self, page, *, status="succeeded", hold=False):
        pending, posts = [], []

        def respond(route):
            request = route.request
            path = request.url.removeprefix(WEB).split("?", 1)[0]
            if path in {"/rules", "/rules.js", "/product-api.js", "/shell.js", "/shell.css"} and not path.startswith("/api"):
                filename = "rules.html" if path == "/rules" else path[1:]
                route.fulfill(path=str(ROOT / "apps" / "web" / filename))
            elif path == "/runtime-config.json":
                route.fulfill(json={"api_url": WEB + "/api"})
            elif path == "/api/public-config":
                route.fulfill(json={"api_url": WEB + "/api"})
            elif path == "/api/me":
                route.fulfill(json={"identity": {"subject": "fixture"}})
            elif path == "/api/rule-types":
                route.fulfill(json={"types": ["控制计划"]})
            elif path == "/api/rules":
                route.fulfill(json={"rules": [{"id": "version-b", "kind": "engine", "title": "规则 B", "type": "控制计划"}]})
            elif path == "/api/files":
                route.fulfill(status=201, json={"file_id": SOURCE, "id": VERSION})
            elif path.endswith(("/blob", "/finalize")):
                route.fulfill(json={})
            elif path == "/api/rules/parse":
                posts.append(request.post_data_json)
                if hold:
                    pending.append(route)
                else:
                    route.fulfill(status=202, json={"id": posts[-1]["request_id"], "status": "queued"})
            elif path.startswith("/api/rules/parse/"):
                route.fulfill(json={"id": path.rsplit("/", 1)[1], "status": status, "source_file_id": SOURCE, "source_file_version_id": VERSION, "document_type": "控制计划", "result": {"title": "规则 A", "body": BODY, "source_file_version_id": VERSION} if status == "succeeded" else None})
            else:
                route.fulfill(status=404, json={"error": "unexpected_test_request"})

        page.route("**/*", respond)
        return pending, posts

    def test_delayed_parse_cannot_be_replaced_by_new_or_open_other_rule(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            pending, posts = self.route(page, hold=True)
            page.goto(WEB + "/rules")
            expect(page.locator("#rule-versions")).to_contain_text("规则 B")
            page.locator("#rule-file").set_input_files({"name": "A.json", "mimeType": "application/json", "buffer": b"{}"})
            with page.expect_request("**/api/rules/parse"):
                page.locator("#parse-rule").click()
            expect(page.locator("#new-rule")).to_be_disabled()
            expect(page.locator("#rule-versions button")).to_be_disabled()
            expect(page.locator("#rule-file")).to_be_disabled()
            page.evaluate("() => { document.querySelector('#new-rule').click(); document.querySelector('#rule-versions button').click(); }")
            self.assertEqual(len(pending), 1)
            pending[0].fulfill(status=202, json={"id": posts[0]["request_id"], "status": "queued"})
            expect(page.locator("#rule-title")).to_have_value("规则 A")
            expect(page.locator("#rule-origin")).to_contain_text("新规则")
            expect(page.locator("#new-rule")).to_be_enabled()
            self.assertEqual(len(posts), 1)
            browser.close()

    def test_refresh_reads_unknown_without_submitting_and_warns_before_explicit_rerun(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            _, posts = self.route(page, status="unknown")
            page.goto(WEB + "/rules?parse=" + JOB)
            expect(page.locator("#rule-message")).to_contain_text("再次消耗模型费用")
            expect(page.locator("#parse-type")).to_have_value("控制计划")
            self.assertEqual(posts, [])
            page.reload()
            expect(page.locator("#rule-message")).to_contain_text("原任务可能仍会完成")
            self.assertEqual(posts, [])
            page.locator("#rerun-rule").click()
            expect(page.locator("#rule-message")).to_contain_text("结果未知")
            self.assertEqual(len(posts), 1)
            self.assertNotEqual(posts[0]["request_id"], JOB)
            self.assertEqual(posts[0]["document_type"], "控制计划")
            browser.close()

    def test_failed_task_can_be_retried_with_selected_type_without_changing_original(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            _, posts = self.route(page)
            original_requests = []

            def original_task(route):
                original_requests.append(route.request.method)
                route.fulfill(json={"id": JOB, "status": "failed", "source_file_id": SOURCE, "source_file_version_id": VERSION, "document_type": "", "result": {"error": "rule_type_required"}})

            page.route("**/api/rules/parse/" + JOB, original_task)
            page.goto(WEB + "/rules?parse=" + JOB)
            expect(page.locator("#rule-message")).to_contain_text("请选择文档类型")
            expect(page.locator("#parse-type")).to_have_value("")
            self.assertEqual(posts, [])
            page.locator("#parse-type").select_option("控制计划")
            page.locator("#rerun-rule").click()
            expect(page.locator("#rule-message")).to_contain_text("尚未发布")
            self.assertEqual(len(posts), 1)
            self.assertNotEqual(posts[0]["request_id"], JOB)
            self.assertEqual(posts[0]["source_file_id"], SOURCE)
            self.assertEqual(posts[0]["source_file_version_id"], VERSION)
            self.assertEqual(posts[0]["document_type"], "控制计划")
            self.assertEqual(original_requests, ["GET"])
            browser.close()


if __name__ == "__main__":
    unittest.main()
