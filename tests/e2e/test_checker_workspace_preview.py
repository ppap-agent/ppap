"""Native UI + HTTP contract fixtures, not a claim of live model or PG acceptance."""

import json
import os
import re
import unittest
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
        self.lose_publish = False
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
            response = {"provider": "deepseek", "model": "deepseek-flash", "configured": True, "updated_at": None}
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
        elif path.startswith("/checker-optimizations/"):
            response = self.operations[path.split("/")[2]]
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
                candidate = self.revision("revision-edited", 3)
                candidate["base_revision_id"] = revision
                candidate["files"].update(self.revisions[revision]["files"])
                candidate["files"].update(body["files"])
                candidate["diff"] = [{"path": key, "before": self.revisions[revision]["files"].get(key, ""), "after": value} for key, value in body["files"].items()]
                self.revisions[candidate["id"]] = candidate
                response = candidate
            else:
                response = self.revisions[revision]
        if response is None:
            route.fulfill(status=404, json={"error": "fixture_route_not_found"})
        else:
            route.fulfill(json=response)


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
        page.get_by_label("正确结论", exact=True).select_option("NG")
        page.get_by_text("添加我在原件中找到的依据", exact=True).click()
        page.get_by_label("工作表名称", exact=True).fill("控制计划")
        page.get_by_label("单元格（例如 B12）", exact=True).fill("B12")
        page.get_by_label("原文摘录（选填）", exact=True).fill("缺少签名")
        page.get_by_role("button", name="加入这处依据", exact=True).click()
        page.get_by_label("为什么应当这样判断", exact=True).fill("该单元格没有签名")
        page.get_by_role("button", name="加入待修复", exact=True).click()

    def test_mobile_manual_evidence_lost_feedback_edit_and_delete(self):
        with sync_playwright() as playwright:
            browser = self.browser(playwright)
            fixture = CheckerHttpFixture()
            fixture.lose_feedback = True
            page = self.open(browser, fixture, 390)
            self.answer(page)
            expect(page.get_by_text("样本保存结果尚未确定，请读取原操作，不要重复提交。", exact=True)).to_be_visible()
            page.reload()
            page.get_by_role("button", name="读取原操作", exact=True).click()
            expect(page.get_by_text("原反馈已保存。", exact=True)).to_be_visible()
            self.assertEqual(len([row for row in fixture.calls if row[:2] == ("POST", "/audit-rounds/round-a/checker-feedback")]), 1)
            page.get_by_text("查看、改错或删除样本", exact=True).click()
            page.get_by_role("button", name="改错", exact=True).click()
            page.get_by_label("正确结论", exact=True).select_option("PASS")
            page.get_by_label("为什么应当这样判断", exact=True).fill("重新核对后确认签名有效")
            with page.expect_response(lambda response: response.request.method == "GET" and urlsplit(response.url).path == "/suite-revisions/revision-base/checker-feedback"):
                page.get_by_role("button", name="保存正确答案修改", exact=True).click()
            expect(page.get_by_text("正确答案已修改。原审核未改写，之前的验证需要重新执行。", exact=True)).to_be_visible()
            self.assertEqual(fixture.feedback[0]["expected_status"], "PASS")
            samples = page.locator("details").filter(has=page.get_by_text("查看、改错或删除样本", exact=True))
            expect(samples).to_contain_text("重新核对后确认签名有效")
            expect(samples).to_have_attribute("open", "")
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
            page.get_by_label("同意本次调用我的 DeepSeek 账户修复并验证这 1 条反馈", exact=True).check()
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
            page.get_by_role("button", name="仅保存这些修改", exact=True).click()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v3 · 待发布的修改", exact=True)).to_be_visible()
            fixture.lose_publish = True
            page.get_by_role("button", name="发布此修改", exact=True).click()
            page.get_by_label("我已核对上述未验证项和新增错误，仍要启用", exact=True).check()
            page.get_by_role("button", name="确认发布", exact=True).click()
            expect(page.get_by_text("发布结果尚未确定。请读取原操作，不要重复发布或回滚。", exact=True)).to_be_visible()
            page.reload()
            page.get_by_role("button", name="读取原发布操作", exact=True).click()
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
            expect(page.get_by_text("先保存当前修改再升级；保存结果未确认时，请先读取原保存操作。", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="仅保存这些修改", exact=True)).to_be_enabled()
            page.get_by_role("button", name="仅保存这些修改", exact=True).click()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v3 · 待发布的修改", exact=True)).to_be_visible()
            page.get_by_role("button", name="升级并验证", exact=True).click()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v2 · 待发布的修改", exact=True)).to_be_visible()
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
            expect(page.get_by_text("升级结果尚未确定。请读取原升级操作，不要再次升级。", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="发布此修改", exact=True)).not_to_be_visible()
            expect(page.get_by_role("button", name="重新运行验证样本", exact=True)).to_be_disabled()
            expect(page.get_by_role("button", name="仅保存待发布修改", exact=True)).to_be_disabled()
            expect(page.get_by_role("button", name="只运行现有检查验证", exact=True)).to_be_disabled()
            self.assertIn("checkerUpgrade=", page.url)
            page.reload()
            page.get_by_role("button", name="读取原升级操作", exact=True).click()
            expect(page.get_by_text("已找回升级候选，尚未自动运行样本。请重新运行验证样本后核对结果。", exact=True)).to_be_visible()
            self.assertNotIn("checkerUpgrade=", page.url)
            self.assertEqual(len([row for row in fixture.calls if row[:2] == ("POST", "/suites/control-plan/upgrades")]), 1)
            self.assertFalse(any(row[:2] == ("POST", "/suite-revisions/revision-upgraded/optimizations") for row in fixture.calls))
            expect(page.get_by_role("button", name="重新运行验证样本", exact=True)).to_be_enabled()
            page.get_by_role("button", name="重新运行验证样本", exact=True).click()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v2 · 待发布的修改", exact=True)).to_be_visible()
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
            expect(page.get_by_role("button", name="读取最新版本后核对升级", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="升级并验证", exact=True)).not_to_be_visible()
            self.assertNotIn("checkerUpgrade=", page.url)
            self.assertEqual(fixture.upgrades, {})
            fixture.latest_official = "official-next"
            fixture.reject_upgrade = False
            page.get_by_role("button", name="读取最新版本后核对升级", exact=True).click()
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
            expect(page.get_by_text("控制计划检查 · 本企业版本 v4 · 待发布的修改", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="重新运行验证样本", exact=True)).to_be_enabled()
            page.reload()
            expect(page.get_by_text("控制计划检查 · 本企业版本 v4 · 待发布的修改", exact=True)).to_be_visible()
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
            expect(page.get_by_text("控制计划检查 · 本企业版本 v2 · 待发布的修改", exact=True)).to_be_visible()
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
            expect(page.get_by_text("控制计划检查 · 本企业版本 v2 · 待发布的修改", exact=True)).to_be_visible()
            upgrade = next(row[2] for row in fixture.calls if row[:2] == ("POST", "/suites/control-plan/upgrades"))
            self.assertEqual(upgrade["source_revision_id"], "revision-current")
            self.assertEqual(upgrade["expected_revision"], 2)
            self.assertEqual(fixture.revisions["revision-upgraded"]["files"]["checker.py"], "# current enterprise change")
            self.assertEqual(fixture.current, "revision-current")
            browser.close()
