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
    def test_batch_upload_keeps_uncertain_version_for_read_only_recovery(self):
        from playwright.sync_api import expect

        names = ["first.xlsx", "second.xlsx", "P037.xlsx", "fourth.xlsx"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
            created, blobs, finalized, reads, analyses, drafts = [], [], [], [], [], []
            read_results = ["failed_read", "staging", "failed_read", "ready", "staging", "staging"]

            def create_file(route):
                created.append(route.request.post_data_json["original_filename"])
                number = len(created)
                route.fulfill(status=201, json={"file_id": f"file-{number}", "id": f"version-{number}"})

            def write_blob(route):
                blobs.append(route.request.url)
                route.fulfill(json={})

            def finalize(route):
                finalized.append(route.request.url)
                if any(f"version-{number}" in route.request.url for number in (3, 4, 5)):
                    route.abort("failed")
                else:
                    route.fulfill(json={"status": "ready"})

            def read_version(route):
                reads.append(route.request.url)
                result = read_results[len(reads) - 1]
                if result == "failed_read":
                    route.abort("failed")
                else:
                    route.fulfill(json={"status": result})

            def save_draft(route):
                drafts.append(route.request.post_data_json)
                route.fulfill(json={"id": "draft-1"})

            def analyze(route):
                analyses.append(route.request.post_data_json["source_file_version_id"])
                route.fulfill(json={"id": "analysis-1", "status": "queued"})

            page.route("**/api/files", create_file)
            page.route("**/api/files/*/versions/*/blob", write_blob)
            page.route("**/api/files/*/versions/*/finalize", finalize)
            page.route("**/api/files/*/versions/*", read_version)
            page.route("**/api/drafts", lambda route: route.fulfill(status=201, json={"id": "draft-1"}))
            page.route("**/api/drafts/draft-1", save_draft)
            page.route("**/api/file-analyses", analyze)
            page.route("**/api/file-analyses/*", lambda route: route.fulfill(json={"id": "analysis-1", "status": "succeeded"}))
            page.goto(WEB)
            page.get_by_test_id("source-upload").set_input_files([
                {"name": name, "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"}
                for name in names
            ])

            expect(page.get_by_test_id("draft-items")).to_contain_text("first.xlsx")
            expect(page.get_by_test_id("draft-items")).to_contain_text("second.xlsx")
            expect(page.get_by_role("alert").filter(has_text="上传结果未确认：P037.xlsx")).to_be_visible()
            check = page.get_by_role("button", name="核对上传结果")
            expect(check).to_be_visible()
            self.assertEqual(check.evaluate("button => button.closest('[role=\"alert\"]')?.tagName"), "DIV")
            expect(page.get_by_text("fourth.xlsx")).to_have_count(0)
            self.assertEqual(created, names[:3])
            self.assertEqual(len(blobs), 3)
            self.assertEqual(len(finalized), 3)
            self.assertEqual([item["items"][-1]["file_version_id"] for item in drafts], ["version-1", "version-2"])
            self.assertTrue(page.evaluate("() => { const event = new Event('beforeunload', {cancelable: true}); window.dispatchEvent(event); return event.defaultPrevented }"))
            page.get_by_role("link", name="规则库").click()
            leave = page.get_by_role("dialog")
            expect(leave).to_be_visible()
            expect(leave).to_contain_text("P037.xlsx")
            expect(leave.get_by_role("button", name="保存并离开")).to_have_count(0)
            leave.get_by_role("button", name="继续核对").click()
            expect(leave).to_have_count(0)

            check.click()
            expect(check).to_be_visible()
            expect(page.get_by_role("alert").filter(has_text="仍未就绪")).to_be_visible()
            check.click()
            expect(check).to_be_visible()
            expect(page.get_by_role("alert").filter(has_text="暂时无法读取")).to_be_visible()
            check.click()
            expect(page.get_by_test_id("draft-items")).to_contain_text("P037.xlsx")
            expect(check).to_have_count(0)
            self.assertEqual(created, names[:3])
            self.assertEqual(len(blobs), 3)
            self.assertEqual(len(finalized), 3)
            self.assertEqual(len(reads), 4)
            self.assertEqual(analyses, ["version-1", "version-2", "version-3"])
            self.assertEqual(drafts[-1]["items"][-1]["file_version_id"], "version-3")
            page.get_by_test_id("source-upload").set_input_files({"name": "later.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"})
            expect(page.get_by_role("button", name="核对上传结果")).to_be_visible()
            page.get_by_role("button", name="暂不加入").click()
            expect(page.get_by_role("button", name="核对上传结果")).to_have_count(0)
            expect(page.get_by_test_id("source-upload")).to_be_enabled()
            expect(page.get_by_test_id("draft-items")).to_contain_text("P037.xlsx")
            expect(page.get_by_test_id("draft-items")).not_to_contain_text("later.xlsx")
            self.assertEqual(created, names[:3] + ["later.xlsx"])
            self.assertEqual(drafts[-1]["items"][-1]["file_version_id"], "version-3")
            page.get_by_test_id("source-upload").set_input_files({"name": "stuck.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"})
            expect(page.get_by_role("button", name="核对上传结果")).to_be_visible()
            page.get_by_role("checkbox", name="参与审核：first.xlsx").uncheck()
            expect(page.get_by_test_id("save-draft")).to_be_disabled()
            dialogs = []
            page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
            page.get_by_role("link", name="规则库").click()
            leave = page.get_by_role("dialog")
            expect(leave).to_contain_text("stuck.xlsx")
            expect(leave).to_contain_text("其他未保存修改也会放弃")
            expect(leave.get_by_role("button", name="保存并离开")).to_have_count(0)
            leave.get_by_role("button", name="暂不加入并离开").click()
            expect(page.get_by_test_id("rule-versions")).to_be_visible()
            self.assertEqual(dialogs, [])
            self.assertEqual(drafts[-1]["items"][-1]["file_version_id"], "version-3")
            self.assertEqual(drafts[-1]["items"][0]["role"], "target")
            browser.close()

    def test_draft_network_failure_has_chinese_message(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
            page.route("**/api/drafts?*", lambda route: route.abort("failed"))
            page.goto(WEB)
            alert = page.get_by_role("alert").filter(has_text="网络连接失败，请检查网络后重试。")
            expect(alert).to_be_visible()
            self.assertNotIn("Failed to fetch", alert.inner_text())
            browser.close()

    def test_draft_analysis_read_failure_is_local_and_retry_does_not_reanalyze(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
            items = [
                {"file_id": "reference", "file_version_id": "ref-version", "original_filename": "flow.xlsx", "role": "reference", "rule_version_id": None},
                {"file_id": SOURCE, "file_version_id": VERSION, "original_filename": "control.xlsx", "role": "target", "rule_version_id": "version-b"},
            ]
            reads, writes = [], []
            page.on("request", lambda request: writes.append(request.url) if request.method in ("POST", "PUT") else None)
            page.route("**/api/drafts/" + JOB, lambda route: route.fulfill(json={"id": JOB, "status": "open", "items": items}))

            def read_latest(route):
                reads.append(route.request.url)
                if "/reference/" in route.request.url and sum("/reference/" in url for url in reads) == 1:
                    route.abort("failed")
                else:
                    reference = "/reference/" in route.request.url
                    route.fulfill(json={"analysis": {"id": "ref-analysis" if reference else "target-analysis", "status": "failed" if reference else "succeeded"}})

            page.route("**/api/files/*/versions/*/analysis", read_latest)
            page.route("**/api/file-analyses/ref-analysis", lambda route: route.fulfill(json={"id": "ref-analysis", "status": "failed"}))
            page.route("**/api/file-analyses/target-analysis", lambda route: route.fulfill(json={
                "id": "target-analysis", "status": "succeeded", "result": {
                    "metadata": {"customer": "CETUS", "model": None, "part": None}, "document_type": "控制计划",
                    "matched_rule_id": "version-b", "matching_status": "matched", "notices": [],
                },
            }))
            page.goto(WEB + "/?draft=" + JOB)
            expect(page.get_by_role("button", name="重新读取 flow.xlsx 的识别结果")).to_be_visible()
            expect(page.get_by_text("CETUS", exact=True)).to_be_visible()
            expect(page.get_by_role("alert")).to_have_count(0)
            expect(page.get_by_test_id("item-rule")).to_have_value("version-b")
            page.get_by_role("button", name="重新读取 flow.xlsx 的识别结果").click()
            expect(page.get_by_text("识别未完成，请核对原件", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="重新读取 flow.xlsx 的识别结果")).to_have_count(0)
            expect(page.get_by_text("匹配失败，请手动选择", exact=True)).to_have_count(0)
            self.assertEqual(len(reads), 3)
            self.assertEqual(writes, [])
            browser.close()

    def test_lost_or_invalid_finalize_response_reads_version_once_without_reposting(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for readback, finalize_response in (("ready", "lost"), ("ready", "bad_json"),
                                                ("staging", "lost"), ("failed_read", "lost")):
                with self.subTest(readback=readback, finalize_response=finalize_response):
                    page = browser.new_page()
                    self.route(page)
                    created_posts, finalize_posts, version_reads = [], [], []

                    def record_create(request):
                        if request.method == "POST" and request.url.endswith("/api/files"):
                            created_posts.append(request.method)

                    page.on("request", record_create)

                    def lose_finalize_response(route):
                        finalize_posts.append(route.request.method)
                        if finalize_response == "bad_json":
                            route.fulfill(status=200, body="{", content_type="application/json")
                        else:
                            route.abort("failed")

                    def read_version(route):
                        version_reads.append(route.request.method)
                        if readback == "failed_read":
                            route.abort("failed")
                        else:
                            route.fulfill(json={"status": readback})

                    page.route("**/api/files/*/versions/*/finalize", lose_finalize_response)
                    page.route("**/api/files/*/versions/*", read_version)
                    page.route("**/api/drafts", lambda route: route.fulfill(status=201, json={"id": JOB}))
                    page.route("**/api/drafts/" + JOB, lambda route: route.fulfill(json={"id": JOB, "status": "open", "items": []}))
                    page.goto(WEB)
                    upload = page.get_by_test_id("source-upload")
                    upload.set_input_files({"name": "尺寸检验报告-example.xlsm", "mimeType": "application/vnd.ms-excel.sheet.macroEnabled.12", "buffer": b"fixture"})
                    if readback == "ready":
                        expect(page.get_by_test_id("draft-items")).to_contain_text("尺寸检验报告-example.xlsm")
                        expect(page.get_by_role("alert")).to_have_count(0)
                        expect(upload).to_be_enabled()
                    else:
                        expect(page.get_by_role("alert").filter(has_text="上传结果未确认：尺寸检验报告-example.xlsm")).to_be_visible()
                        expect(page.get_by_test_id("draft-items")).to_have_count(0)
                        expect(page.get_by_role("button", name="核对上传结果")).to_be_visible()
                        expect(upload).to_be_disabled()
                    expect(page.get_by_text("正在上传 尺寸检验报告-example.xlsm", exact=True)).to_have_count(0)
                    self.assertEqual(created_posts, ["POST"])
                    self.assertEqual(finalize_posts, ["POST"])
                    self.assertEqual(version_reads, ["GET"])
                    page.close()
            browser.close()

    def test_failed_round_reaudit_preserves_inputs_and_mode_without_starting_a_job(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
            draft_id = "00000000-0000-4000-8000-000000000004"
            items = [
                {"file_id": SOURCE, "file_version_id": VERSION, "original_filename": "control-plan.xlsx", "role": "target", "rule_version_id": "version-b"},
                {"file_id": "reference-file", "file_version_id": "reference-version", "original_filename": "process-flow.xlsx", "role": "reference", "rule_version_id": None},
            ]
            posts, saves, accepted = [], [], []
            page.route("**/api/public-config", lambda route: route.fulfill(json={"api_url": WEB + "/api", "profile": "isolated-dev", "deep_offered": True}))
            page.route("**/api/rounds/" + JOB, lambda route: route.fulfill(json={
                "id": JOB, "execution": "failed", "analysis_mode": "deep", "inputs": items, "findings": [],
                "failure": {"code": "deep_budget_exhausted", "message": "执行额度已用完。", "retryable": False},
            }))

            def create_draft(route):
                posts.append(route.request.post_data_json)
                route.fulfill(status=201, json={"id": draft_id})

            def save_draft(route):
                saves.append(route.request.post_data_json)
                route.fulfill(json={"id": draft_id, "status": "open", "items": items, "analysis_mode": "deep"})

            page.route("**/api/drafts", create_draft)
            page.route("**/api/drafts/" + draft_id, save_draft)
            page.route("**/api/drafts/*/accept", lambda route: (accepted.append(route.request.method), route.fulfill(status=500, json={})))
            page.route("**/api/integrations/feishu/usage/quota?*", lambda route: route.fulfill(json={"accept_allowed": True}))
            page.goto(WEB + "/?round=" + JOB)
            page.get_by_test_id("prepare-again").click()
            expect(page.locator("#audit-preparation")).to_be_visible()
            expect(page.get_by_test_id("draft-items")).to_contain_text("control-plan.xlsx")
            expect(page.get_by_test_id("draft-items")).to_contain_text("process-flow.xlsx")
            self.assertEqual(posts, [{"predecessor_id": JOB}])
            self.assertEqual(saves, [{"items": [{key: item[key] for key in ("file_version_id", "role", "rule_version_id")} for item in items], "analysis_mode": "deep"}])
            self.assertEqual(accepted, [])
            browser.close()

    def test_round_read_failure_keeps_acceptance_and_recovers_without_resubmitting(self):
        from playwright.sync_api import expect

        for entry, first_read in (("submit", "connectionreset"), ("deep_link", "connectionreset"),
                                  ("submit", "bad_json"), ("deep_link", "bad_json")):
            with self.subTest(entry=entry, first_read=first_read), sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                self.route(page)
                draft_id = "00000000-0000-4000-8000-000000000004"
                item = {"file_id": SOURCE, "file_version_id": VERSION, "original_filename": "control-plan.xlsx", "role": "target", "rule_version_id": "version-b"}
                accepted = {"id": JOB, "execution": "queued", "analysis_mode": "standard", "inputs": [item], "findings": []}
                posts, reads = [], []
                page.route("**/api/drafts/" + draft_id, lambda route: route.fulfill(json={"id": draft_id, "status": "open", "items": [item]}))
                page.route("**/api/files/*/versions/*/analysis", lambda route: route.fulfill(json={"analysis": None}))
                page.route("**/api/integrations/feishu/usage/quota?*", lambda route: route.fulfill(json={"accept_allowed": True}))

                def accept(route):
                    posts.append(route.request.method)
                    route.fulfill(status=201, json=accepted)

                def read_round(route):
                    reads.append(route.request.method)
                    if len(reads) == 1:
                        if first_read == "connectionreset":
                            route.abort("connectionreset")
                        else:
                            route.fulfill(status=200, body="{", content_type="application/json")
                    else:
                        route.fulfill(json={**accepted, "execution": "completed"})

                page.route("**/api/drafts/" + draft_id + "/accept", accept)
                page.route("**/api/rounds/" + JOB, read_round)
                page.goto(WEB + ("/?draft=" + draft_id if entry == "submit" else "/?round=" + JOB))
                if entry == "submit":
                    page.get_by_test_id("submit-audit").click()
                expect(page.get_by_text("暂时无法读取审核状态，正在重新连接。请勿重复提交。", exact=True)).to_be_visible()
                expect(page.locator("#audit-preparation")).to_have_count(0)
                expect(page.get_by_test_id("submit-audit")).to_have_count(0)
                expect(page.get_by_text("检查完成", exact=True).first).to_be_visible(timeout=10000)
                expect(page.get_by_text("暂时无法读取审核状态，正在重新连接。请勿重复提交。", exact=True)).to_have_count(0)
                self.assertEqual(posts, ["POST"] if entry == "submit" else [])
                self.assertEqual(reads, ["GET", "GET"])
                browser.close()

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
            expect(page.get_by_role("dialog")).to_be_visible()
            page.get_by_role("button", name="放弃修改").click()
            page.wait_for_function("() => location.pathname.endsWith('/rules') && !location.search", timeout=5000)
            self.assertEqual(dialogs, [])
            browser.close()


if __name__ == "__main__":
    unittest.main()
