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
        if path == "/audit-rounds/round-a/checker-feedback":
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
            expect(page.get_by_text("已加入待修复，没有改写本轮结果。", exact=True)).to_be_visible()
            citation = fixture.feedback[0]["evidence"][0]
            self.assertEqual(citation["file_version_id"], "file-pdf")
            self.assertEqual(citation["locator"]["page"], 2)
            for actual, expected in zip(citation["locator"]["region"], [48, 192, 144, 288], strict=True):
                self.assertAlmostEqual(actual, expected, delta=1)
            self.assertEqual(len(downloads), 1)
            self.assertFalse(fixture.operations)
            browser.close()

    def test_admitted_optimization_keeps_polling_while_feedback_refresh_is_pending(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            page = self.open(browser, fixture)
            self.answer(page)
            expect(page.get_by_role("button", name="修复并验证", exact=True)).to_be_enabled()
            pending_refresh = []
            page.route("**/audit-rounds/round-a/checker-feedback", lambda route: pending_refresh.append(route))
            with (
                page.expect_request(lambda request: request.method == "GET" and request.url.endswith("/audit-rounds/round-a/checker-feedback")),
                page.expect_response(lambda response: response.request.method == "GET" and "/checker-optimizations/" in response.url) as first_read,
            ):
                page.get_by_role("button", name="修复并验证", exact=True).click()
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
            fixture.lose_optimization = True
            page.get_by_role("button", name="修复并验证", exact=True).click()
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
