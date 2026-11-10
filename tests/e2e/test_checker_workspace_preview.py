"""Native UI + HTTP contract fixtures, not a claim of live model or PG acceptance."""

import json
import os
import re
import unittest
from types import SimpleNamespace
from urllib.parse import urlsplit

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")


class CheckerHttpFixture:
    def __init__(self):
        self.feedback = []
        self.operations = {}
        self.calls = []
        self.history = []
        self.current = "revision-base"
        self.head = 0
        self.model_config = {
            "provider": "responses-compatible",
            "base_url": "https://models.example.test",
            "model": "test-model",
            "configured": True,
            "revision": "7a7cceab-e503-477a-9874-d0300f7d2dcc",
            "updated_at": None,
        }
        self.lose_model_save = False
        self.model_error = None
        self.lose_feedback = False
        self.lose_optimization = False
        self.lose_publish = False
        self.drop_publish_before_write = False
        self.edits = 0
        self.tenant_override = {}
        self.lose_upgrade = False
        self.reject_upgrade = False
        self.reject_upgrade_verification = False
        self.latest_official = None
        self.seen_official = set()
        self.upgrades = {}
        self.revisions = {"revision-base": self.revision("revision-base", 1)}

    @staticmethod
    def revision(identity, version):
        return {
            "id": identity,
            "tenant_id": "tenant-a",
            "base_revision_id": "official-old",
            "official_base_revision_id": "official-old",
            "suite_key": "control-plan",
            "title": "控制计划检查",
            "version": version,
            "diff": [],
            "files": {"rules.json": json.dumps({"rules": [{"id": "U08-1", "rule_to_check": "需要签名"}]}), "templates.json": "[]", "checker.py": "def check(ctx): pass"},
            "changed_paths": [],
            "validation": {"status": "unverified", "total_samples": 1, "passed_samples": 1, "new_errors": 0, "reasons": ["insufficient_samples"]},
            "round_count": 1,
            "rounds": [],
        }

    def active(self):
        return {
            "suite_key": "control-plan",
            "current_revision_id": self.current,
            "revision": self.head,
            "history": self.history,
            "selection_mode": "pinned",
            "force_unverified": bool(self.history and self.history[-1]["force_unverified"]),
            "validation": self.revisions[self.current]["validation"],
            "latest_official_revision_id": self.latest_official,
            "latest_official_version": 4 if self.latest_official else None,
            "official_base_revision_id": "official-old",
            "official_update_available": bool(self.latest_official),
            "official_update_notice": bool(self.latest_official and self.latest_official not in self.seen_official),
        }

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path
        method = request.method
        body = request.post_data_json if request.post_data else None
        self.calls.append((method, path, body))
        response = None
        if path == "/me/model-config":
            if method == "PUT":
                if self.model_error:
                    route.fulfill(status=400, json={"error": self.model_error, "detail": body["api_key"]})
                    return
                self.model_config = {
                    "provider": "responses-compatible",
                    "base_url": body["base_url"],
                    "model": body["model"],
                    "configured": True,
                    "revision": "9376ad10-0771-4cad-9f52-d08483163387",
                    "updated_at": "2026-11-06T00:00:00Z",
                }
                if self.lose_model_save:
                    self.lose_model_save = False
                    route.abort("failed")
                    return
            elif method == "DELETE":
                self.model_config = {**self.model_config, "configured": False, "revision": None}
            response = self.model_config
        elif path == "/audit-rounds/round-a/checker-feedback":
            if method == "GET":
                response = {
                    "round_id": "round-a",
                    "feedback": self.feedback,
                    "sources": [{"file_version_id": "file-a", "suite_revision_id": "revision-base", "suite_key": "control-plan"}],
                    "optimizations": list(reversed(list(self.operations.values()))),
                }
            else:
                row = {**body, "id": "sample-a", "suite_revision_id": "revision-base", "suite_key": "control-plan", "round_id": "round-a", "revision": 1, "ordinal": 1}
                self.feedback.append(row)
                if self.lose_feedback:
                    self.lose_feedback = False
                    route.abort("failed")
                    return
                response = row
        elif path.endswith("/checker-feedback/sample-a"):
            if method == "PUT":
                assert body["expected_revision"] == self.feedback[0]["revision"]
                self.feedback[0] = {**self.feedback[0], **body, "revision": body["expected_revision"] + 1}
                response = self.feedback[0]
            elif method == "DELETE":
                response = {"id": "sample-a", "deleted": True, "revision": self.feedback[0]["revision"] + 1}
                self.feedback.clear()
        elif path.startswith("/suite-revisions/") and path.endswith("/checker-feedback"):
            response = {"feedback": self.feedback}
        elif path.startswith("/suite-revisions/") and path.endswith("/optimizations"):
            if body.get("consume_model") and body.get("expected_model_revision") != self.model_config["revision"]:
                route.fulfill(status=409, json={"error": "personal_model_configuration_changed"})
                return
            revision = path.split("/")[2]
            if revision == "revision-upgraded" and self.reject_upgrade_verification:
                route.fulfill(status=409, json={"error": "checker_feedback_rules_changed"})
                return
            candidate = "revision-upgrade-tested" if revision == "revision-upgraded" else "revision-candidate"
            state = "running" if body["consume_model"] else "saved"
            self.revisions[candidate] = self.revision(candidate, 2)
            if revision == "revision-upgraded":
                self.revisions[candidate]["base_revision_id"] = self.latest_official
                self.revisions[candidate]["official_base_revision_id"] = self.latest_official
            response = {
                "operation_id": body["operation_id"],
                "suite_revision_id": revision,
                "suite_key": "control-plan",
                "state": state,
                "candidate_revision_id": None if body["consume_model"] else candidate,
                "report": None if body["consume_model"] else {"verified": False, "reasons": ["tests_not_run"]},
                "usage": {"source": "controlled", "total_tokens": 0, "uncertain": False},
                "failure": None,
                "cancel_requested": False,
                "created_at": "2026-11-06T00:00:00Z",
            }
            self.operations[body["operation_id"]] = response
            if self.lose_optimization:
                self.lose_optimization = False
                route.abort("failed")
                return
        elif path.startswith("/checker-optimizations/"):
            response = self.operations.get(path.split("/")[2])
            if response is None:
                route.fulfill(status=404, json={"error": "checker_access_denied"})
                return
            if path.endswith("/stop"):
                response.update(state="stopped", cancel_requested=True)
        elif path == "/suites/control-plan/official-update/seen":
            assert body["official_revision_id"] == self.latest_official
            self.seen_official.add(body["official_revision_id"])
            response = self.active()
        elif path.startswith("/suites/control-plan/upgrades"):
            if method == "POST":
                if self.reject_upgrade:
                    route.fulfill(status=409, json={"error": "official_update_changed"})
                    return
                assert body["expected_revision"] == self.head
                assert body["target_revision_id"] == self.latest_official
                source = self.revisions[body["source_revision_id"]]
                candidate = self.revision("revision-upgraded", 4)
                candidate.update(base_revision_id=self.latest_official, official_base_revision_id=self.latest_official, upgrade=body)
                candidate["files"].update(source["files"])
                self.revisions[candidate["id"]] = candidate
                self.upgrades[body["operation_id"]] = candidate
                if self.lose_upgrade:
                    self.lose_upgrade = False
                    route.abort("failed")
                    return
                response = candidate
            else:
                response = self.upgrades.get(path.split("/")[-1])
        elif path == "/suites/control-plan/active":
            if method == "PUT":
                if self.drop_publish_before_write:
                    self.drop_publish_before_write = False
                    route.abort("failed")
                    return
                assert body["expected_revision"] == self.head
                self.head += 1
                response = {**body, "suite_key": "control-plan", "revision": self.head, "published_by": "alice", "published_by_name": "测试用户", "published_at": "2026-11-06T00:00:00Z"}
                self.current = body["revision_id"]
                self.history.append(response)
                if self.lose_publish:
                    self.lose_publish = False
                    route.abort("failed")
                    return
            else:
                response = self.active()
        elif path.startswith("/suite-revisions/"):
            revision = path.split("/")[2]
            if path.endswith("/candidates"):
                self.edits += 1
                candidate = self.revision("revision-edited" if self.edits == 1 else f"revision-edited-{self.edits}", 2 + self.edits)
                candidate["base_revision_id"] = revision
                candidate["files"].update(self.revisions[revision]["files"])
                candidate["files"].update(body["files"])
                candidate["diff"] = [{"path": key, "before": self.revisions[revision]["files"].get(key, ""), "after": value} for key, value in body["files"].items()]
                self.revisions[candidate["id"]] = candidate
                response = candidate
            else:
                response = self.revisions[revision]
                if revision in self.tenant_override:
                    response = {**response, "tenant_id": self.tenant_override[revision]}
        if response is None:
            route.fulfill(status=404, json={"error": "fixture_route_not_found"})
        else:
            route.fulfill(json=response)


def test_missing_preview_optimization_returns_the_product_not_found_receipt():
    fixture = CheckerHttpFixture()
    for method, suffix in (("GET", ""), ("POST", "/stop")):
        replies = []
        request = SimpleNamespace(url=f"http://preview.test/checker-optimizations/missing{suffix}", method=method, post_data=None, post_data_json=None)
        fixture.route(SimpleNamespace(request=request, fulfill=lambda **reply: replies.append(reply)))
        assert replies == [{"status": 404, "json": {"error": "checker_access_denied"}}]
        assert fixture.operations == {}


@unittest.skipUnless(PREVIEW and sync_playwright, "requires the explicit UI preview and Playwright")
class CheckerWorkspacePreviewTests(unittest.TestCase):
    def browser(self, playwright):
        executable = os.environ.get("PPAP_BROWSER_EXECUTABLE")
        return playwright.chromium.launch(**({"executable_path": executable} if executable else {}))

    def open(self, browser, fixture, width=1440):
        page = browser.new_page(viewport={"width": width, "height": 1000})
        page.set_default_timeout(7000)
        page.route(re.compile(r".*/(?:audit-rounds|checker-optimizations|suite-revisions|suites|me)/.*"), fixture.route)
        page.goto(PREVIEW + "/preview/checker.html")
        return page

    def answer(self, page):
        page.get_by_label("正确结论", exact=True).click()
        page.get_by_role("option", name="不符合", exact=True).click()
        page.get_by_text("添加我在原件中找到的依据", exact=True).click()
        page.get_by_label("工作表名称", exact=True).fill("控制计划")
        page.get_by_label("单元格（例如 B12）", exact=True).fill("B12")
        page.get_by_label("原文摘录（选填）", exact=True).fill("缺少签名")
        page.get_by_role("button", name="加入这处依据", exact=True).click()
        page.get_by_label("为什么应当这样判断", exact=True).fill("该单元格没有签名")
        page.get_by_role("button", name="加入待修复", exact=True).click()

    def test_pdf_original_region_backfills_feedback_and_page_change_clears_selection(self):
        import fitz

        document = fitz.open()
        for _ in range(2):
            sheet = document.new_page(width=240, height=320)
            sheet.insert_text((20, 30), "Original source")
            sheet.set_rotation(90)
        contents = document.tobytes()
        document.close()
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            page = self.open(browser, fixture)
            downloads = []

            def original(route):
                downloads.append(route.request.url)
                route.fulfill(body=contents, content_type="application/pdf")

            page.route("**/rounds/round-a/files/original-pdf/versions/file-pdf/download", original)
            page.get_by_label("正确结论", exact=True).click()
            page.get_by_role("option", name="不符合", exact=True).click()
            page.get_by_text("添加我在原件中找到的依据", exact=True).click()
            page.get_by_label("依据原件", exact=True).click()
            page.get_by_role("option", name="签署原件.pdf", exact=True).click()
            page.get_by_role("button", name="在原件上点选位置", exact=True).click()
            reader = page.get_by_role("region", name="原件阅读器", exact=True)
            canvas = reader.get_by_role("img", name="签署原件.pdf · 第 1 页", exact=True)
            expect(canvas).to_be_visible()
            bounds = canvas.bounding_box()
            page.mouse.move(bounds["x"] + bounds["width"] * 0.1, bounds["y"] + bounds["height"] * 0.2)
            page.mouse.down()
            page.mouse.move(bounds["x"] + bounds["width"] * 0.4, bounds["y"] + bounds["height"] * 0.6)
            page.mouse.up()
            expect(reader.get_by_role("button", name="引用选区", exact=True)).to_be_visible()
            reader.get_by_role("button", name="下一页", exact=True).click()
            expect(reader.get_by_role("img", name="签署原件.pdf · 第 2 页", exact=True)).to_be_visible()
            expect(reader.get_by_role("button", name="引用当前页", exact=True)).to_be_enabled()
            bounds = reader.get_by_role("img", name="签署原件.pdf · 第 2 页", exact=True).bounding_box()
            page.mouse.move(bounds["x"] + bounds["width"] * 0.1, bounds["y"] + bounds["height"] * 0.2)
            page.mouse.down()
            page.mouse.move(bounds["x"] + bounds["width"] * 0.4, bounds["y"] + bounds["height"] * 0.6)
            page.mouse.up()
            reader.get_by_role("button", name="引用选区", exact=True).click()
            expect(page.get_by_label("页码（从 1 开始）", exact=True)).to_have_value("2")
            expect(page.get_by_text("已从原件填入位置与选区，请确认后加入依据。", exact=True)).to_be_visible()
            self.assertFalse(fixture.feedback)
            page.get_by_role("button", name="加入这处依据", exact=True).click()
            page.get_by_label("为什么应当这样判断", exact=True).fill("已核对第二页签署区域。")
            page.get_by_role("button", name="加入待修复", exact=True).click()
            expect(page.get_by_text("已加入待修复。尚未调用模型，也没有改写本轮结果。", exact=True)).to_be_visible()
            citation = fixture.feedback[0]["evidence"][0]
            self.assertEqual(citation["file_version_id"], "file-pdf")
            self.assertEqual(citation["locator"]["page"], 2)
            for actual, expected in zip(citation["locator"]["region"], [48, 192, 144, 288], strict=True):
                self.assertAlmostEqual(actual, expected, delta=1)
            self.assertEqual(len(downloads), 1)
            self.assertFalse(fixture.operations)
            browser.close()

    def test_history_reads_its_revision_on_demand_and_keeps_failed_or_wrong_receipts_unknown(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            historical = fixture.revision("revision-historical", 0)
            historical["changed_paths"] = ["checker.py"]
            historical["diff"] = [{"path": "checker.py", "before": "old historical source", "after": "repaired historical source"}]
            fixture.revisions[historical["id"]] = historical
            fixture.history = [
                {"operation_id": "history-op", "revision_id": historical["id"], "revision": 1, "force_unverified": True, "published_at": "2026-11-06T00:00:00Z", "published_by_name": "历史发布人"}
            ]
            page = self.open(browser, fixture)
            release = page.get_by_role("region", name="检查发布与版本记录", exact=True)
            expect(release.get_by_text("控制计划检查 · 本企业版本 v1 · 当前使用", exact=True)).to_be_visible()
            path = "/suite-revisions/revision-historical"
            self.assertFalse(any(row[1] == path for row in fixture.calls))
            release.get_by_text("版本记录与受影响审核", exact=True).click()
            attempts = []

            def history_read(route):
                attempts.append(route.request.url)
                if len(attempts) == 1:
                    route.fulfill(status=503, json={"error": "unavailable"})
                elif len(attempts) == 2:
                    route.fulfill(json=fixture.revisions[fixture.current])
                else:
                    route.fulfill(json=historical)

            page.route("**" + path, history_read)
            release.get_by_role("button", name="本次修复内容", exact=True).click()
            expect(release.get_by_role("alert")).to_contain_text("无法读取这次修复内容")
            expect(release.get_by_text("修改后：repaired historical source", exact=True)).to_have_count(0)
            release.get_by_role("button", name="重新读取修复内容", exact=True).click()
            expect(release.get_by_role("alert")).to_contain_text("检查版本不属于当前工作区")
            release.get_by_role("button", name="重新读取修复内容", exact=True).click()
            expect(release.get_by_text("修改前：old historical source", exact=True)).to_be_visible()
            expect(release.get_by_text("修改后：repaired historical source", exact=True)).to_be_visible()
            self.assertEqual(len(attempts), 3)
            self.assertFalse(any(row[0] in {"POST", "PUT", "DELETE"} for row in fixture.calls))
            browser.close()

    def test_adopted_stale_version_can_be_explicitly_reaffirmed(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            fixture.revisions[fixture.current]["validation"].update(status="stale", reasons=["suite_inputs_changed"])
            page = self.open(browser, fixture)
            release = page.get_by_role("region", name="检查发布与版本记录", exact=True)
            release.get_by_role("button", name="核对并继续使用未验证版本", exact=True).click()
            confirmation = release.get_by_role("group", name="确认采用检查版本", exact=True)
            expect(confirmation.get_by_role("button", name="确认发布", exact=True)).to_be_disabled()
            confirmation.get_by_label("我已核对上述未验证项和新增错误，仍要启用", exact=True).check()
            expect(confirmation.get_by_role("button", name="确认发布", exact=True)).to_be_enabled()
            confirmation.get_by_role("button", name="确认发布", exact=True).click()
            expect(release.get_by_text("发布操作已完成", exact=True)).to_be_visible()
            self.assertEqual(len(fixture.history), 1)
            receipt = fixture.history[0]
            self.assertEqual(receipt["revision_id"], "revision-base")
            self.assertEqual(receipt["expected_revision"], 0)
            self.assertEqual(receipt["revision"], 1)
            self.assertTrue(receipt["force_unverified"])
            page.reload()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v1 · 当前使用", exact=True)).to_be_visible()
            self.assertEqual(len([row for row in fixture.calls if row[:2] == ("PUT", "/suites/control-plan/active")]), 1)
            browser.close()

    def test_custom_model_full_save_unknown_readback_and_destination_consent(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            page = self.open(browser, fixture, 390)
            self.answer(page)
            page.get_by_role("button", name="让 AI 修复并验证", exact=True).click()
            original_consent = page.get_by_label("同意将本次资料发送至 https://models.example.test，使用 test-model 修复并验证这 1 条反馈", exact=True)
            original_consent.check()
            expect(page.get_by_role("button", name="确认开始修复", exact=True)).to_be_enabled()
            # A saved model is a summary; changing it opens the form.
            page.get_by_role("button", name="更换模型或 Key", exact=True).click()
            page.get_by_label("服务地址", exact=True).fill("https://models.company.test/v1")
            page.get_by_label("模型名称", exact=True).fill("internal-reasoner")
            save = page.get_by_role("button", name="保存", exact=True)
            expect(save).to_be_disabled()
            expect(page.get_by_role("button", name="确认开始修复", exact=True)).to_be_disabled()
            page.get_by_label("API Key", exact=True).fill("synthetic-new-key")
            fixture.lose_model_save = True
            save.click()
            expect(page.get_by_text("保存结果待确认。密钥输入已清除，请读取当前配置后再决定是否修改。", exact=True)).to_be_visible()
            # Until the saved state is read back, the form (and its key) is gone and nothing can be saved again.
            expect(page.get_by_label("API Key", exact=True)).to_have_count(0)
            expect(save).to_have_count(0)
            self.assertEqual(len([row for row in fixture.calls if row[:2] == ("PUT", "/me/model-config")]), 1)
            self.assertFalse(fixture.operations)
            self.assertNotIn("synthetic-new-key", page.locator("body").inner_text())
            page.get_by_role("button", name="读取当前配置", exact=True).click()
            summary = page.get_by_role("region", name="我的模型配置", exact=True)
            expect(summary.get_by_text("internal-reasoner", exact=True)).to_be_visible()
            expect(summary.get_by_title("https://models.company.test/v1", exact=True)).to_be_visible()
            consent = page.get_by_label("同意将本次资料发送至 https://models.company.test/v1，使用 internal-reasoner 修复并验证这 1 条反馈", exact=True)
            expect(consent).not_to_be_checked()
            expect(page.get_by_role("button", name="确认开始修复", exact=True)).to_be_disabled()
            self.assertEqual(len([row for row in fixture.calls if row[:2] == ("PUT", "/me/model-config")]), 1)
            consent.check()
            expect(page.get_by_role("button", name="确认开始修复", exact=True)).to_be_enabled()
            page.get_by_role("button", name="撤销 Key", exact=True).click()
            page.get_by_role("button", name="取消", exact=True).click()
            self.assertTrue(fixture.model_config["configured"])
            self.assertFalse(any(row[:2] == ("DELETE", "/me/model-config") for row in fixture.calls))
            self.assertFalse(fixture.operations)
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            browser.close()

    def test_model_rejection_clears_secret_without_echoing_error_payload(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            fixture.model_error = "model_endpoint_not_allowed"
            page = self.open(browser, fixture)
            self.answer(page)
            page.get_by_role("button", name="让 AI 修复并验证", exact=True).click()
            page.get_by_role("button", name="更换模型或 Key", exact=True).click()
            page.get_by_label("服务地址", exact=True).fill("https://unapproved.test/v1")
            page.get_by_label("API Key", exact=True).fill("synthetic-secret-in-error")
            page.get_by_role("button", name="保存", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("这个服务地址尚未获准使用")
            # A rejection saved nothing: the form stays to correct it, with the key cleared.
            expect(page.get_by_label("API Key", exact=True)).to_have_value("")
            self.assertNotIn("synthetic-secret-in-error", page.locator("body").inner_text())
            expect(page.get_by_role("button", name="确认开始修复", exact=True)).to_be_disabled()
            self.assertFalse(fixture.operations)
            browser.close()

    def test_changed_model_before_admission_rejects_until_readback_and_new_consent(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            page = self.open(browser, fixture)
            self.answer(page)
            page.get_by_role("button", name="让 AI 修复并验证", exact=True).click()
            page.get_by_label("同意将本次资料发送至 https://models.example.test，使用 test-model 修复并验证这 1 条反馈", exact=True).check()
            original_revision = fixture.model_config["revision"]
            fixture.model_config = {**fixture.model_config, "revision": "588f07c7-cd41-4a1b-a80f-e264adfdb58b", "base_url": "https://models.company.test/v1", "model": "internal-reasoner"}
            pending_admission = []
            page.route("**/suite-revisions/*/optimizations", lambda route: pending_admission.append(route))
            with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/optimizations")):
                page.get_by_role("button", name="确认开始修复", exact=True).click()
            # Let the pending-intent render and its effects run while POST is held.
            # No missing-operation GET may race the authoritative rejection below.
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            self.assertIn("checkerOptimization=", page.url)
            self.assertEqual(len(pending_admission), 1)
            self.assertFalse(any(method == "GET" and path.startswith("/checker-optimizations/") for method, path, _ in fixture.calls))
            page.unroute("**/suite-revisions/*/optimizations")
            fixture.route(pending_admission.pop())
            expect(page.get_by_role("alert")).to_contain_text("模型配置已更改，本次操作未被接收")
            self.assertNotIn("checkerOptimization=", page.url)
            self.assertFalse(fixture.operations)
            attempts = [row for row in fixture.calls if row[0] == "POST" and row[1].endswith("/optimizations")]
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0][2]["expected_model_revision"], original_revision)
            expect(page.get_by_role("button", name="确认开始修复", exact=True)).to_be_disabled()
            page.get_by_role("button", name="读取当前配置", exact=True).click()
            consent = page.get_by_label("同意将本次资料发送至 https://models.company.test/v1，使用 internal-reasoner 修复并验证这 1 条反馈", exact=True)
            expect(consent).not_to_be_checked()
            expect(page.get_by_role("button", name="确认开始修复", exact=True)).to_be_disabled()
            consent.check()
            page.get_by_role("button", name="确认开始修复", exact=True).click()
            expect(page.get_by_role("heading", name="正在修复与验证", exact=True)).to_be_visible()
            attempts = [row for row in fixture.calls if row[0] == "POST" and row[1].endswith("/optimizations")]
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[1][2]["expected_model_revision"], fixture.model_config["revision"])
            browser.close()

    def test_admitted_optimization_keeps_polling_while_feedback_refresh_is_pending(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            page = self.open(browser, fixture)
            self.answer(page)
            page.get_by_role("button", name="让 AI 修复并验证", exact=True).click()
            page.get_by_label("同意将本次资料发送至 https://models.example.test，使用 test-model 修复并验证这 1 条反馈", exact=True).check()
            pending_refresh = []
            page.route("**/audit-rounds/round-a/checker-feedback", lambda route: pending_refresh.append(route))
            with (
                page.expect_request(lambda request: request.method == "GET" and request.url.endswith("/audit-rounds/round-a/checker-feedback")),
                page.expect_response(lambda response: response.request.method == "GET" and "/checker-optimizations/" in response.url) as first_read,
            ):
                page.get_by_role("button", name="确认开始修复", exact=True).click()
            operation = first_read.value.json()
            self.assertEqual(operation["state"], "running")
            self.assertEqual(len(pending_refresh), 1)
            self.assertEqual(pending_refresh[0].request.method, "GET")
            operation_id = operation["operation_id"]
            self.assertIn("checkerOptimization=" + operation_id, page.url)
            expect(page.get_by_role("heading", name="正在修复与验证", exact=True)).to_be_visible()
            with page.expect_response(lambda response: response.request.method == "GET" and response.url.endswith("/checker-optimizations/" + operation_id)):
                fixture.operations[operation_id]["state"] = "completed"
            expect(page.get_by_role("heading", name="修复结果", exact=True)).to_be_visible()
            self.assertEqual(len([row for row in fixture.calls if row[0] == "POST" and row[1].endswith("/optimizations")]), 1)
            self.assertEqual(len(pending_refresh), 1)
            page.unroute("**/audit-rounds/round-a/checker-feedback")
            fixture.route(pending_refresh.pop())
            expect(page.get_by_role("heading", name="修复结果", exact=True)).to_be_visible()
            browser.close()

    def test_unknown_optimization_admission_reads_same_operation_after_refresh(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            page = self.open(browser, fixture)
            self.answer(page)
            page.get_by_role("button", name="让 AI 修复并验证", exact=True).click()
            page.get_by_label("同意将本次资料发送至 https://models.example.test，使用 test-model 修复并验证这 1 条反馈", exact=True).check()
            fixture.lose_optimization = True
            page.get_by_role("button", name="确认开始修复", exact=True).click()
            expect(page.get_by_role("heading", name="正在修复与验证", exact=True)).to_be_visible()
            self.assertEqual(len(fixture.operations), 1)
            operation_id = next(iter(fixture.operations))
            self.assertIn("checkerOptimization=" + operation_id, page.url)
            page.reload()
            expect(page.get_by_role("heading", name="正在修复与验证", exact=True)).to_be_visible()
            self.assertEqual(page.get_by_role("region", name="修复与回归结果", exact=True).get_attribute("data-operation-id"), operation_id)
            self.assertEqual(len([row for row in fixture.calls if row[0] == "POST" and row[1].endswith("/optimizations")]), 1)
            self.assertTrue(any(row[:2] == ("GET", "/checker-optimizations/" + operation_id) for row in fixture.calls))
            browser.close()

    def test_mobile_manual_evidence_lost_feedback_edit_and_delete(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            fixture.lose_feedback = True
            page = self.open(browser, fixture, 390)
            self.answer(page)
            expect(page.get_by_text("样本保存结果尚未确定，请查看原操作，不要重复提交。", exact=True)).to_be_visible()
            page.reload()
            page.get_by_role("button", name="查看原操作", exact=True).click()
            expect(page.get_by_text("原反馈已保存。", exact=True)).to_be_visible()
            self.assertEqual(len([row for row in fixture.calls if row[:2] == ("POST", "/audit-rounds/round-a/checker-feedback")]), 1)
            page.get_by_text("查看、改错或删除样本", exact=True).click()
            page.get_by_role("button", name="改错", exact=True).click()
            page.get_by_label("正确结论", exact=True).click()
            page.get_by_role("option", name="符合", exact=True).click()
            page.get_by_label("为什么应当这样判断", exact=True).fill("重新核对后确认签名有效")
            with page.expect_response(lambda response: response.request.method == "GET" and urlsplit(response.url).path == "/suite-revisions/revision-base/checker-feedback"):
                page.get_by_role("button", name="保存正确答案修改", exact=True).click()
            expect(page.get_by_text("正确答案已修改。原审核未改写，之前的验证需要重新执行。", exact=True)).to_be_visible()
            self.assertEqual(fixture.feedback[0]["expected_status"], "PASS")
            samples = page.locator('[data-slot="collapsible"]').filter(has=page.get_by_text("查看、改错或删除样本", exact=True))
            expect(samples).to_contain_text("重新核对后确认签名有效")
            expect(samples).to_have_attribute("data-state", "open")
            page.get_by_role("button", name="删除样本", exact=True).click()
            page.get_by_role("button", name="确认删除样本", exact=True).click()
            expect(page.get_by_text("样本已删除，相关验证需要重新执行。已有报告不变。", exact=True)).to_be_visible()
            self.assertEqual(fixture.feedback, [])
            self.assertFalse(any("optimizations" in row[1] and row[0] == "POST" for row in fixture.calls))
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            browser.close()

    def test_explicit_model_consent_stop_save_rule_force_publish_recovery_and_reaudit(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            page = self.open(browser, fixture)
            self.answer(page)
            page.get_by_role("button", name="让 AI 修复并验证", exact=True).click()
            expect(page.get_by_role("button", name="确认开始修复", exact=True)).to_be_disabled()
            page.get_by_label("同意将本次资料发送至 https://models.example.test，使用 test-model 修复并验证这 1 条反馈", exact=True).check()
            page.get_by_role("button", name="确认开始修复", exact=True).click()
            expect(page.get_by_role("heading", name="正在修复与验证", exact=True)).to_be_visible()
            page.reload()
            expect(page.get_by_role("heading", name="正在修复与验证", exact=True)).to_be_visible()
            self.assertEqual(len([row for row in fixture.calls if row[0] == "POST" and row[1].endswith("/optimizations")]), 1)
            page.get_by_role("button", name="停止后续执行", exact=True).click()
            page.get_by_role("button", name="确认停止", exact=True).click()
            expect(page.get_by_role("heading", name="已停止", exact=True)).to_be_visible()
            page.get_by_role("button", name="仅保存待发布修改", exact=True).click()
            expect(page.get_by_role("heading", name="已保存，尚未验证", exact=True)).to_be_visible()
            self.assertFalse(list(fixture.operations.values())[-1]["report"]["verified"])
            page.get_by_text("单独修改规则或模板", exact=True).click()
            page.get_by_label("条款 U08-1 的规则原文", exact=True).fill("需要有效签名")
            page.get_by_role("button", name="保存修改", exact=True).click()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v3 · 未发布的修改", exact=True)).to_be_visible()
            fixture.lose_publish = True
            page.get_by_role("button", name="发布此修改", exact=True).click()
            page.get_by_label("我已核对上述未验证项和新增错误，仍要启用", exact=True).check()
            page.get_by_role("button", name="确认发布", exact=True).click()
            expect(page.get_by_text("发布结果尚未确定。请查看原操作，不要重复发布或回滚。", exact=True)).to_be_visible()
            page.reload()
            page.get_by_role("button", name="查看原发布操作", exact=True).click()
            expect(page.get_by_text("发布操作已完成", exact=True)).to_be_visible()
            page.get_by_role("button", name="用新版本重审这份文件", exact=True).click()
            expect(page.get_by_text("已进入现有显式重审入口", exact=True)).to_be_visible()
            self.assertEqual(len([row for row in fixture.calls if row[:2] == ("PUT", "/suites/control-plan/active")]), 1)
            browser.close()

    def open_upgrade(self, page, fixture):
        fixture.latest_official = "official-new"
        self.answer(page)
        expect(page.get_by_role("button", name="升级并验证", exact=True)).to_be_enabled()

    def test_official_notice_once_and_upgrade_tests_selected_edits_without_publish_or_model(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            fixture.latest_official = "official-new"
            page = self.open(browser, fixture, 390)
            expect(page.get_by_text("官方检查有新版本", exact=True)).to_be_visible()
            self.answer(page)
            expect(page.get_by_role("button", name="升级并验证", exact=True)).to_be_enabled()
            page.reload()
            expect(page.get_by_role("button", name="升级并验证", exact=True)).to_be_enabled()
            expect(page.get_by_text("官方检查有新版本", exact=True)).not_to_be_visible()
            self.assertEqual(len([row for row in fixture.calls if row[1].endswith("/official-update/seen")]), 1)
            page.get_by_text("单独修改规则或模板", exact=True).click()
            page.get_by_label("条款 U08-1 的规则原文", exact=True).fill("保留企业有效签名要求")
            expect(page.get_by_role("button", name="升级并验证", exact=True)).to_be_disabled()
            expect(page.get_by_text("先保存当前修改再升级；保存结果未确认时，请先查看原保存操作。", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="保存修改", exact=True)).to_be_enabled()
            page.get_by_role("button", name="保存修改", exact=True).click()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v3 · 未发布的修改", exact=True)).to_be_visible()
            page.get_by_role("button", name="升级并验证", exact=True).click()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v2 · 未发布的修改", exact=True)).to_be_visible()
            writes = [row for row in fixture.calls if row[0] == "POST"]
            upgrade = next(row for row in writes if row[1] == "/suites/control-plan/upgrades")
            self.assertEqual(upgrade[2]["source_revision_id"], "revision-edited")
            self.assertEqual(upgrade[2]["target_revision_id"], "official-new")
            self.assertEqual(upgrade[2]["expected_revision"], 0)
            verification = next(row for row in writes if row[1] == "/suite-revisions/revision-upgraded/optimizations")
            self.assertEqual(verification[2]["action"], "run_tests")
            self.assertFalse(verification[2]["consume_model"])
            self.assertEqual(verification[2]["feedback_ids"], ["sample-a"])
            self.assertIn("保留企业有效签名要求", fixture.revisions["revision-upgraded"]["files"]["rules.json"])
            self.assertEqual(fixture.history, [])
            self.assertEqual(fixture.current, "revision-base")
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            browser.close()

    def test_unknown_upgrade_refresh_is_read_only_and_verification_requires_another_click(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            fixture.lose_upgrade = True
            page = self.open(browser, fixture)
            self.open_upgrade(page, fixture)
            page.get_by_role("button", name="升级并验证", exact=True).click()
            expect(page.get_by_text("升级结果尚未确定。请查看原升级操作，不要再次升级。", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="发布此修改", exact=True)).not_to_be_visible()
            expect(page.get_by_role("button", name="重新运行验证样本", exact=True)).to_be_disabled()
            expect(page.get_by_role("button", name="仅保存待发布修改", exact=True)).to_be_disabled()
            expect(page.get_by_role("button", name="只运行现有检查验证", exact=True)).to_be_disabled()
            self.assertIn("checkerUpgrade=", page.url)
            page.reload()
            page.get_by_role("button", name="查看原升级操作", exact=True).click()
            expect(page.get_by_text("已找回升级候选，尚未自动运行样本。请重新运行验证样本后核对结果。", exact=True)).to_be_visible()
            self.assertNotIn("checkerUpgrade=", page.url)
            self.assertEqual(len([row for row in fixture.calls if row[:2] == ("POST", "/suites/control-plan/upgrades")]), 1)
            self.assertFalse(any(row[:2] == ("POST", "/suite-revisions/revision-upgraded/optimizations") for row in fixture.calls))
            expect(page.get_by_role("button", name="重新运行验证样本", exact=True)).to_be_enabled()
            page.get_by_role("button", name="重新运行验证样本", exact=True).click()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v2 · 未发布的修改", exact=True)).to_be_visible()
            self.assertEqual(len([row for row in fixture.calls if row[:2] == ("POST", "/suite-revisions/revision-upgraded/optimizations")]), 1)
            browser.close()

    def test_changed_official_target_rejects_upgrade_and_new_notice_is_per_revision(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            fixture.reject_upgrade = True
            page = self.open(browser, fixture)
            self.open_upgrade(page, fixture)
            page.get_by_role("button", name="升级并验证", exact=True).click()
            expect(page.get_by_role("button", name="刷新后核对升级", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="升级并验证", exact=True)).not_to_be_visible()
            self.assertNotIn("checkerUpgrade=", page.url)
            self.assertEqual(fixture.upgrades, {})
            fixture.latest_official = "official-next"
            fixture.reject_upgrade = False
            page.get_by_role("button", name="刷新后核对升级", exact=True).click()
            expect(page.get_by_text("官方检查有新版本", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="升级并验证", exact=True)).to_be_enabled()
            page.reload()
            expect(page.get_by_role("button", name="升级并验证", exact=True)).to_be_enabled()
            expect(page.get_by_text("官方检查有新版本", exact=True)).not_to_be_visible()
            seen = [row[2]["official_revision_id"] for row in fixture.calls if row[1].endswith("/official-update/seen")]
            self.assertEqual(seen, ["official-new", "official-next"])
            browser.close()

    def test_upgrade_candidate_stays_selected_when_verification_rejects(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            fixture.reject_upgrade_verification = True
            page = self.open(browser, fixture)
            self.open_upgrade(page, fixture)
            page.get_by_role("button", name="升级并验证", exact=True).click()
            expect(page.get_by_text("原反馈中的条款已变更，请先核对正确答案。", exact=True)).to_be_visible()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v4 · 未发布的修改", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="重新运行验证样本", exact=True)).to_be_enabled()
            page.reload()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v4 · 未发布的修改", exact=True)).to_be_visible()
            self.assertEqual(len([row for row in fixture.calls if row[:2] == ("POST", "/suites/control-plan/upgrades")]), 1)
            self.assertEqual(len([row for row in fixture.calls if row[:2] == ("POST", "/suite-revisions/revision-upgraded/optimizations")]), 1)
            self.assertEqual(fixture.history, [])
            browser.close()

    def test_official_update_is_visible_without_feedback_or_optimization_candidate(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            fixture.latest_official = "official-new"
            page = self.open(browser, fixture)
            expect(page.get_by_text("官方检查有新版本", exact=True)).to_be_visible()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v1 · 当前使用", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="升级并验证", exact=True)).to_be_disabled()
            expect(page.get_by_text("尚无验证样本。可以查看版本和官方更新；添加正确答案后才能升级并验证。", exact=True)).to_be_visible()
            self.assertEqual(fixture.feedback, [])
            self.assertEqual(fixture.operations, {})
            self.assertEqual(fixture.upgrades, {})
            self.assertFalse(any(row[0] == "POST" and row[1].endswith(("/candidates", "/optimizations", "/upgrades")) for row in fixture.calls))
            self.answer(page)
            expect(page.get_by_role("button", name="升级并验证", exact=True)).to_be_enabled()
            page.get_by_role("button", name="升级并验证", exact=True).click()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v2 · 未发布的修改", exact=True)).to_be_visible()
            upgrades = [row for row in fixture.calls if row[:2] == ("POST", "/suites/control-plan/upgrades")]
            self.assertEqual(upgrades[0][2]["source_revision_id"], "revision-base")
            self.assertFalse(any(row[0] == "POST" and row[1].endswith("/candidates") for row in fixture.calls))
            self.assertEqual(fixture.history, [])
            browser.close()

    def test_historical_entry_upgrades_current_enterprise_version_not_old_report_source(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            fixture.latest_official = "official-new"
            fixture.current = "revision-current"
            fixture.revisions[fixture.current] = fixture.revision(fixture.current, 3)
            fixture.revisions[fixture.current]["files"]["checker.py"] = "# current enterprise change"
            fixture.head = 2
            page = self.open(browser, fixture)
            expect(page.get_by_text("控制计划检查 · 本企业版本 v3 · 当前使用", exact=True)).to_be_visible()
            self.answer(page)
            page.get_by_role("button", name="升级并验证", exact=True).click()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v2 · 未发布的修改", exact=True)).to_be_visible()
            upgrade = next(row[2] for row in fixture.calls if row[:2] == ("POST", "/suites/control-plan/upgrades"))
            self.assertEqual(upgrade["source_revision_id"], "revision-current")
            self.assertEqual(upgrade["expected_revision"], 2)
            self.assertEqual(fixture.revisions["revision-upgraded"]["files"]["checker.py"], "# current enterprise change")
            self.assertEqual(fixture.current, "revision-current")
            browser.close()

    def test_two_manual_saves_restore_latest_candidate_and_unknown_publication_without_server_write(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            fixture.current = "revision-current"
            fixture.head = 2
            fixture.revisions[fixture.current] = fixture.revision(fixture.current, 2)
            page = self.open(browser, fixture)
            expect(page.get_by_text("控制计划检查 · 本企业版本 v2 · 当前使用", exact=True)).to_be_visible()
            for version, text in [(3, "第一次保存的企业修改"), (4, "第二次保存的企业修改")]:
                page.get_by_text("单独修改规则或模板", exact=True).click()
                page.get_by_label("条款 U08-1 的规则原文", exact=True).fill(text)
                page.get_by_role("button", name="保存修改", exact=True).click()
                expect(page.get_by_text(f"控制计划检查 · 本企业版本 v{version} · 未发布的修改", exact=True)).to_be_visible()
            self.assertNotIn("checkerDetailRoot=", page.url)
            self.assertIn("checkerDetailScope=", page.url)
            page.reload()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v4 · 未发布的修改", exact=True)).to_be_visible()
            page.get_by_text("单独修改规则或模板", exact=True).click()
            expect(page.get_by_label("条款 U08-1 的规则原文", exact=True)).to_have_value("第二次保存的企业修改")
            self.assertEqual(fixture.edits, 2)
            fixture.drop_publish_before_write = True
            page.get_by_role("button", name="发布此修改", exact=True).click()
            page.get_by_label("我已核对上述未验证项和新增错误，仍要启用", exact=True).check()
            page.get_by_role("button", name="确认发布", exact=True).click()
            expect(page.get_by_text("发布结果尚未确定。请查看原操作，不要重复发布或回滚。", exact=True)).to_be_visible()
            self.assertEqual(fixture.history, [])
            self.assertEqual(fixture.current, "revision-current")
            page.reload()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v4 · 未发布的修改", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="发布此修改", exact=True)).to_be_disabled()
            expect(page.get_by_role("button", name="重新运行验证样本", exact=True)).to_be_disabled()
            page.get_by_role("button", name="查看原发布操作", exact=True).click()
            expect(page.get_by_text("尚未读到原发布结果。不要重复发布，可以稍后继续读取。", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="发布此修改", exact=True)).to_be_disabled()
            self.assertEqual(len([row for row in fixture.calls if row[:2] == ("PUT", "/suites/control-plan/active")]), 1)
            self.assertEqual(fixture.edits, 2)
            self.assertEqual(fixture.history, [])
            browser.close()

    def test_auto_discovered_operation_preserves_manual_candidate_until_same_operation_is_selected(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            page = self.open(browser, fixture)
            self.answer(page)
            for version, text in [(3, "第一次保存的企业修改"), (4, "第二次保存的企业修改")]:
                page.get_by_text("单独修改规则或模板", exact=True).click()
                page.get_by_label("条款 U08-1 的规则原文", exact=True).fill(text)
                page.get_by_role("button", name="保存修改", exact=True).click()
                expect(page.get_by_text(f"控制计划检查 · 本企业版本 v{version} · 未发布的修改", exact=True)).to_be_visible()
            self.assertNotIn("checkerOptimization=", page.url)
            # The same user finished another operation in a different window.
            fixture.revisions["revision-other-window"] = fixture.revision("revision-other-window", 9)
            fixture.operations["operation-other-window"] = {
                "operation_id": "operation-other-window",
                "suite_revision_id": "revision-base",
                "suite_key": "control-plan",
                "state": "saved",
                "candidate_revision_id": "revision-other-window",
                "report": {"verified": False, "reasons": ["tests_not_run"]},
                "usage": {"source": "controlled", "total_tokens": 0, "uncertain": False},
                "failure": None,
                "cancel_requested": False,
                "created_at": "2026-11-06T00:00:00Z",
            }
            with page.expect_response(lambda response: urlsplit(response.url).path == "/checker-optimizations/operation-other-window"):
                page.reload()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v4 · 未发布的修改", exact=True)).to_be_visible()
            page.get_by_text("单独修改规则或模板", exact=True).click()
            expect(page.get_by_label("条款 U08-1 的规则原文", exact=True)).to_have_value("第二次保存的企业修改")
            page.get_by_text("之前的修复记录", exact=True).click()
            # Choosing the already displayed operation must still rerender its explicit intent.
            page.get_by_role("button", name=re.compile("查看待验证修改$")).click()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v9 · 未发布的修改", exact=True)).to_be_visible()
            self.assertIn("checkerOptimization=operation-other-window", page.url)
            self.assertEqual(fixture.edits, 2)
            self.assertFalse(any(row[0] == "POST" and row[1].endswith("/optimizations") for row in fixture.calls))
            self.assertEqual(fixture.history, [])
            browser.close()

    def test_late_feedback_readback_keeps_open_rule_draft_until_save_and_revision_switch_clears_it(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            official = fixture.revision("revision-official", 5)
            official["tenant_id"] = None
            official["files"]["rules.json"] = json.dumps({"rules": [{"id": "U08-1", "rule_to_check": "官方冻结原文"}]}, ensure_ascii=False)
            foreign = fixture.revision("revision-foreign", 6)
            foreign["suite_key"] = "other-suite"
            foreign["tenant_id"] = "tenant-b"
            foreign["title"] = "其他套件检查"
            foreign["files"]["rules.json"] = json.dumps({"rules": [{"id": "U08-1", "rule_to_check": "越权套件原文"}]}, ensure_ascii=False)
            fixture.revisions[official["id"]] = official
            fixture.revisions[foreign["id"]] = foreign
            page = self.open(browser, fixture)
            release = page.get_by_role("region", name="检查发布与版本记录", exact=True)
            expect(release.get_by_text("控制计划检查 · 本企业版本 v1 · 当前使用", exact=True)).to_be_visible()
            expect(page.get_by_text("尚无验证样本。可以查看版本和官方更新；添加正确答案后才能升级并验证。", exact=True)).to_be_visible()
            held = []
            page.route("**/audit-rounds/round-a/checker-feedback", lambda route: held.append(route) if route.request.method == "GET" else fixture.route(route))
            with page.expect_request(lambda request: request.method == "GET" and urlsplit(request.url).path == "/audit-rounds/round-a/checker-feedback"):
                self.answer(page)
            # Let the held-route callback run. expect_request returns before that sync handler is dispatched.
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            self.assertEqual([(route.request.method, urlsplit(route.request.url).path) for route in held], [("GET", "/audit-rounds/round-a/checker-feedback")])
            draft = "迟到读回前手写的规则"
            editor = page.locator('[data-slot="collapsible"]').filter(has=page.get_by_text("单独修改规则或模板", exact=True))
            page.get_by_text("单独修改规则或模板", exact=True).click()
            field = page.get_by_label("条款 U08-1 的规则原文", exact=True)
            expect(field).to_be_visible()
            field.fill(draft)
            expect(field).to_have_value(draft)
            # The feedback GET that follows “加入待修复” is still outstanding. Release it only after the draft exists.
            fixture.history.extend(
                [
                    {
                        "operation_id": "pub-official",
                        "suite_key": "control-plan",
                        "revision_id": official["id"],
                        "revision": 1,
                        "force_unverified": False,
                        "published_at": "2026-11-01T00:00:00Z",
                        "published_by_name": "官方冻结",
                    },
                    {
                        "operation_id": "pub-foreign",
                        "suite_key": "control-plan",
                        "revision_id": foreign["id"],
                        "revision": 2,
                        "force_unverified": False,
                        "published_at": "2026-11-02T00:00:00Z",
                        "published_by_name": "越权版本",
                    },
                ]
            )
            page.unroute("**/audit-rounds/round-a/checker-feedback")
            fixture.route(held.pop())
            expect(page.get_by_text("已确认的样本 1 条", exact=True)).to_be_visible()
            expect(editor).to_have_attribute("data-state", "open")
            expect(field).to_be_visible()
            expect(field).to_be_editable()
            expect(field).to_have_value(draft)
            expect(release.get_by_text("控制计划检查 · 本企业版本 v1 · 当前使用", exact=True)).to_be_visible()
            self.assertEqual(fixture.edits, 0)
            self.assertFalse(any(row[0] == "POST" and row[1].endswith("/candidates") for row in fixture.calls))
            page.get_by_role("button", name="保存修改", exact=True).click()
            expect(release.get_by_text("控制计划检查 · 本企业版本 v3 · 未发布的修改", exact=True)).to_be_visible()
            self.assertEqual(fixture.edits, 1)
            self.assertEqual(json.loads(fixture.revisions["revision-edited"]["files"]["rules.json"])["rules"][0]["rule_to_check"], draft)
            self.assertEqual(json.loads(fixture.revisions["revision-base"]["files"]["rules.json"])["rules"][0]["rule_to_check"], "需要签名")
            page.get_by_text("单独修改规则或模板", exact=True).click()
            expect(field).to_be_visible()
            expect(field).to_have_value(draft)
            release.get_by_text("版本记录与受影响审核", exact=True).click()
            release.locator("div.border-t").filter(has_text="发布人 官方冻结").get_by_role("button", name="查看此版本／回滚", exact=True).click()
            expect(release.get_by_text("控制计划检查 · 官方版本 v5 · 历史版本", exact=True)).to_be_visible()
            expect(field).not_to_be_visible()
            page.get_by_text("单独修改规则或模板", exact=True).click()
            expect(field).to_be_editable()
            expect(field).to_have_value("官方冻结原文")
            self.assertNotIn(draft, field.input_value())
            self.assertEqual(fixture.edits, 1)
            self.assertIsNone(fixture.revisions["revision-official"]["tenant_id"])
            fixture.tenant_override["revision-official"] = "tenant-b"
            release.get_by_role("button", name="读取最新版本", exact=True).click()
            expect(release.get_by_role("alert")).to_contain_text("检查版本不属于当前工作区，请核对原版本。")
            expect(release.get_by_text("控制计划检查 · 官方版本 v5 · 历史版本", exact=True)).to_be_visible()
            expect(field).to_have_value("官方冻结原文")
            self.assertEqual(fixture.edits, 1)
            self.assertIsNone(fixture.revisions["revision-official"]["tenant_id"])
            # Switching revisions remounts this disclosure closed. Open it again for the other history row.
            release.get_by_text("版本记录与受影响审核", exact=True).click()
            release.locator("div.border-t").filter(has_text="发布人 越权版本").get_by_role("button", name="查看此版本／回滚", exact=True).click()
            expect(release.get_by_role("alert")).to_contain_text("检查版本不属于当前工作区，请核对原版本。")
            expect(page.get_by_text("越权套件原文", exact=True)).to_have_count(0)
            expect(page.get_by_text("其他套件检查", exact=True)).to_have_count(0)
            self.assertEqual(fixture.edits, 1)
            self.assertFalse(any(row[:2] == ("PUT", "/suites/control-plan/active") for row in fixture.calls))
            self.assertEqual(json.loads(fixture.revisions["revision-base"]["files"]["rules.json"])["rules"][0]["rule_to_check"], "需要签名")
            self.assertEqual(json.loads(official["files"]["rules.json"])["rules"][0]["rule_to_check"], "官方冻结原文")
            browser.close()

    def test_failed_authoritative_refetch_retries_without_losing_dirty_draft(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            page = self.open(browser, fixture)
            self.answer(page)
            release = page.get_by_role("region", name="检查发布与版本记录", exact=True)
            save = release.get_by_role("button", name="保存修改", exact=True)
            page.get_by_text("单独修改规则或模板", exact=True).click()
            expect(save).to_be_enabled()
            field = page.get_by_label("条款 U08-1 的规则原文", exact=True)
            draft = "读取失败后仍需保存的原草稿"
            field.fill(draft)
            held = []
            page.route("**/suite-revisions/revision-base", lambda route: held.append(route))
            fixture.feedback[0]["revision"] += 1
            with page.expect_request(lambda request: urlsplit(request.url).path == "/suite-revisions/revision-base"):
                page.get_by_role("button", name="读取最新记录", exact=True).click()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            self.assertEqual(len(held), 1)
            self.assertEqual(held[0].request.method, "GET")
            expect(save).to_be_disabled()
            expect(field).to_be_editable()
            held.pop().fulfill(status=503, json={"error": "controlled_revision_read_failed"})
            expect(release.get_by_role("alert")).to_contain_text("审核服务暂时不可用，请稍后重试。")
            expect(field).to_have_value(draft)
            retry = release.get_by_role("button", name="读取最新版本", exact=True)
            expect(retry).to_be_enabled()
            expect(save).to_be_disabled()
            calls_before_retry = len(fixture.calls)
            # A concurrent enterprise-head change must not replace the frozen base of this dirty draft.
            fixture.revisions["revision-new-current"] = fixture.revision("revision-new-current", 8)
            fixture.current = "revision-new-current"
            with page.expect_request(lambda request: urlsplit(request.url).path == "/suite-revisions/revision-base"):
                retry.click()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            self.assertEqual(len(held), 1)
            expect(retry).to_be_disabled()
            expect(save).to_be_disabled()
            expect(field).to_have_value(draft)
            self.assertEqual(fixture.edits, 0)
            fixture.route(held.pop())
            expect(save).to_be_enabled()
            expect(field).to_be_editable()
            expect(field).to_have_value(draft)
            expect(release.get_by_text("控制计划检查 · 本企业版本 v1 · 未发布的修改", exact=True)).to_be_visible()
            self.assertTrue(all(method == "GET" for method, _, _ in fixture.calls[calls_before_retry:]))
            self.assertFalse(any(path == "/suite-revisions/revision-new-current" for _, path, _ in fixture.calls))
            save.click()
            expect(release.get_by_text("控制计划检查 · 本企业版本 v3 · 未发布的修改", exact=True)).to_be_visible()
            self.assertEqual(fixture.edits, 1)
            self.assertEqual(fixture.revisions["revision-edited"]["base_revision_id"], "revision-base")
            self.assertEqual(json.loads(fixture.revisions["revision-edited"]["files"]["rules.json"])["rules"][0]["rule_to_check"], draft)
            self.assertEqual(json.loads(fixture.revisions["revision-base"]["files"]["rules.json"])["rules"][0]["rule_to_check"], "需要签名")
            self.assertFalse(fixture.operations)
            self.assertEqual(fixture.history, [])
            browser.close()

    def test_authoritative_refetch_fences_parent_optimizer_even_after_new_consent(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            # A previous failed operation supplies a real operation-selection action, without a model call.
            fixture.operations["prior-failed"] = {
                "operation_id": "prior-failed",
                "suite_revision_id": "revision-base",
                "suite_key": "control-plan",
                "state": "failed",
                "candidate_revision_id": None,
                "report": None,
                "usage": {"source": "controlled", "total_tokens": 0, "uncertain": False},
                "failure": None,
                "cancel_requested": False,
                "created_at": "2026-11-06T00:00:00Z",
            }
            page = self.open(browser, fixture)
            self.answer(page)
            release = page.get_by_role("region", name="检查发布与版本记录", exact=True)
            page.get_by_text("之前的修复记录", exact=True).click()
            prior = page.get_by_role("button", name=re.compile("查看(?:失败记录|待验证修改)$"))
            prior.click()
            expect(page.get_by_role("region", name="修复与回归结果", exact=True)).to_have_attribute("data-operation-id", "prior-failed")
            page.get_by_role("button", name="让 AI 修复并验证", exact=True).click()
            consent = page.get_by_label("同意将本次资料发送至 https://models.example.test，使用 test-model 修复并验证这 1 条反馈", exact=True)
            consent.check()
            start = page.get_by_role("button", name="确认开始修复", exact=True)
            expect(start).to_be_enabled()
            expect(prior).to_be_enabled()
            original_url = page.url
            writes_before = [row for row in fixture.calls if row[0] != "GET"]
            held = []
            page.route("**/suite-revisions/revision-base", lambda route: held.append(route))
            fixture.feedback[0]["revision"] += 1
            with page.expect_request(lambda request: urlsplit(request.url).path == "/suite-revisions/revision-base"):
                page.get_by_role("button", name="读取最新记录", exact=True).click()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            self.assertEqual(len(held), 1)
            expect(consent).not_to_be_checked()
            consent.check()
            expect(consent).to_be_checked()
            expect(start).to_be_disabled()
            expect(prior).to_be_disabled()
            expect(page.get_by_role("button", name="只运行现有检查验证", exact=True)).to_be_disabled()
            expect(release.get_by_text("控制计划检查 · 本企业版本 v1 · 当前使用", exact=True)).to_be_visible()
            held.pop().fulfill(status=503, json={"error": "controlled_revision_read_failed"})
            expect(release.get_by_role("alert")).to_contain_text("审核服务暂时不可用，请稍后重试。")
            expect(start).to_be_disabled()
            expect(prior).to_be_disabled()
            # A newly read operation receipt must also wait for authoritative version recovery.
            fixture.revisions["revision-other-window"] = fixture.revision("revision-other-window", 9)
            fixture.operations["prior-failed"].update(state="saved", candidate_revision_id="revision-other-window", report={"verified": False, "reasons": ["tests_not_run"]})
            page.get_by_role("button", name="读取最新记录", exact=True).click()
            expect(page.get_by_role("region", name="修复与回归结果", exact=True)).to_have_attribute("data-candidate-revision", "revision-other-window")
            expect(release.get_by_text("控制计划检查 · 本企业版本 v1 · 当前使用", exact=True)).to_be_visible()
            self.assertEqual(page.url, original_url)
            self.assertFalse(any(path == "/suite-revisions/revision-other-window" for _, path, _ in fixture.calls))
            with page.expect_request(lambda request: urlsplit(request.url).path == "/suite-revisions/revision-base"):
                release.get_by_role("button", name="读取最新版本", exact=True).click()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            self.assertEqual(len(held), 1)
            expect(start).to_be_disabled()
            expect(release.get_by_text("控制计划检查 · 本企业版本 v1 · 当前使用", exact=True)).to_be_visible()
            fixture.route(held.pop())
            expect(release.get_by_text("控制计划检查 · 本企业版本 v9 · 未发布的修改", exact=True)).to_be_visible()
            # Selecting the read-back candidate resets consent; it still does not start a model call.
            expect(consent).not_to_be_checked()
            expect(start).to_be_disabled()
            consent.check()
            expect(start).to_be_enabled()
            expect(prior).to_be_enabled()
            self.assertIn("checkerOptimization=prior-failed", page.url)
            self.assertEqual(list(fixture.operations), ["prior-failed"])
            self.assertEqual(fixture.edits, 0)
            self.assertEqual(fixture.history, [])
            self.assertEqual([row for row in fixture.calls if row[0] != "GET"], writes_before)
            self.assertFalse(any(method == "POST" and path.endswith("/optimizations") for method, path, _ in fixture.calls))
            browser.close()

    def test_revision_retry_cannot_bypass_pending_or_unknown_child_save(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            page = self.open(browser, fixture)
            self.answer(page)
            release = page.get_by_role("region", name="检查发布与版本记录", exact=True)
            page.get_by_text("单独修改规则或模板", exact=True).click()
            expect(release.get_by_role("button", name="保存修改", exact=True)).to_be_enabled()
            field = page.get_by_label("条款 U08-1 的规则原文", exact=True)
            field.fill("保存结果未知时只读原操作")
            held = []
            page.route("**/suite-revisions/revision-base/candidates", lambda route: held.append(route))
            with page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == "/suite-revisions/revision-base/candidates"):
                release.get_by_role("button", name="保存修改", exact=True).click()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            self.assertEqual(len(held), 1)
            retry = release.get_by_role("button", name="读取最新版本", exact=True)
            expect(retry).to_be_disabled()
            page.route("**/suite-revisions/revision-base", lambda route: route.fulfill(status=503, json={"error": "controlled_revision_read_failed"}))
            fixture.feedback[0]["revision"] += 1
            page.get_by_role("button", name="读取最新记录", exact=True).click()
            expect(release.get_by_role("alert")).to_contain_text("审核服务暂时不可用，请稍后重试。")
            expect(retry).to_be_disabled()
            held.pop().abort("failed")
            expect(release.get_by_text("保存结果待确认，请查看原保存操作，不要再次提交。", exact=True)).to_be_visible()
            expect(retry).to_be_disabled()
            expect(release.get_by_role("button", name="查看原保存操作", exact=True)).to_be_enabled()
            expect(release.get_by_role("button", name="保存修改", exact=True)).to_be_disabled()
            self.assertIn("checkerEditOperation=", page.url)
            self.assertEqual(fixture.edits, 0)
            self.assertEqual(fixture.history, [])
            self.assertFalse(fixture.operations)
            browser.close()
