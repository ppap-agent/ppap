"""Deterministic browser/API interleavings; no live backend or model calls."""

import os
import unittest
from pathlib import Path
from urllib.parse import urlsplit

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional until the e2e job installs Playwright
    sync_playwright = None

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")

ROOT = Path(__file__).resolve().parents[2]
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
class RuleInteractionTests(unittest.TestCase):
    def test_explicit_all_records_selection_survives_initial_history_read(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
            pending, writes = [], []
            page.on("request", lambda request: writes.append(request.method) if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None)
            page.route("**/api/drafts?*", lambda route: pending.append((route, {"drafts": [], "next_offset": None})))
            summary = {
                "id": JOB,
                "execution": "completed",
                "analysis_mode": "standard",
                "inputs": [{"file_version_id": VERSION, "original_filename": "history.xlsx", "role": "target", "rule": None}],
                "result_counts": {"PASS": 1},
                "reviewed_count": 0,
                "created_at": "2026-10-18T00:00:00Z",
            }
            page.route("**/api/rounds?*", lambda route: pending.append((route, {"rounds": [summary], "next_offset": None})))
            page.goto(WEB)
            selected = page.get_by_role("tab", name="全部记录", exact=True)
            expect(selected).to_have_attribute("aria-selected", "true")
            selected.click()
            self.assertEqual(len(pending), 2)
            for route, body in pending:
                route.fulfill(json=body)
            expect(page.get_by_role("button", name="刷新审核记录", exact=True)).to_be_enabled()
            expect(selected).to_have_attribute("aria-selected", "true")
            expect(page.get_by_role("table", name="全部审核记录", exact=True)).to_contain_text("history.xlsx")
            self.assertEqual(writes, [])
            browser.close()

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
            page.get_by_test_id("source-upload").set_input_files(
                [{"name": name, "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"} for name in names]
            )

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
            drafts_failed = True
            writes = []
            page.on("request", lambda request: writes.append(request.url) if request.method in ("POST", "PUT", "PATCH", "DELETE") else None)
            page.route("**/api/rules", lambda route: route.fulfill(json={"rules": []}))
            page.route(
                "**/api/rounds?*",
                lambda route: route.fulfill(
                    json={
                        "rounds": [
                            {
                                "id": JOB,
                                "execution": "failed",
                                "predecessor_id": None,
                                "analysis_mode": "standard",
                                "inputs": [{"file_version_id": VERSION, "original_filename": "已读取的审核.xlsx", "role": "target", "rule": None}],
                                "result_counts": {},
                                "reviewed_count": 0,
                                "created_at": "2026-10-16T00:00:00Z",
                                "case_title": "",
                                "case_scope": "",
                            }
                        ],
                        "next_offset": None,
                    }
                ),
            )

            def read_drafts(route):
                if drafts_failed:
                    route.abort("failed")
                else:
                    route.fulfill(
                        json={
                            "drafts": [
                                {
                                    "id": SOURCE,
                                    "status": "open",
                                    "predecessor_id": None,
                                    "analysis_mode": "standard",
                                    "writeback": {"available": False},
                                    "items": [
                                        {
                                            "file_id": JOB,
                                            "file_version_id": "00000000-0000-4000-8000-000000000005",
                                            "original_filename": "恢复的准备.xlsx",
                                            "role": "target",
                                            "rule_version_id": None,
                                            "rule": None,
                                            "status": "ready",
                                        }
                                    ],
                                    "updated_at": "2026-10-16T01:00:00Z",
                                }
                            ],
                            "next_offset": None,
                        }
                    )

            page.route("**/api/drafts?*", read_drafts)
            page.goto(WEB)
            alert = page.get_by_role("alert").filter(has_text="审核准备读取失败：暂时无法连接服务，请重新读取。")
            expect(alert).to_be_visible()
            self.assertNotIn("Failed to fetch", alert.inner_text())
            self.assertNotIn("unexpected_test_request", alert.inner_text())
            table = page.get_by_role("table", name="需要处理审核记录", exact=True)
            expect(table.get_by_text("已读取的审核.xlsx", exact=True)).to_be_visible()
            expect(page.get_by_text("已读取的记录仍可打开；状态可能已变化。", exact=True)).to_be_visible()
            drafts_failed = False
            alert.get_by_role("button", name="重新读取", exact=True).click()
            expect(alert).to_have_count(0)
            expect(table.get_by_text("恢复的准备.xlsx", exact=True)).to_be_visible()
            expect(table.get_by_text("已读取的审核.xlsx", exact=True)).to_be_visible()
            self.assertEqual(writes, [])
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
            page.route(
                "**/api/file-analyses/target-analysis",
                lambda route: route.fulfill(
                    json={
                        "id": "target-analysis",
                        "status": "succeeded",
                        "result": {
                            "metadata": {"customer": "CETUS", "model": None, "part": None},
                            "document_type": "控制计划",
                            "matched_rule_id": "version-b",
                            "matching_status": "matched",
                            "notices": [],
                        },
                    }
                ),
            )
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
            for readback, finalize_response in (("ready", "lost"), ("ready", "bad_json"), ("staging", "lost"), ("failed_read", "lost")):
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
            page.route(
                "**/api/rounds/" + JOB,
                lambda route: route.fulfill(
                    json={
                        "id": JOB,
                        "execution": "failed",
                        "analysis_mode": "deep",
                        "inputs": items,
                        "findings": [],
                        "failure": {"code": "deep_budget_exhausted", "message": "执行额度已用完。", "retryable": False},
                    }
                ),
            )

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

        for entry, first_read in (("submit", "connectionreset"), ("deep_link", "connectionreset"), ("submit", "bad_json"), ("deep_link", "bad_json")):
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
            elif path == "/rules" and request.method == "POST":
                if publish_status >= 400:
                    route.fulfill(status=publish_status, json={"error": "save_failed"}, headers=cors)
                else:
                    route.fulfill(
                        status=publish_status,
                        json={
                            "id": "saved-rule",
                            "title": "规则 A",
                            "kind": "engine",
                            "type": "控制计划",
                            "created_at": "2026-09-23T00:00:00Z",
                            "body": BODY,
                            "source_file_id": SOURCE,
                            "source_file_version_id": VERSION,
                        },
                        headers=cors,
                    )  # noqa: E501
            elif path == "/rules":
                route.fulfill(json={"rules": [{"id": "version-b", "kind": "engine", "title": "规则 B", "type": "控制计划"}]}, headers=cors)
            elif path == "/rules/saved-rule":
                route.fulfill(
                    json={
                        "id": "saved-rule",
                        "title": "规则 A",
                        "kind": "engine",
                        "type": "控制计划",
                        "created_at": "2026-09-23T00:00:00Z",
                        "body": BODY,
                        "source_file_id": SOURCE,
                        "source_file_version_id": VERSION,
                    },
                    headers=cors,
                )  # noqa: E501
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
                route.fulfill(
                    json={
                        "id": path.rsplit("/", 1)[1],
                        "status": status,
                        "source_file_id": SOURCE,
                        "source_file_version_id": VERSION,
                        "document_type": "控制计划",
                        "result": {"title": "规则 A", "body": BODY, "source_file_version_id": VERSION} if status == "succeeded" else None,
                    },
                    headers=cors,
                )  # noqa: E501
            else:
                route.fulfill(status=404, json={"error": "unexpected_test_request"}, headers=cors)

        page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))
        page.route("**/api/**", respond)
        return pending, posts

    def test_late_parse_reply_cannot_replace_another_requirement(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page, status="queued")
            state["hold_next_parse"] = True
            self.open_requirement(page)
            expect(page.get_by_label("要求名称", exact=True)).to_be_visible()
            first = state["parses"][0]
            self.assertEqual(len(state["held"]), 1)
            page.get_by_role("link", name="返回审核要求", exact=True).click()
            expect(page.get_by_role("heading", name="审核要求", exact=True)).to_be_visible()
            page.get_by_label("上传共用要求文件").set_input_files({"name": "B.json", "mimeType": "application/json", "buffer": b"second"})
            state["status"] = "succeeded"
            page.get_by_label("适用资料类型").select_option("控制计划")
            page.get_by_role("button", name="提取条款", exact=True).click()
            expect(page.get_by_label("要求名称", exact=True)).to_have_value("规则 A")
            page.get_by_label("要求名称", exact=True).fill("第二份要求")
            second = state["parses"][1]
            self.assertNotEqual(first["source_file_version_id"], second["source_file_version_id"])
            self.assertNotEqual(first["request_id"], second["request_id"])
            state["writes"].clear()
            with page.expect_response(lambda response: response.request.method == "POST" and response.request.post_data_json.get("request_id") == first["request_id"]) as late:
                state["held"][0].fulfill(
                    status=202,
                    json={
                        "id": first["request_id"],
                        "status": "succeeded",
                        "source_file_id": SOURCE,
                        "source_file_version_id": first["source_file_version_id"],
                        "document_type": "控制计划",
                        "result": {"title": "迟到的旧要求", "body": BODY, "source_file_version_id": first["source_file_version_id"]},
                    },
                )
            late.value.finished()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            expect(page.get_by_label("要求名称", exact=True)).to_have_value("第二份要求")
            expect(page.get_by_role("region", name="要求条款", exact=True).get_by_role("article")).to_have_count(1)
            expect(page.get_by_role("main")).not_to_contain_text("迟到的旧要求")
            self.assertEqual(len(state["parses"]), 2)
            self.assertEqual(state["saves"], [])
            self.assertEqual(state["writes"], [])
            browser.close()

    def test_refresh_reads_unknown_without_submitting_and_warns_before_explicit_rerun(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_default_timeout(5000)
            self.route(page)
            page.route("**/api/rules", lambda route: route.fulfill(json={"rules": []}))
            page.route("**/api/drafts?*", lambda route: route.fulfill(json={"drafts": [], "next_offset": None}))
            page.route("**/api/rounds?*", lambda route: route.fulfill(json={"rounds": [], "next_offset": None}))
            source, posts, reads, delayed, delayed_posts, writes = {}, [], [], [], [], []
            hold_original = False
            page.on("request", lambda request: writes.append((request.method, urlsplit(request.url).path.removeprefix("/api"))) if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None)

            def create_file(route):
                value = route.request.post_data_json
                source.update(
                    file_id=SOURCE,
                    id=value["request_id"],
                    original_filename=value["original_filename"],
                    byte_size=value["byte_size"],
                    content_type=value["content_type"],
                    sha256=value["sha256"],
                    status="staging",
                )
                route.fulfill(status=201, json=source)

            def receive_blob(route):
                source["status"] = "ready"
                route.fulfill(json=source)

            def job(identity, status="unknown"):
                return {
                    "id": identity,
                    "status": status,
                    "source_file_id": SOURCE,
                    "source_file_version_id": source["id"],
                    "document_type": "控制计划",
                    "result": {"title": "迟到的旧结果", "body": BODY, "source_file_version_id": source["id"]} if status == "succeeded" else None,
                }

            def parse(route):
                self.assertEqual(route.request.method, "POST")
                posts.append(route.request.post_data_json)
                if len(posts) == 2:
                    delayed_posts.append(route)
                else:
                    route.fulfill(status=202, json=job(posts[-1]["request_id"]))

            def read(route):
                identity = route.request.url.rsplit("/", 1)[1]
                reads.append((route.request.method, identity))
                if route.request.method != "GET":
                    route.fulfill(status=405, json={"error": "read_only_recovery_required"})
                    return
                if hold_original and identity == posts[0]["request_id"]:
                    delayed.append(route)
                else:
                    route.fulfill(json=job(identity))

            page.route("**/api/files", create_file)
            page.route("**/api/files/*/versions/*/blob", receive_blob)
            page.route("**/api/rules/parse", parse)
            page.route("**/api/rules/parse/*", read)
            page.goto(WEB + "/?view=requirements")
            page.get_by_label("上传共用要求文件").set_input_files({"name": "待核对要求.json", "mimeType": "application/json", "buffer": b"{}"})
            page.get_by_label("适用资料类型").select_option("控制计划")
            page.get_by_role("button", name="提取条款", exact=True).click()
            unknown = page.get_by_role("alert").filter(has_text="提取结果暂不确定")
            expect(unknown).to_contain_text("原任务可能仍会完成")
            expect(page.get_by_role("status").filter(has_text="正在提取条款")).to_have_count(0)
            self.assertEqual(len(posts), 1)
            original = posts[0]["request_id"]
            writes.clear()
            page.reload()
            expect(unknown).to_be_visible()
            self.assertEqual(len(posts), 1)
            self.assertEqual(writes, [])
            with page.expect_request("**/api/rules/parse/" + original):
                page.get_by_role("button", name="核对原提取请求", exact=True).click()
            expect(unknown).to_be_visible()
            self.assertEqual(len(posts), 1)
            self.assertEqual(writes, [])
            page.get_by_role("button", name="重新提取条款", exact=True).click()
            confirmation = page.get_by_role("dialog", name="重新提取条款？", exact=True)
            expect(confirmation).to_contain_text("再次消耗模型费用")
            confirmation.get_by_role("button", name="取消", exact=True).click()
            self.assertEqual(len(posts), 1)
            self.assertEqual(writes, [])
            hold_original = True
            with page.expect_request("**/api/rules/parse/" + original):
                page.get_by_role("button", name="刷新提取状态", exact=True).click()
            self.assertEqual(len(delayed), 1)
            page.get_by_role("button", name="重新提取条款", exact=True).click()
            confirmation.get_by_role("button", name="确认重新提取", exact=True).click()
            expect(unknown).to_be_visible()
            self.assertEqual(len(posts), 2)
            self.assertNotEqual(posts[1]["request_id"], original)
            self.assertEqual(posts[1]["source_file_id"], SOURCE)
            self.assertEqual(posts[1]["source_file_version_id"], source["id"])
            self.assertEqual(posts[1]["document_type"], "控制计划")
            delayed[0].fulfill(json=job(original, "succeeded"))
            with page.expect_request("**/api/rules/parse/" + posts[1]["request_id"]):
                page.get_by_role("button", name="刷新提取状态", exact=True).click()
            expect(unknown).to_be_visible()
            expect(page.get_by_label("要求名称", exact=True)).to_have_value("待核对要求")
            expect(page.get_by_role("region", name="要求条款", exact=True).get_by_role("article")).to_have_count(0)
            self.assertIn(("GET", original), reads)
            self.assertTrue(all(method == "GET" for method, _identity in reads))
            self.assertEqual(len(delayed_posts), 1)
            page.get_by_role("button", name="录入条款", exact=True).click()
            manual = page.get_by_role("textbox", name="要执行的要求", exact=True)
            manual.fill("人工核对的要求，不能被迟到提取覆盖")
            with page.expect_response("**/api/rules/parse") as response:
                delayed_posts[0].fulfill(status=202, json=job(posts[1]["request_id"], "succeeded"))
            response.value.finished()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            expect(manual).to_have_value("人工核对的要求，不能被迟到提取覆盖")
            expect(page.get_by_role("region", name="要求条款", exact=True).get_by_role("article")).to_have_count(1)
            expect(page.get_by_label("要求名称", exact=True)).to_have_value("待核对要求")
            self.assertEqual(len(posts), 2)
            self.assertTrue(all(method == "GET" for method, _identity in reads))
            self.assertEqual(writes, [("POST", "/rules/parse")])
            browser.close()

    def test_failed_task_can_be_retried_with_selected_type_without_changing_original(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page, status="failed")
            self.open_requirement(page)
            expect(page.get_by_role("alert").filter(has_text="要求提取失败")).to_be_visible()
            first = state["parses"][0]
            state["writes"].clear()
            page.get_by_label("适用资料类型").select_option("全功能检验报告")
            state["status"] = "succeeded"
            page.get_by_role("button", name="重新提取条款", exact=True).click()
            expect(page.get_by_label("要求名称", exact=True)).to_have_value("规则 A")
            expect(page.get_by_text("适用资料：全功能检验报告", exact=True)).to_be_visible()
            self.assertEqual(len(state["parses"]), 2)
            retried = state["parses"][1]
            self.assertNotEqual(retried["request_id"], first["request_id"])
            self.assertEqual(retried["source_file_id"], first["source_file_id"])
            self.assertEqual(retried["source_file_version_id"], first["source_file_version_id"])
            self.assertEqual(retried["document_type"], "全功能检验报告")
            self.assertEqual(state["writes"], [("POST", "/rules/parse")])
            self.assertEqual(state["saves"], [])
            browser.close()

    def test_save_success_clears_the_leave_guard_before_navigation(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            dialogs = []
            page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
            self.open_requirement(page)
            self.check_requirement(page)
            page.get_by_role("button", name="确认并保存版本", exact=True).click()
            expect(page.get_by_role("status").filter(has_text="版本 v1 已保存")).to_be_visible()
            self.assertEqual(len(state["saves"]), 1)
            published = state["saves"][0]
            self.assertEqual(published["title"], "已核对要求")
            self.assertEqual(published["body"]["rules"][0]["rule_to_check"], "检查 A")
            self.assertEqual(published["source_file_version_id"], state["source"]["id"])
            expect(page.get_by_label("要求名称", exact=True)).to_have_count(0)
            state["writes"].clear()
            page.get_by_role("link", name="返回审核要求", exact=True).click()
            expect(page.get_by_role("heading", name="审核要求", exact=True)).to_be_visible()
            page.get_by_role("link", name="已核对要求", exact=True).filter(visible=True).click()
            expect(page.get_by_role("heading", name="已核对要求", exact=True)).to_be_visible()
            page.reload()
            expect(page.get_by_role("heading", name="已核对要求", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="创建新版本", exact=True)).to_be_enabled()
            expect(page.get_by_role("dialog")).to_have_count(0)
            self.assertEqual(state["writes"], [])
            self.assertEqual(dialogs, [])
            browser.close()

    def requirement_routes(self, page, *, status="succeeded"):
        """Current requirement library protocol; only its external HTTP seam is controlled."""
        self.route(page)
        state = {"source": {}, "parses": [], "saves": [], "rules": {}, "writes": [], "held": [], "status": status, "save_status": 201}
        page.on("request", lambda request: state["writes"].append((request.method, urlsplit(request.url).path.removeprefix("/api"))) if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None)
        page.route("**/api/drafts?*", lambda route: route.fulfill(json={"drafts": [], "next_offset": None}))
        page.route("**/api/rounds?*", lambda route: route.fulfill(json={"rounds": [], "next_offset": None}))
        page.route("**/api/rule-types", lambda route: route.fulfill(json={"types": ["控制计划", "全功能检验报告"]}))

        def create_file(route):
            self.assertEqual(route.request.method, "POST")
            value = route.request.post_data_json
            state["source"].update(
                file_id=SOURCE,
                id=value["request_id"],
                original_filename=value["original_filename"],
                byte_size=value["byte_size"],
                content_type=value["content_type"],
                sha256=value["sha256"],
                status="staging",
            )
            route.fulfill(status=201, json=state["source"])

        def blob(route):
            self.assertEqual(route.request.method, "PUT")
            state["source"]["status"] = "ready"
            route.fulfill(json=state["source"])

        def rules(route):
            if route.request.method == "GET":
                route.fulfill(json={"rules": [{**rule, **{key: rule["body"][key] for key in ("rule_profile", "customers", "models", "parts")}} for rule in state["rules"].values()]})
            else:
                self.assertEqual(route.request.method, "POST")
                value = route.request.post_data_json
                state["saves"].append(value)
                if state["save_status"] >= 400:
                    route.fulfill(status=state["save_status"], json={"error": "save_failed"})
                else:
                    normalized = {**value["body"], **{key: [item.strip() for item in value["body"][key]] for key in ("customers", "models", "parts")}}
                    rule = {**value, "body": normalized, "id": value["request_id"], "kind": "engine", "type": value["body"]["type"], "source_file_id": SOURCE, "created_at": "2026-10-18T00:00:00Z"}
                    rule.update(state.get("reply_patch", {}))
                    state["rules"][rule["id"]] = rule
                    if state.get("drop_save_reply"):
                        route.abort("failed")
                    else:
                        route.fulfill(status=201, json=rule)

        def rule(route):
            self.assertEqual(route.request.method, "GET")
            found = state["rules"].get(urlsplit(route.request.url).path.rsplit("/", 1)[1])
            route.fulfill(json=found) if found else route.fulfill(status=404, json={"error": "missing"})

        def parse(route):
            self.assertEqual(route.request.method, "POST")
            value = route.request.post_data_json
            state["parses"].append(value)
            if state.pop("hold_next_parse", False):
                state["held"].append(route)
            else:
                route.fulfill(status=202, json=parse_job(value["request_id"], "queued"))

        def parse_job(identity, status):
            original = next(value for value in state["parses"] if value["request_id"] == identity)
            return {
                "id": identity,
                "status": status,
                "source_file_id": original["source_file_id"],
                "source_file_version_id": original["source_file_version_id"],
                "document_type": original["document_type"],
                "result": {"title": "规则 A", "body": {**BODY, "type": original["document_type"]}, "source_file_version_id": original["source_file_version_id"]}
                if status == "succeeded"
                else {"error": "rule_parse_failed"}
                if status == "failed"
                else None,
            }

        def read_parse(route):
            self.assertEqual(route.request.method, "GET")
            route.fulfill(json=parse_job(urlsplit(route.request.url).path.rsplit("/", 1)[1], state["status"]))

        page.route("**/api/files", create_file)
        page.route("**/api/files/*/versions/*", lambda route: route.fulfill(json=state["source"]))
        page.route("**/api/files/*/versions/*/blob", blob)
        page.route("**/api/rules", rules)
        page.route("**/api/rules/*", rule)
        page.route("**/api/rules/parse", parse)
        page.route("**/api/rules/parse/*", read_parse)
        return state

    def open_requirement(self, page):
        page.goto(WEB + "/?view=requirements")
        page.get_by_label("上传共用要求文件").set_input_files({"name": "A.json", "mimeType": "application/json", "buffer": b"{}"})
        page.get_by_label("适用资料类型").select_option("控制计划")
        page.get_by_role("button", name="提取条款", exact=True).click()

    def check_requirement(self, page):
        from playwright.sync_api import expect

        expect(page.get_by_label("要求名称", exact=True)).to_have_value("规则 A")
        page.get_by_label("要求名称", exact=True).fill("已核对要求")
        for label in ("不限定客户", "不限定零件", "不限定图纸修订"):
            page.get_by_label(label, exact=True).check()
        page.get_by_label("来源说明／变更原因", exact=True).fill("已按原件核对。")
        clause = page.get_by_role("article", name="条款 1", exact=True)
        clause.get_by_role("button", name="编辑条款", exact=True).click()
        clause.get_by_label("出处位置", exact=True).fill("原件第 1 条")
        clause.get_by_label("1 已核对来源", exact=True).check()
        clause.get_by_role("button", name="完成编辑", exact=True).click()
        page.get_by_label("已核对本版本的来源、范围和变更", exact=True).check()

    def test_save_failure_returns_to_the_editable_candidate(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            state["save_status"] = 500
            self.open_requirement(page)
            self.check_requirement(page)
            save = page.get_by_role("button", name="确认并保存版本", exact=True)
            save.click()
            expect(page.get_by_role("alert").filter(has_text="请求失败（500）")).to_be_visible()
            expect(save).to_be_enabled()
            expect(page.get_by_label("要求名称", exact=True)).to_be_enabled()
            expect(page.get_by_label("要求名称", exact=True)).to_have_value("已核对要求")
            expect(page.get_by_role("textbox", name="来源说明／变更原因", exact=True)).to_have_value("已按原件核对。")
            expect(page.get_by_label("1 已核对来源", exact=True)).to_be_checked()
            self.assertEqual(len(state["saves"]), 1)
            self.assertEqual(state["rules"], {})
            # A same-content retry must reuse the original publication identity.
            state["save_status"] = 201
            save.click()
            expect(page.get_by_role("status").filter(has_text="版本 v1 已保存")).to_be_visible()
            self.assertEqual(len(state["saves"]), 2)
            self.assertEqual(state["saves"][0], state["saves"][1])
            self.assertEqual(len(state["rules"]), 1)
            browser.close()

    def test_confirmed_abandon_leaves_without_a_second_prompt(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            dialogs = []

            def on_dialog(dialog):
                dialogs.append(dialog.type)
                dialog.dismiss()

            page.on("dialog", on_dialog)
            self.open_requirement(page)
            expect(page.get_by_label("要求名称", exact=True)).to_have_value("规则 A")
            state["writes"].clear()
            page.get_by_role("button", name="放弃草稿", exact=True).click()
            dialog = page.get_by_role("dialog", name="放弃这个版本草稿？", exact=True)
            expect(dialog).to_be_visible()
            dialog.get_by_role("button", name="继续编辑", exact=True).click()
            expect(dialog).to_have_count(0)
            expect(page.get_by_label("要求名称", exact=True)).to_have_value("规则 A")
            page.get_by_role("button", name="放弃草稿", exact=True).click()
            dialog.get_by_role("button", name="放弃此草稿", exact=True).click()
            expect(dialog).to_have_count(0)
            expect(page.get_by_role("heading", name="审核要求", exact=True)).to_be_visible()
            expect(page.get_by_role("heading", name="还没有审核要求", exact=True)).to_be_visible()
            page.reload()
            expect(page.get_by_role("heading", name="还没有审核要求", exact=True)).to_be_visible()
            self.assertEqual(state["writes"], [])
            self.assertEqual(state["saves"], [])
            self.assertEqual(dialogs, [])
            browser.close()

    def test_rule_publication_refuses_conflicting_frozen_snapshot(self):
        from playwright.sync_api import expect

        conflicts = [
            {"source_file_version_id": "00000000-0000-4000-8000-000000000099"},
            {"source_file_id": "00000000-0000-4000-8000-000000000099"},
            {"title": "另一份要求"},
            {"body": {**BODY, "rules": [{**BODY["rules"][0], "rule_to_check": "另一条要求"}]}},
            {"case_context": {}},
            {"predecessor_id": JOB},
        ]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for conflict in conflicts:
                with self.subTest(field=next(iter(conflict))):
                    page = browser.new_page()
                    state = self.requirement_routes(page)
                    state["reply_patch"] = conflict
                    self.open_requirement(page)
                    self.check_requirement(page)
                    page.get_by_role("button", name="确认并保存版本", exact=True).click()
                    expect(page.get_by_role("alert").filter(has_text="保存回执与本次要求不一致")).to_be_visible()
                    expect(page.get_by_role("status").filter(has_text="已保存")).to_have_count(0)
                    expect(page.get_by_label("要求名称", exact=True)).to_have_value("已核对要求")
                    expect(page.get_by_role("button", name="确认并保存版本", exact=True)).to_be_enabled()
                    self.assertEqual(len(state["saves"]), 1)
                    self.assertEqual(state["saves"][0]["source_file_version_id"], state["source"]["id"])
                    page.close()
            browser.close()

    def test_lost_rule_publication_reply_reads_the_original_request_without_reposting(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            state["drop_save_reply"] = True
            self.open_requirement(page)
            self.check_requirement(page)
            state["writes"].clear()
            page.get_by_role("button", name="确认并保存版本", exact=True).click()
            expect(page.get_by_role("status").filter(has_text="版本 v1 已保存")).to_be_visible()
            self.assertEqual(len(state["saves"]), 1)
            self.assertEqual(state["writes"], [("POST", "/rules")])
            expect(page.get_by_label("要求名称", exact=True)).to_have_count(0)
            self.assertIn("reqVersion=" + state["saves"][0]["request_id"], page.url)
            browser.close()

    def test_rule_publication_accepts_normalized_customer_and_part_metadata(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            self.open_requirement(page)
            self.check_requirement(page)
            page.get_by_label("不限定客户", exact=True).uncheck()
            page.get_by_label("适用客户", exact=True).fill("  测试客户  ")
            page.get_by_label("不限定零件", exact=True).uncheck()
            page.get_by_label("适用零件", exact=True).fill("  零件 P-1  ")
            page.get_by_label("已核对本版本的来源、范围和变更", exact=True).check()
            page.get_by_role("button", name="确认并保存版本", exact=True).click()
            expect(page.get_by_role("status").filter(has_text="版本 v1 已保存")).to_be_visible()
            self.assertEqual(len(state["saves"]), 1)
            self.assertEqual(state["saves"][0]["body"]["customers"], ["测试客户"])
            self.assertEqual(state["saves"][0]["body"]["parts"], ["零件 P-1"])
            browser.close()


if __name__ == "__main__":
    unittest.main()
