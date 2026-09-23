"""Deterministic browser/API interleavings; no live backend or model calls."""
from pathlib import Path
import unittest

from tests.e2e.test_web_audit import WEB, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
JOB = "00000000-0000-4000-8000-000000000001"
SOURCE = "00000000-0000-4000-8000-000000000002"
VERSION = "00000000-0000-4000-8000-000000000003"
BODY = {"kind": "engine", "type": "控制计划", "rule_profile": "v3", "customers": [], "models": [], "parts": [], "rules": [{"id": "1", "category": "A", "priority": None, "rule_to_check": "检查 A", "type_of_rule": "table"}]}


@unittest.skipUnless(sync_playwright, "playwright not installed")
class RuleInteractionTests(unittest.TestCase):
    def route(self, page, *, status="succeeded", hold=False, publish_status=201):
        pending, posts = [], []
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
            elif path == "/rules" and request.method == "POST":
                if publish_status >= 400:
                    route.fulfill(status=publish_status, json={"error": "save_failed"}, headers=cors)
                else:
                    route.fulfill(status=publish_status, json={"id": "saved-rule", "title": "规则 A", "kind": "engine", "type": "控制计划", "created_at": "2026-09-23T00:00:00Z", "body": BODY, "source_file_id": SOURCE, "source_file_version_id": VERSION}, headers=cors)
            elif path == "/rules":
                route.fulfill(json={"rules": [{"id": "version-b", "kind": "engine", "title": "规则 B", "type": "控制计划"}]}, headers=cors)
            elif path == "/rules/saved-rule":
                route.fulfill(json={"id": "saved-rule", "title": "规则 A", "kind": "engine", "type": "控制计划", "created_at": "2026-09-23T00:00:00Z", "body": BODY, "source_file_id": SOURCE, "source_file_version_id": VERSION}, headers=cors)
            elif path == "/files":
                route.fulfill(status=201, json={"file_id": SOURCE, "id": VERSION}, headers=cors)
            elif path.endswith(("/blob", "/finalize")):
                route.fulfill(json={}, headers=cors)
            elif path == "/rules/parse":
                posts.append(request.post_data_json)
                if hold:
                    pending.append(route)
                else:
                    route.fulfill(status=202, json={"id": posts[-1]["request_id"], "status": "queued"}, headers=cors)
            elif path.startswith("/rules/parse/"):
                route.fulfill(json={"id": path.rsplit("/", 1)[1], "status": status, "source_file_id": SOURCE, "source_file_version_id": VERSION, "document_type": "控制计划", "result": {"title": "规则 A", "body": BODY, "source_file_version_id": VERSION} if status == "succeeded" else None}, headers=cors)
            else:
                route.fulfill(status=404, json={"error": "unexpected_test_request"}, headers=cors)

        page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))
        page.route("**/api/**", respond)
        return pending, posts

    def test_delayed_parse_cannot_be_replaced_by_new_or_open_other_rule(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            pending, posts = self.route(page, hold=True)
            page.goto(WEB + "/rules")
            expect(page.get_by_test_id("rule-versions")).to_contain_text("规则 B")
            page.get_by_test_id("new-rule").click()
            with page.expect_request("**/api/rules/parse"):
                page.get_by_test_id("rule-file").set_input_files({"name": "A.json", "mimeType": "application/json", "buffer": b"{}"})
            expect(page.get_by_test_id("rule-back")).to_be_disabled()
            expect(page.get_by_test_id("rule-file")).to_be_disabled()
            page.evaluate("() => { document.querySelector('[data-testid=rule-back]')?.click(); }")
            self.assertEqual(len(pending), 1)
            pending[0].fulfill(status=202, json={"id": posts[0]["request_id"], "status": "queued"}, headers={"Access-Control-Allow-Origin": WEB, "Access-Control-Allow-Credentials": "true"})
            page.wait_for_timeout(100)
            page.route("**/api/rules/parse/" + posts[0]["request_id"], lambda route: route.fulfill(json={"id": posts[0]["request_id"], "status": "succeeded", "source_file_id": SOURCE, "source_file_version_id": VERSION, "document_type": "控制计划", "result": {"title": "规则 A", "body": BODY, "source_file_version_id": VERSION}}, headers={"Access-Control-Allow-Origin": WEB, "Access-Control-Allow-Credentials": "true"}))
            expect(page.get_by_test_id("rule-title")).to_have_value("规则 A")
            expect(page.locator("#rule-origin")).to_contain_text("新规则")
            expect(page.get_by_test_id("rule-back")).to_be_enabled()
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
            page.get_by_test_id("rerun-rule").click()
            expect(page.locator("#rule-message")).to_contain_text("解析结果暂不确定")
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
                if route.request.method == "OPTIONS":
                    route.fulfill(status=204, headers={"Access-Control-Allow-Origin": WEB, "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "content-type"})
                    return
                route.fulfill(json={"id": JOB, "status": "failed", "source_file_id": SOURCE, "source_file_version_id": VERSION, "document_type": "", "result": {"error": "rule_type_required"}}, headers={"Access-Control-Allow-Origin": WEB, "Access-Control-Allow-Credentials": "true"})

            page.route("**/api/rules/parse/" + JOB, original_task)
            page.goto(WEB + "/rules?parse=" + JOB)
            expect(page.locator("#rule-message")).to_contain_text("请选择文档类型")
            expect(page.locator("#parse-type")).to_have_value("")
            self.assertEqual(posts, [])
            page.locator("#parse-type").select_option("控制计划")
            page.get_by_test_id("rerun-rule").click()
            expect(page.get_by_test_id("rule-title")).to_have_value("规则 A")
            self.assertEqual(len(posts), 1)
            self.assertNotEqual(posts[0]["request_id"], JOB)
            self.assertEqual(posts[0]["source_file_id"], SOURCE)
            self.assertEqual(posts[0]["source_file_version_id"], VERSION)
            self.assertEqual(posts[0]["document_type"], "控制计划")
            self.assertEqual([method for method in original_requests if method != "OPTIONS"], ["GET"])
            browser.close()


    def test_save_success_clears_the_leave_guard_before_navigation(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
            dialogs = []

            def on_dialog(dialog):
                dialogs.append(dialog.type)
                dialog.dismiss()

            page.on("dialog", on_dialog)
            page.goto(WEB + "/rules?parse=" + JOB)
            expect(page.get_by_test_id("rule-title")).to_have_value("规则 A")
            page.get_by_test_id("publish-rule").click()
            page.wait_for_function(
                """() => location.search.includes('saved=1') || (document.querySelector('[data-testid=publish-rule]') || {}).textContent?.includes('打开已保存规则')""",
                timeout=5000,
            )
            self.assertEqual(dialogs, [])
            if "saved=1" not in page.url:
                expect(page.get_by_test_id("publish-rule")).to_have_text("打开已保存规则")
                expect(page.get_by_test_id("publish-rule")).to_be_enabled()
            browser.close()

    def test_save_failure_returns_to_the_editable_candidate(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page, publish_status=500)
            page.goto(WEB + "/rules?parse=" + JOB)
            expect(page.get_by_test_id("rule-title")).to_have_value("规则 A")
            page.get_by_test_id("publish-rule").click()
            expect(page.locator("#rule-message")).to_contain_text("请求失败")
            expect(page.get_by_test_id("publish-rule")).to_have_text("保存规则")
            expect(page.get_by_test_id("publish-rule")).to_be_enabled()
            expect(page.get_by_test_id("rule-title")).to_be_enabled()
            browser.close()

    def test_confirmed_abandon_leaves_without_a_second_prompt(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
            dialogs = []

            def on_dialog(dialog):
                dialogs.append(dialog.type)
                dialog.dismiss()

            page.on("dialog", on_dialog)
            page.goto(WEB + "/rules?parse=" + JOB)
            expect(page.get_by_test_id("rule-title")).to_have_value("规则 A")
            page.get_by_test_id("rule-back").click()
            expect(page.locator('[data-ui="modal"]')).to_be_visible()
            page.get_by_role("button", name="放弃修改").click()
            page.wait_for_function("() => location.pathname.endsWith('/rules') && !location.search", timeout=5000)
            self.assertEqual(dialogs, [])
            browser.close()


if __name__ == "__main__":
    unittest.main()
