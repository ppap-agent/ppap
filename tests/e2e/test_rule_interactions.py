"""Deterministic browser/API interleavings; no live backend or model calls."""

import os
import time
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
    def test_json_type_conflict_allows_explicit_correction_without_losing_the_source(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page, status="queued")
            self.native_rule_routes(page, state)
            self.open_requirement(page, "/audit?view=requirements", document_type="全功能检验报告")
            state["status"] = "failed"
            state["parse_patch"] = {"result": {"error": "rule_document_type_mismatch"}}
            page.get_by_role("button", name="刷新提取状态", exact=True).click()
            expect(page.get_by_role("alert").filter(has_text="原件中的资料类型与所选类型不同")).to_be_visible()
            page.get_by_label("适用资料类型").select_option("控制计划")
            state["status"] = "succeeded"
            state["parse_patch"] = {}
            page.get_by_role("button", name="重新提取条款", exact=True).click()
            expect(page.get_by_role("article", name="条款 1", exact=True)).to_contain_text("检查 A")
            self.assertEqual(len(state["parses"]), 2)
            first, corrected = state["parses"]
            self.assertNotEqual(first["request_id"], corrected["request_id"])
            self.assertEqual(first["source_file_id"], corrected["source_file_id"])
            self.assertEqual(first["source_file_version_id"], corrected["source_file_version_id"])
            self.assertEqual(first["document_type"], "全功能检验报告")
            self.assertEqual(corrected["document_type"], "控制计划")
            self.assertEqual(state["saves"], [])
            browser.close()

    def test_rule_parse_receipt_must_match_original_and_type_before_replacing_draft(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for patch in (
                {"id": "another-job"},
                {"source_file_id": "another-file"},
                {"source_file_version_id": "another-version"},
                {"document_type": "全功能检验报告"},
                {"result": {"source_file_version_id": "another-version"}},
                {"result": {"body": {"type": "全功能检验报告"}}},
            ):
                with self.subTest(patch=patch):
                    page = browser.new_page()
                    state = self.requirement_routes(page, status="queued")
                    self.native_rule_routes(page, state)
                    self.open_requirement(page, "/audit?view=requirements")
                    page.get_by_label("要求名称", exact=True).fill("保留的草稿名称")
                    page.get_by_role("textbox", name="来源说明／变更原因", exact=True).fill("保留的来源说明")
                    state["parse_patch"] = patch
                    state["status"] = "succeeded"
                    page.get_by_role("button", name="刷新提取状态", exact=True).click()
                    expect(page.get_by_role("alert").filter(has_text="提取回执与当前原件或资料类型不一致")).to_be_visible()
                    expect(page.get_by_label("要求名称", exact=True)).to_have_value("保留的草稿名称")
                    expect(page.get_by_role("textbox", name="来源说明／变更原因", exact=True)).to_have_value("保留的来源说明")
                    expect(page.get_by_role("article", name="条款 1", exact=True)).to_have_count(0)
                    expect(page.get_by_text("正在提取条款，完成后逐条核对原文。", exact=True)).to_have_count(0)
                    self.assertEqual(len(state["parses"]), 1)
                    self.assertEqual(state["saves"], [])
                    state["parse_patch"] = {}
                    page.get_by_role("button", name="核对原提取请求", exact=True).click()
                    expect(page.get_by_role("article", name="条款 1", exact=True)).to_contain_text("检查 A")
                    expect(page.get_by_text("提取回执与当前原件或资料类型不一致", exact=False)).to_have_count(0)
                    expect(page.get_by_role("textbox", name="来源说明／变更原因", exact=True)).to_have_value("保留的来源说明")
                    self.assertEqual(len(state["parses"]), 1)
                    self.assertEqual(state["saves"], [])
                    page.close()
            browser.close()

    def test_rule_source_recovery_preserves_checked_draft_without_reextracting_or_publishing(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)
            self.open_requirement(page, "/audit?view=requirements")
            self.check_requirement(page)
            address = page.url
            page.evaluate("""() => {
                const key = Object.keys(localStorage).find(key => key.endsWith(':requirement-library'));
                const saved = JSON.parse(localStorage.getItem(key));
                const basis = saved.value.collections.find(item => item.draft).draft.basis;
                delete basis.server.sources;
                for (const clause of basis.clauses) {
                    delete clause.provenance;
                    clause.location = `条款 ${clause.id}（原件位置待核对）`;
                }
                localStorage.setItem(key, JSON.stringify(saved));
            }""")
            page.reload()
            expect(page.get_by_label("要求名称", exact=True)).to_have_value("已核对要求")
            expect(page.get_by_role("textbox", name="来源说明／变更原因", exact=True)).to_have_value("已按原件核对。")
            expect(page.get_by_label("1 已核对来源", exact=True)).to_be_checked()
            expect(page.get_by_text("已核对 1/1 条", exact=True)).to_be_visible()
            self.assertEqual(page.url, address)
            self.assertEqual(len(state["parses"]), 1)
            self.assertEqual(state["saves"], [])
            browser.close()

    def test_unmatched_source_requires_original_selection_not_a_free_form_location(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            state["source_units"] = [{"id": "p:1", "location": "段落 1", "excerpt": "原文的检查 A，包含完整条件"}]
            self.native_rule_routes(page, state)
            self.open_requirement(page, "/audit?view=requirements")
            expect(page.get_by_text("已核对 0/1 条", exact=True)).to_be_visible()
            clause = page.get_by_role("article", name="条款 1", exact=True)
            expect(clause.get_by_label("1 已核对来源", exact=True)).to_be_disabled()
            clause.get_by_role("button", name="编辑条款", exact=True).click()
            expect(clause.get_by_label("出处位置", exact=True)).not_to_be_editable()
            clause.get_by_role("button", name="完成编辑", exact=True).click()
            clause.get_by_role("button", name="核对出处", exact=True).click()
            page.get_by_role("combobox", name="原文位置", exact=True).click()
            page.get_by_role("option", name="段落 1 · 原文的检查 A，包含完整条件", exact=True).click()
            expect(page.get_by_role("blockquote")).to_contain_text("原文的检查 A，包含完整条件")
            page.get_by_role("button", name="返回条款", exact=True).click()
            expect(clause.get_by_label("1 已核对来源", exact=True)).not_to_be_checked()
            clause.get_by_label("1 已核对来源", exact=True).check()
            expect(page.get_by_text("已核对 1/1 条", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="确认并保存版本", exact=True)).to_be_disabled()
            self.assertEqual(state["saves"], [])
            browser.close()

    def test_hidden_rule_upload_reply_cannot_navigate_away_from_audit(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)
            held = []
            page.route("**/api/files/*/versions/*/blob", lambda route: held.append(route))
            page.goto(WEB + "/audit?view=requirements")
            with page.expect_request(lambda request: request.method == "PUT" and request.url.endswith("/blob")):
                page.get_by_label("上传共用要求文件").set_input_files({"name": "迟到规则.json", "mimeType": "application/json", "buffer": b"{}"})
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="PPAP Audit", exact=True).click()
            address = page.url
            held[0].fulfill(json={**state["source"], "status": "ready"})
            page.locator("a").filter(has_text="迟到规则").first.wait_for(state="attached")
            expect(page.get_by_role("heading", name="上传 PPAP 文件", exact=True)).to_be_visible()
            self.assertEqual(page.url, address)
            page.get_by_role("button", name="上传规则", exact=True).click()
            expect(page.get_by_role("link", name="迟到规则", exact=True).filter(visible=True)).to_be_visible()
            self.assertEqual(state["saves"], [])
            browser.close()

    def test_late_rule_publication_keeps_the_current_surface_and_navigation_generation(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for destination in ("audit", "list", "same-draft", "completed-then-back"):
                with self.subTest(destination=destination):
                    page = browser.new_page()
                    state = self.requirement_routes(page)
                    self.native_rule_routes(page, state)
                    self.open_requirement(page, "/audit?view=requirements")
                    self.check_requirement(page)
                    state["hold_save_reply"] = True
                    with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/rules")):
                        page.get_by_role("button", name="确认并保存版本", exact=True).click()
                    page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="PPAP Audit", exact=True).click()
                    if destination == "list":
                        page.get_by_role("button", name="上传规则", exact=True).click()
                    elif destination == "same-draft":
                        page.go_back()
                    address = page.url
                    reply, rule = state["held_saves"][0]
                    with page.expect_response("**/api/audit-template-catalog"):
                        reply.fulfill(status=201, json=rule)
                    if destination == "completed-then-back":
                        expect(page.get_by_role("heading", name="上传 PPAP 文件", exact=True)).to_be_visible()
                        self.assertEqual(page.url, address)
                        page.go_back()
                    if destination in {"same-draft", "completed-then-back"}:
                        expect(page.get_by_role("heading", name="已核对要求", exact=True)).to_be_visible()
                        self.assertIn("requirement=" + rule["id"], page.url)
                        self.assertIn("reqVersion=" + rule["id"], page.url)
                        page.get_by_role("link", name="返回审核规则", exact=True).click()
                    else:
                        expect(page.get_by_role("heading", name="审核规则" if destination == "list" else "上传 PPAP 文件", exact=True)).to_be_visible()
                        self.assertEqual(page.url, address)
                    if destination == "audit":
                        page.get_by_role("button", name="上传规则", exact=True).click()
                    expect(page.get_by_role("link", name="已核对要求", exact=True).filter(visible=True)).to_be_visible()
                    self.assertEqual(len(state["saves"]), 1)
                    page.close()
            browser.close()

    def test_new_rule_draft_does_not_reuse_the_previous_publication_receipt(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)
            self.open_requirement(page, "/audit?view=requirements")
            self.check_requirement(page)
            page.get_by_role("button", name="确认并保存版本", exact=True).click()
            expect(page.get_by_role("button", name="创建新版本", exact=True)).to_be_visible()
            page.get_by_role("button", name="创建新版本", exact=True).click()
            page.get_by_label("版本号", exact=True).fill("v2")
            page.get_by_label("来源说明／变更原因", exact=True).fill("第二次核对")
            page.get_by_role("checkbox", name="1 已核对来源", exact=True).check()
            page.get_by_label("已核对本版本的来源、范围和变更", exact=True).check()
            page.get_by_role("button", name="确认并保存版本", exact=True).click()
            expect(page.get_by_role("button", name="创建新版本", exact=True)).to_be_visible()
            page.get_by_role("button", name="创建新版本", exact=True).click()
            page.get_by_label("版本号", exact=True).fill("v3")
            address = page.url
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="PPAP Audit", exact=True).click()
            page.go_back()
            expect(page.get_by_label("版本号", exact=True)).to_have_value("v3")
            self.assertEqual(page.url, address)
            self.assertEqual(len(state["saves"]), 2)
            browser.close()

    def test_native_local_record_recovery_returns_to_native_rules_not_preview(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for close_dialog in (False, True):
                with self.subTest(close_dialog=close_dialog):
                    page = browser.new_page()
                    state = self.requirement_routes(page)
                    self.native_rule_routes(page, state)
                    page.goto(WEB + "/audit?view=requirements")
                    expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
                    page.get_by_role("button", name="alice，账号菜单", exact=True).click()
                    page.get_by_role("menuitem", name="本机草稿", exact=True).click()
                    dialog = page.get_by_role("dialog", name="本机记录", exact=True)
                    dialog.get_by_role("button", name="清理本机记录", exact=True).click()
                    dialog.get_by_role("button", name="核对清理范围", exact=True).click()
                    dialog.get_by_role("checkbox", name="我已保留所需备份，理解此浏览器中的原记录将被删除", exact=True).check()
                    dialog.get_by_role("button", name="确认清理这 1 条记录", exact=True).click()
                    if close_dialog:
                        page.keyboard.press("Escape")
                    else:
                        dialog.get_by_role("button", name="重新打开工作台", exact=True).click()
                    expect(page).to_have_url(WEB + "/audit?view=requirements")
                    expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
                    expect(dialog).to_have_count(0)
                    page.close()
            browser.close()

    def test_rule_publication_keeps_unknown_validation_request_identity(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)
            created, latest_reads = [], []

            def latest(route):
                latest_reads.append(route.request.url)
                route.fulfill(json={"validation": None})

            def create_validation(route):
                created.append(route.request.post_data_json)
                route.abort("failed")

            def read_validation(route):
                job = created[0]
                self.assertEqual(route.request.url.rsplit("/", 1)[1], job["request_id"])
                route.fulfill(
                    json={
                        "id": job["request_id"],
                        "source_file_id": job["source_file_id"],
                        "source_file_version_id": job["source_file_version_id"],
                        "status": "succeeded",
                        "result": {"state": "valid", "reasons": []},
                    }
                )

            page.route("**/api/files/*/versions/*/validation", latest)
            page.route("**/api/file-validations", create_validation)
            page.route("**/api/file-validations/*", read_validation)
            page.goto(WEB + "/audit")
            page.get_by_label("选择审核文件", exact=True).set_input_files(
                {"name": "未知回执.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"}
            )
            retry = page.get_by_role("button", name="重新读取 未知回执.xlsx 检查结果", exact=True)
            expect(retry).to_be_enabled()
            page.get_by_role("button", name="上传规则", exact=True).click()
            page.get_by_label("上传共用要求文件").set_input_files({"name": "新规则.json", "mimeType": "application/json", "buffer": b"{}"})
            page.get_by_label("适用资料类型").select_option("控制计划")
            page.get_by_role("button", name="提取条款", exact=True).click()
            self.check_requirement(page)
            with page.expect_response("**/api/audit-template-catalog"):
                page.get_by_role("button", name="确认并保存版本", exact=True).click()
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="PPAP Audit", exact=True).click()
            retry.click()
            expect(page.get_by_role("checkbox", name="选择 未知回执.xlsx", exact=True)).to_be_checked()
            self.assertEqual(len(created), 1)
            self.assertEqual(len(latest_reads), 1)
            browser.close()

    def test_native_audit_exposes_rule_upload_and_reuses_verified_publication(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)
            page.goto(WEB + "/audit")
            page.get_by_role("button", name="上传规则", exact=True).click()
            expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
            with page.expect_file_chooser():
                page.get_by_role("button", name="上传规则", exact=True).click()
            page.get_by_label("上传共用要求文件").set_input_files({"name": "客户规则.json", "mimeType": "application/json", "buffer": b"{}"})
            page.get_by_label("适用资料类型").select_option("控制计划")
            page.get_by_role("button", name="提取条款", exact=True).click()
            self.check_requirement(page)
            page.get_by_label("不限定客户", exact=True).uncheck()
            page.get_by_label("适用客户", exact=True).fill("测试客户")
            page.get_by_label("已核对本版本的来源、范围和变更", exact=True).check()
            page.get_by_role("button", name="确认并保存版本", exact=True).click()
            expect(page.get_by_role("status").filter(has_text="版本 v1 已保存")).to_be_visible()
            self.assertEqual(len(state["parses"]), 1)
            self.assertEqual(len(state["saves"]), 1)
            self.assertEqual(state["saves"][0]["body"]["customers"], ["测试客户"])
            self.assertEqual(state["saves"][0]["source_file_version_id"], state["source"]["id"])
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="PPAP Audit", exact=True).click()
            page.get_by_role("combobox", name="客户（可选）", exact=True).click()
            expect(page.get_by_role("option", name="测试客户", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="审核规则", exact=True).click()
            page.get_by_role("link", name="已核对要求", exact=True).filter(visible=True).click()
            page.reload()
            expect(page.get_by_role("heading", name="已核对要求", exact=True)).to_be_visible()
            expect(page.get_by_text("检查 A", exact=True)).to_be_visible()
            self.assertEqual(len(state["saves"]), 1)
            self.assertFalse(any(path.startswith(("/audit-tasks", "/drafts", "/rounds")) for method, path in state["writes"]))
            browser.close()

    def test_native_rule_draft_survives_navigation_and_mobile_deep_link(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)
            page.goto(WEB + "/audit?view=requirements")
            expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
            page.get_by_label("上传共用要求文件").set_input_files({"name": "草稿规则.json", "mimeType": "application/json", "buffer": b"{}"})
            page.get_by_label("要求名称", exact=True).fill("未发布的规则草稿")
            page.get_by_role("button", name="打开产品导航", exact=True).click()
            page.get_by_role("dialog").get_by_role("button", name="PPAP Audit", exact=True).click()
            expect(page.get_by_role("heading", name="上传 PPAP 文件", exact=True)).to_be_visible()
            page.get_by_role("button", name="上传规则", exact=True).click()
            page.get_by_role("link", name="未发布的规则草稿").filter(visible=True).click()
            expect(page.get_by_label("要求名称", exact=True)).to_have_value("未发布的规则草稿")
            page.reload()
            expect(page.get_by_label("要求名称", exact=True)).to_have_value("未发布的规则草稿")
            self.assertEqual(state["parses"], [])
            self.assertEqual(state["saves"], [])
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            browser.close()

    def native_rule_routes(self, page, state):
        def catalog(route):
            if state.get("catalog_failed"):
                route.fulfill(status=503, json={"error": "catalog_unavailable"})
                return
            customers = sorted({customer for rule in state["rules"].values() for customer in rule["body"]["customers"]})
            route.fulfill(
                json={
                    "coverage_universe": ["控制计划", *[f"资料类型{index}" for index in range(16)]],
                    "customers": [{"customer_id": name, "name": name, "effective_rule_types": ["控制计划"]} for name in customers],
                }
            )

        page.route("**/api/audit-template-catalog", catalog)
        page.route("**/api/audit-tasks?*", lambda route: route.fulfill(json={"tasks": [], "next_offset": None}))

    def test_native_rule_catalog_refresh_failure_keeps_the_saved_version(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)
            self.open_requirement(page, "/audit?view=requirements")
            self.check_requirement(page)
            state["catalog_failed"] = True
            page.get_by_role("button", name="确认并保存版本", exact=True).click()
            notice = page.get_by_role("alert").filter(has_text="客户规则目录未能刷新")
            expect(notice).to_be_visible()
            expect(page.get_by_role("heading", name="已核对要求", exact=True)).to_be_visible()
            self.assertEqual(len(state["saves"]), 1)
            state["catalog_failed"] = False
            notice.get_by_role("button", name="重新读取客户目录", exact=True).click()
            expect(notice).to_have_count(0)
            expect(page.get_by_role("heading", name="已核对要求", exact=True)).to_be_visible()
            self.assertEqual(len(state["saves"]), 1)
            browser.close()

    def test_native_rule_upload_keeps_unconfirmed_audit_files_and_selection(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)

            def validation(route):
                version = state["source"]
                route.fulfill(
                    json={
                        "validation": {
                            "id": "validation-original",
                            "source_file_id": version["file_id"],
                            "source_file_version_id": version["id"],
                            "status": "succeeded",
                            "result": {"state": "valid", "reasons": []},
                        }
                    }
                )

            page.route("**/api/files/*/versions/*/validation", validation)
            page.goto(WEB + "/audit")
            page.get_by_label("选择审核文件", exact=True).set_input_files(
                {"name": "保留选择.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"}
            )
            selected = page.get_by_role("checkbox", name="选择 保留选择.xlsx", exact=True)
            expect(selected).to_be_checked()
            selected.uncheck()
            upload_identity = page.evaluate("history.state.auditUploadDraft")
            self.assertTrue(upload_identity)
            page.get_by_role("button", name="上传规则", exact=True).click()
            page.get_by_label("上传共用要求文件").set_input_files({"name": "新增规则.json", "mimeType": "application/json", "buffer": b"{}"})
            page.get_by_label("要求名称", exact=True).fill("待核对规则")
            page.get_by_role("tab", name="版本变化", exact=True).click()
            page.get_by_role("link", name="返回审核规则", exact=True).click()
            page.get_by_role("searchbox", name="搜索审核要求", exact=True).fill("待核对")
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="PPAP Audit", exact=True).click()
            expect(page.get_by_role("heading", name="核对本次文件", exact=True)).to_be_visible()
            expect(selected).not_to_be_checked()
            self.assertEqual(page.evaluate("history.state.auditUploadDraft"), upload_identity)
            self.assertEqual(state["parses"], [])
            self.assertEqual(state["saves"], [])
            self.assertFalse(any(path.startswith(("/audit-tasks", "/drafts", "/rounds")) for method, path in state["writes"]))
            browser.close()

    def test_leaving_during_draft_creation_never_starts_file_transmission(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
            held, transmissions = [], []
            page.route("**/api/drafts", lambda route: held.append(route))
            page.route("**/api/files", lambda route: (transmissions.append(route.request.post_data_json), route.fulfill(status=500, json={"error": "unexpected_upload"})))
            page.goto(WEB)
            page.evaluate("history.pushState(null, '', '?view=workbench&queue=all')")
            page.get_by_role("button", name="上传资料", exact=True).click()
            with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/drafts")):
                page.get_by_label("上传文件", exact=True).set_input_files({"name": "leave.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"})
            page.go_back()
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/api/drafts")) as late:
                held[0].fulfill(
                    status=201,
                    json={
                        "id": "late-draft",
                        "status": "open",
                        "analysis_mode": "standard",
                        "predecessor_id": None,
                        "items": [],
                        "case_context": {},
                        "writeback": {"available": False},
                        "updated_at": "2026-10-18T00:00:00Z",
                    },
                )
            late.value.finished()
            expect(page.get_by_role("dialog", name="上传资料", exact=True)).to_have_count(0)
            expect(page.get_by_role("heading", name="工作台", exact=True)).to_be_visible()
            self.assertEqual(transmissions, [])
            browser.close()

    def test_batch_upload_keeps_uncertain_version_for_read_only_recovery(self):
        from playwright.sync_api import expect

        names = ["first.xlsx", "second.xlsx", "third.xlsx", "fourth.xlsx"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_default_timeout(4000)
            self.route(page)
            created, blobs, saves, reads, writes = [], [], [], [], []
            versions = {}
            read_state = {"state": "unknown", "fail": False, "hold": False, "mismatch": False}
            held = []
            draft_fields = {
                "status": "open",
                "analysis_mode": "standard",
                "predecessor_id": None,
                "updated_at": "2026-10-18T00:00:00Z",
                "writeback": {"available": False},
                "items": [],
                "case_context": {},
            }
            page.on("request", lambda request: writes.append((request.method, urlsplit(request.url).path)) if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None)

            def create_file(route):
                body = route.request.post_data_json
                created.append(body["original_filename"])
                version = {
                    "id": body["request_id"],
                    "file_id": f"file-{len(created)}",
                    "original_filename": body["original_filename"],
                    "byte_size": body["byte_size"],
                    "content_type": body["content_type"],
                    "sha256": body["sha256"],
                    "status": "staging",
                }
                versions[version["id"]] = version
                route.fulfill(status=201, json=version)

            def write_blob(route):
                blobs.append(route.request.url)
                version = versions[urlsplit(route.request.url).path.split("/")[-2]]
                if version["original_filename"] in {"third.xlsx", "sixth.xlsx"}:
                    route.abort("failed")
                else:
                    version["status"] = "ready"
                    route.fulfill(json=version)

            def read_receipt(route):
                request_id = route.request.url.rsplit("/", 1)[1]
                reads.append(request_id)
                if read_state["hold"]:
                    held.append(route)
                    return
                if read_state["fail"]:
                    route.abort("failed")
                    return
                version = dict(versions[request_id])
                if read_state["state"] == "ready":
                    version["status"] = "ready"
                if read_state["mismatch"]:
                    version["byte_size"] += 1
                route.fulfill(
                    json={
                        "request_id": request_id,
                        "state": read_state["state"],
                        "file": version,
                        "reason": "文件可能已接收，请核对原请求。",
                        "retry_allowed": False,
                        "resume_allowed": read_state["state"] == "uploading",
                    }
                )

            def draft(route):
                if route.request.method == "PUT":
                    saves.append(route.request.post_data_json)
                saved = saves[-1] if saves else {}
                items = [
                    {**item, "file_id": versions[item["file_version_id"]]["file_id"], "original_filename": versions[item["file_version_id"]]["original_filename"], "status": "ready", "rule": None}
                    for item in saved.get("items", [])
                ]
                route.fulfill(
                    json={
                        **draft_fields,
                        "id": "batch-draft",
                        "items": items,
                        "case_context": saved.get("case_context", {}),
                    }
                )

            page.route("**/api/rules", lambda route: route.fulfill(json={"rules": []}))
            page.route("**/api/drafts?*", lambda route: route.fulfill(json={"drafts": [], "next_offset": None}))
            page.route("**/api/rounds?*", lambda route: route.fulfill(json={"rounds": [], "next_offset": None}))
            page.route("**/api/files", create_file)
            page.route("**/api/files/*/versions/*/blob", write_blob)
            page.route("**/api/file-uploads/*", read_receipt)
            page.route("**/api/drafts", lambda route: route.fulfill(status=201, json={**draft_fields, "id": "batch-draft"}))
            page.route("**/api/drafts/batch-draft", draft)
            page.goto(WEB)
            page.get_by_role("button", name="上传资料", exact=True).click()
            page.get_by_label("上传文件", exact=True).set_input_files(
                [{"name": name, "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"} for name in names]
            )
            expect(page.get_by_role("table", name="审核资料", exact=True)).to_contain_text("first.xlsx")
            expect(page.get_by_role("table", name="审核资料", exact=True)).to_contain_text("second.xlsx")
            expect(page.get_by_role("button", name="核对上传结果", exact=True)).to_be_visible()
            self.assertEqual(created, names[:3])
            self.assertEqual(len(blobs), 3)
            self.assertEqual([versions[item["file_version_id"]]["file_id"] for item in saves[-1]["items"]], ["file-1", "file-2"])
            self.assertEqual(len(reads), 1)
            writes.clear()
            page.evaluate("localStorage.clear()")
            page.reload()
            table = page.get_by_role("table", name="审核资料", exact=True)
            expect(table).to_contain_text("first.xlsx")
            expect(table).to_contain_text("second.xlsx")
            check = page.get_by_role("button", name="核对上传结果", exact=True)
            expect(check).to_be_visible()
            self.assertEqual(writes, [])
            self.assertEqual(created, names[:3])
            read_state["fail"] = True
            check.click()
            expect(page.get_by_role("alert").filter(has_text="暂时无法连接服务，请重新读取")).to_be_visible()
            expect(check).to_be_enabled()
            self.assertEqual(writes, [])
            read_state["fail"] = False
            check.click()
            expect(check).to_be_enabled()
            self.assertEqual(created, names[:3])
            self.assertEqual(len(blobs), 3)
            read_state["state"] = "ready"
            read_state["mismatch"] = True
            check.click()
            expect(page.get_by_role("alert").filter(has_text="上传回执与原文件不一致")).to_be_visible()
            expect(check).to_be_enabled()
            self.assertEqual(len(saves[-1]["items"]), 2)
            read_state["mismatch"] = False
            with page.expect_response(
                lambda response: response.request.method == "PUT" and response.url.endswith("/drafts/batch-draft") and len(response.request.post_data_json["items"]) == 3
            ) as saved_third:
                check.click()
            saved_third.value.finished()
            expect(check).to_have_count(0)
            expect(table.get_by_role("row").filter(has_text="third.xlsx")).not_to_contain_text("核对上传结果")
            self.assertEqual([versions[item["file_version_id"]]["file_id"] for item in saves[-1]["items"]], ["file-1", "file-2", "file-3"])
            self.assertEqual(created, names[:3])
            self.assertEqual(len(blobs), 3)
            self.assertEqual(set(reads), {list(versions)[2]})
            with page.expect_response(
                lambda response: response.request.method == "PUT" and response.url.endswith("/drafts/batch-draft") and len(response.request.post_data_json["items"]) == 4
            ) as saved_fourth:
                table.get_by_role("row").filter(has_text="fourth.xlsx").get_by_role("button", name="上传此文件", exact=True).click()
            saved_fourth.value.finished()
            expect(table.get_by_role("row").filter(has_text="fourth.xlsx").get_by_role("button", name="上传此文件", exact=True)).to_have_count(0)
            self.assertEqual(created, names)
            self.assertEqual(len(blobs), 4)
            self.assertEqual(len(saves[-1]["items"]), 4)
            read_state["state"] = "unknown"
            page.get_by_label("添加审核资料", exact=True).set_input_files(
                [{"name": name, "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"} for name in ["fifth.xlsx", "sixth.xlsx"]]
            )
            expect(table).to_contain_text("fifth.xlsx")
            expect(check).to_be_enabled()
            self.assertEqual(created, names + ["fifth.xlsx", "sixth.xlsx"])
            self.assertEqual(len(saves[-1]["items"]), 5)
            table.get_by_role("row").filter(has_text="sixth.xlsx").get_by_role("button", name="sixth.xlsx 操作", exact=True).click()
            with page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/drafts/batch-draft")) as removed:
                page.get_by_role("menuitem", name="移出本次资料", exact=True).click()
            removed.value.finished()
            expect(table.get_by_role("checkbox", name="纳入 sixth.xlsx", exact=True)).not_to_be_checked()
            expect(check).to_be_enabled()
            read_state["hold"] = True
            check.click()
            expect(page.get_by_role("button", name="正在核对…", exact=True)).to_be_disabled()
            self.assertEqual(len(held), 1)
            page.get_by_role("link", name="工作台", exact=True).click()
            page.route("**/api/drafts", lambda route: route.fulfill(status=201, json={**draft_fields, "id": "another-draft"}))
            other_saves = []
            fail_save = {"enabled": False}

            def save_other(route):
                if fail_save["enabled"]:
                    route.fulfill(status=403, json={"error": "access_denied"})
                    return
                other_saves.append(route.request.post_data_json)
                route.fulfill(json={**draft_fields, "id": "another-draft"})

            page.route("**/api/drafts/another-draft", save_other)
            page.get_by_role("button", name="上传资料", exact=True).click()
            page.get_by_label("上传文件", exact=True).set_input_files({"name": "other.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"})
            expect(page.get_by_role("table", name="审核资料", exact=True)).to_contain_text("other.xlsx")
            writes.clear()
            old_request = list(versions)[5]
            late_file = {**versions[old_request], "status": "ready"}
            with page.expect_response(lambda response: response.url.endswith(f"/file-uploads/{old_request}")) as late:
                held[0].fulfill(json={"request_id": old_request, "state": "ready", "file": late_file, "reason": None, "retry_allowed": False})
            late.value.finished()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            expect(page.get_by_role("table", name="审核资料", exact=True)).not_to_contain_text("sixth.xlsx")
            self.assertEqual(writes, [])
            self.assertEqual(len(other_saves[-1]["items"]), 1)
            fail_save["enabled"] = True
            page.get_by_label("添加审核资料", exact=True).set_input_files(
                {"name": "not-sent.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"}
            )
            not_sent = page.get_by_role("table", name="审核资料", exact=True).get_by_role("row").filter(has_text="not-sent.xlsx")
            expect(not_sent.get_by_role("button", name="上传此文件", exact=True)).to_be_visible()
            self.assertNotIn("not-sent.xlsx", created)
            fail_save["enabled"] = False
            with page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/drafts/another-draft") and len(response.request.post_data_json["items"]) == 2):
                not_sent.get_by_role("button", name="上传此文件", exact=True).click()
            expect(not_sent.get_by_role("button", name="上传此文件", exact=True)).to_have_count(0)
            self.assertEqual(created[-1], "not-sent.xlsx")
            read_state.update({"hold": False, "state": "uploading"})
            page.route("**/api/files/*/versions/*/blob", lambda route: route.fulfill(status=401, json={"error": "unauthenticated"}))
            page.get_by_label("添加审核资料", exact=True).set_input_files(
                {"name": "denied.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"}
            )
            denied = page.get_by_role("table", name="审核资料", exact=True).get_by_role("row").filter(has_text="denied.xlsx")
            expect(denied).to_contain_text("原件未接收（401）")
            expect(denied.get_by_role("button", name="核对上传结果", exact=True)).to_be_enabled()
            before = list(created)
            denied.get_by_role("button", name="核对上传结果", exact=True).click()
            expect(denied.get_by_role("button", name="继续原传输", exact=True)).to_be_enabled()
            self.assertEqual(created, before)
            browser.close()

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
                "predecessor_id": None,
                "analysis_mode": "standard",
                "inputs": [{"file_version_id": VERSION, "original_filename": "history.xlsx", "role": "target", "rule": None}],
                "result_counts": {"PASS": 1},
                "reviewed_count": 0,
                "created_at": "2026-10-18T00:00:00Z",
                "case_title": "",
                "case_scope": "",
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

    def test_native_validation_read_failure_keeps_other_file_without_creating_another_job(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_default_timeout(5000)
            self.route(page)
            versions, reads, creates, writes = {}, [], [], []
            page.on("request", lambda request: writes.append((request.method, urlsplit(request.url).path)) if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None)
            page.route("**/api/audit-template-catalog", lambda route: route.fulfill(json={"coverage_universe": [f"type-{i}" for i in range(17)], "customers": []}))
            page.route("**/api/audit-tasks?*", lambda route: route.fulfill(json={"tasks": [], "next_offset": None}))

            def upload(route):
                body = route.request.post_data_json
                version = {key: body[key] for key in ("original_filename", "byte_size", "content_type", "sha256")}
                version.update(id=body["request_id"], file_id=body["request_id"], status="staging")
                versions[version["id"]] = version
                route.fulfill(status=201, json=version)

            def blob(route):
                version = versions[route.request.url.split("/versions/")[1].split("/")[0]]
                version["status"] = "ready"
                route.fulfill(json=version)

            def job(version, ready=False):
                return {
                    "id": "validation-" + version["id"],
                    "source_file_id": version["file_id"],
                    "source_file_version_id": version["id"],
                    "status": "succeeded" if ready else "queued",
                    "result": {"state": "valid", "reasons": []} if ready else None,
                }

            def latest(route):
                version = versions[route.request.url.split("/versions/")[1].split("/")[0]]
                reads.append((route.request.method, "latest", version["id"]))
                route.fulfill(json={"validation": job(version, version["original_filename"] == "control.xlsx")})

            def read_validation(route):
                ident = route.request.url.rsplit("/", 1)[1]
                reads.append((route.request.method, "job", ident))
                if sum(kind == "job" for _method, kind, _ident in reads) == 1:
                    route.abort("failed")
                else:
                    version = versions[ident.removeprefix("validation-")]
                    route.fulfill(json={**job(version, True), "result": {"state": "invalid", "reasons": ["此文件无法读取。"]}})

            def create_validation(route):
                creates.append(route.request.post_data_json)
                route.fulfill(status=500, json={"error": "unexpected_validation_creation"})

            page.route("**/api/files", upload)
            page.route("**/api/files/*/versions/*/blob", blob)
            page.route("**/api/files/*/versions/*/validation", latest)
            page.route("**/api/file-validations/*", read_validation)
            page.route("**/api/file-validations", create_validation)
            page.goto(WEB + "/audit")
            page.get_by_label("选择审核文件", exact=True).set_input_files(
                [{"name": name, "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"} for name in ("flow.xlsx", "control.xlsx")]
            )
            retry = page.get_by_role("button", name="重新读取 flow.xlsx 检查结果", exact=True)
            expect(retry).to_be_enabled()
            expect(page.get_by_role("checkbox", name="选择 control.xlsx", exact=True)).to_be_checked()
            expect(page.get_by_role("heading", name="异常文件 · 1 份", exact=True)).to_be_visible()
            retry.click()
            expect(page.get_by_text("此文件无法读取。", exact=True)).to_be_visible()
            expect(retry).to_have_count(0)
            expect(page.get_by_role("checkbox", name="选择 control.xlsx", exact=True)).to_be_checked()
            flow = next(version for version in versions.values() if version["original_filename"] == "flow.xlsx")
            self.assertCountEqual(reads, [("GET", "latest", version["id"]) for version in versions.values()] + [("GET", "job", "validation-" + flow["id"])] * 2)
            self.assertEqual(creates, [])
            self.assertCountEqual(writes, [("POST", "/api/files")] * 2 + [("PUT", f"/api/files/{version['file_id']}/versions/{version['id']}/blob") for version in versions.values()])
            browser.close()

    def test_lost_or_invalid_blob_reply_reads_original_request_without_reupload(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            cases = [("ready", "lost"), ("ready", "bad_json"), ("staging", "lost"), ("failed_read", "lost"), ("wrong_request", "lost"), ("wrong_hash", "bad_json")]
            for readback, blob_reply in cases:
                with self.subTest(readback=readback, blob_reply=blob_reply):
                    page = browser.new_page()
                    page.set_default_timeout(4000)
                    self.route(page)
                    creates, blobs, reads, saves = [], [], [], []
                    versions = {}
                    draft_body = {
                        "id": JOB,
                        "status": "open",
                        "analysis_mode": "standard",
                        "predecessor_id": None,
                        "updated_at": "2026-10-18T00:00:00Z",
                        "writeback": {"available": False},
                        "items": [],
                        "case_context": {},
                    }

                    def create_file(route):
                        body = route.request.post_data_json
                        creates.append(body["request_id"])
                        version = {
                            "id": body["request_id"],
                            "file_id": SOURCE,
                            "original_filename": body["original_filename"],
                            "byte_size": body["byte_size"],
                            "content_type": body["content_type"],
                            "sha256": body["sha256"],
                            "status": "staging",
                        }
                        versions[body["request_id"]] = version
                        route.fulfill(status=201, json=version)

                    def lose_blob_reply(route):
                        blobs.append(route.request.method)
                        if blob_reply == "bad_json":
                            route.fulfill(status=200, body="{", content_type="application/json")
                        else:
                            route.abort("failed")

                    def read_receipt(route):
                        request_id = route.request.url.rsplit("/", 1)[1]
                        reads.append((route.request.method, request_id))
                        if route.request.method != "GET":
                            route.fulfill(status=405, json={"error": "read_only_receipt"})
                            return
                        if readback == "failed_read":
                            route.abort("failed")
                            return
                        ready = readback != "staging"
                        version = {**versions[request_id], "status": "ready" if ready else "staging"}
                        if readback == "wrong_request":
                            version["id"] = VERSION
                        if readback == "wrong_hash":
                            version["sha256"] = "0" * 64
                        route.fulfill(json={"request_id": request_id, "state": "ready" if ready else "uploading", "file": version, "reason": None, "retry_allowed": False, "resume_allowed": not ready})

                    def save_draft(route):
                        saves.append(route.request.post_data_json)
                        route.fulfill(json=draft_body)

                    page.route("**/api/rules", lambda route: route.fulfill(json={"rules": []}))
                    page.route("**/api/drafts?*", lambda route: route.fulfill(json={"drafts": [], "next_offset": None}))
                    page.route("**/api/rounds?*", lambda route: route.fulfill(json={"rounds": [], "next_offset": None}))
                    page.route("**/api/files", create_file)
                    page.route("**/api/files/*/versions/*/blob", lose_blob_reply)
                    page.route("**/api/file-uploads/*", read_receipt)
                    page.route("**/api/drafts", lambda route: route.fulfill(status=201, json=draft_body))
                    page.route("**/api/drafts/" + JOB, save_draft)
                    page.goto(WEB)
                    page.get_by_role("button", name="上传资料", exact=True).click()
                    page.get_by_label("上传文件", exact=True).set_input_files({"name": "尺寸检验报告-example.xlsm", "mimeType": "application/vnd.ms-excel.sheet.macroEnabled.12", "buffer": b"fixture"})
                    table = page.get_by_role("table", name="审核资料", exact=True)
                    expect(table).to_contain_text("尺寸检验报告-example.xlsm")
                    if readback == "ready":
                        expect(table.get_by_text("上传结果尚未确认，请核对原请求。", exact=True)).to_have_count(0)
                        expect(page.get_by_role("button", name="添加文件", exact=True)).to_be_enabled()
                        expect(page.get_by_role("button", name="核对上传结果", exact=True)).to_have_count(0)
                        self.assertEqual([item["file_version_id"] for item in saves[-1]["items"]], creates)
                    else:
                        expect(page.get_by_role("button", name="核对上传结果", exact=True)).to_be_enabled()
                        self.assertEqual(saves[-1]["items"], [])
                        if readback == "staging":
                            expect(page.get_by_role("button", name="继续原传输", exact=True)).to_be_enabled()
                        else:
                            expect(page.get_by_role("button", name="继续原传输", exact=True)).to_have_count(0)
                    self.assertEqual(len(creates), 1)
                    self.assertEqual(blobs, ["PUT"])
                    self.assertEqual(reads, [("GET", creates[0])])
                    page.close()
            browser.close()

    def test_failed_round_reaudit_preserves_inputs_and_mode_without_starting_a_job(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_default_timeout(4000)
            self.route(page)
            page.route(
                f"**/api/files/{SOURCE}/versions/{VERSION}",
                lambda route: route.fulfill(json={"file_id": SOURCE, "id": VERSION, "original_filename": "原始规则.json", "status": "ready"}),
            )
            prepared = {}
            rule = {"id": "version-b", "title": "规则 B", "body": BODY, "created_at": "2026-10-18T00:00:00Z", "source_file_id": SOURCE, "source_file_version_id": VERSION, "predecessor_id": None}
            items = [
                {
                    "file_id": SOURCE,
                    "file_version_id": VERSION,
                    "original_filename": "control-plan.xlsx",
                    "role": "target",
                    "rule_version_id": "version-b",
                    "status": "ready",
                    "rule": {key: rule[key] for key in ("id", "title", "body")},
                },
                {
                    "file_id": "flow-file",
                    "file_version_id": "flow-version",
                    "original_filename": "process-flow.xlsx",
                    "role": "target",
                    "rule_version_id": "flow-rule",
                    "status": "ready",
                    "rule": {"id": "flow-rule", "title": "流程图要求", "body": {**BODY, "type": "过程流程图"}},
                },
                {
                    "file_id": "other-plan-file",
                    "file_version_id": "other-plan-version",
                    "original_filename": "other-control-plan.xlsx",
                    "role": "target",
                    "rule_version_id": "other-plan-rule",
                    "status": "ready",
                    "rule": {"id": "other-plan-rule", "title": "另一套控制计划要求", "body": BODY},
                },
                {
                    "file_id": "reference-file",
                    "file_version_id": "reference-version",
                    "original_filename": "context.pdf",
                    "role": "reference",
                    "rule_version_id": None,
                    "status": "ready",
                    "rule": None,
                },
            ]
            posts, saves, accepted, reads, held = [], [], [], [], []
            response = {"mode": "normal", "read_fails": False}
            page.route(
                "**/api/public-config",
                lambda route: route.fulfill(
                    json={
                        "git_sha": "fixture",
                        "api_url": WEB + "/api",
                        "profile": "isolated-dev",
                        "login": "isolated-dev",
                        "deep_offered": True,
                        "feishu_app_id": "",
                        "current_access": "allowed",
                        "usage_charge_enabled": False,
                    }
                ),
            )
            page.route(
                "**/api/rounds/" + JOB,
                lambda route: route.fulfill(
                    json={
                        "id": JOB,
                        "execution": "failed",
                        "analysis_mode": "deep",
                        "source_draft_id": "old-draft",
                        "preparation_allowed": not response.get("task", False),
                        "predecessor_id": None,
                        "created_at": "2026-10-18T00:00:00Z",
                        "coverage": {},
                        "progress": None,
                        "result_attempt": None,
                        "writeback": {"available": False},
                        "case_context": {},
                        "inputs": items,
                        "findings": [],
                        "failure": {"code": "deep_budget_exhausted", "message": "执行额度已用完。", "retryable": False},
                    }
                ),
            )

            def create_draft(route):
                body = route.request.post_data_json
                posts.append(body)
                prepared.update(
                    id=body["request_id"],
                    status="open",
                    analysis_mode="deep",
                    predecessor_id=JOB,
                    items=items,
                    case_context=body["case_context"],
                    updated_at="2026-10-18T00:00:00Z",
                    writeback={"available": False},
                )
                if response["mode"] in {"lost", "read_failure"}:
                    route.abort("failed")
                elif response["mode"] == "bad_json":
                    route.fulfill(status=201, body="{", content_type="application/json")
                elif response["mode"] == "mismatch":
                    route.fulfill(status=201, json={**prepared, "id": VERSION})
                elif response["mode"] == "leave":
                    held.append(route)
                else:
                    route.fulfill(status=201, json=prepared)

            def save_draft(route):
                if route.request.method == "PUT":
                    body = route.request.post_data_json
                    saves.append(body)
                    prepared.update(case_context=body["case_context"], items=[{**next(item for item in items if item["file_version_id"] == row["file_version_id"]), **row} for row in body["items"]])
                    route.fulfill(json=prepared)
                    return
                if route.request.method != "GET":
                    route.fulfill(status=405, json={"error": "read_or_save_only"})
                    return
                reads.append((route.request.method, route.request.url.rsplit("/", 1)[1]))
                if response["read_fails"]:
                    route.abort("failed")
                    return
                route.fulfill(json=prepared)

            page.route("**/api/drafts", create_draft)
            page.route("**/api/rules/version-b", lambda route: route.fulfill(json=rule))
            page.route("**/api/drafts/*", save_draft)
            page.route("**/api/drafts/*/round", lambda route: route.fulfill(json=None))
            page.route("**/api/drafts/*/accept", lambda route: (accepted.append(route.request.method), route.fulfill(status=500, json={})))
            page.route("**/api/integrations/feishu/usage/quota?*", lambda route: route.fulfill(json={"accept_allowed": True}))
            for mode in ("normal", "lost", "bad_json", "mismatch", "read_failure", "leave"):
                with self.subTest(mode=mode):
                    posts.clear()
                    saves.clear()
                    reads.clear()
                    held.clear()
                    prepared.clear()
                    response.update(mode=mode, read_fails=mode == "read_failure")
                    page.goto(WEB + "/?round=" + JOB)
                    expect(page.get_by_role("heading", name="审核未能完成", exact=True)).to_be_visible()
                    with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/drafts")):
                        page.get_by_role("button", name="准备新一轮审核", exact=True).click()
                    if mode == "mismatch":
                        expect(page.get_by_text("新轮准备回执与原请求不一致，未切换审核记录。", exact=True)).to_be_visible()
                        expect(page.get_by_role("heading", name="审核未能完成", exact=True)).to_be_visible()
                        page.get_by_role("button", name="准备新一轮审核", exact=True).click()
                    elif mode == "read_failure":
                        expect(page.get_by_role("alert")).to_contain_text("新轮准备结果尚未确认")
                        response["read_fails"] = False
                        page.reload()
                        page.get_by_role("button", name="准备新一轮审核", exact=True).click()
                    elif mode == "leave":
                        deadline = time.monotonic() + 5
                        while not held:
                            self.assertLess(time.monotonic(), deadline)
                            page.wait_for_timeout(10)
                        page.get_by_role("link", name="工作台", exact=True).click()
                        with page.expect_response(lambda reply: reply.request.method == "POST" and reply.url.endswith("/api/drafts")) as late:
                            held[0].fulfill(status=201, json=prepared)
                        late.value.finished()
                        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                        expect(page.get_by_role("heading", name="工作台", exact=True)).to_be_visible()
                        expect(page.get_by_role("heading", name="审核准备", exact=True)).to_have_count(0)
                        self.assertNotIn("case=", page.url)
                        page.goto(WEB + "/?round=" + JOB)
                        page.get_by_role("button", name="准备新一轮审核", exact=True).click()
                    expect(page.get_by_role("heading", name="审核准备", exact=True)).to_be_visible()
                    table = page.get_by_role("table", name="审核资料", exact=True)
                    expect(table).to_contain_text("control-plan.xlsx")
                    expect(table).to_contain_text("process-flow.xlsx")
                    expect(table.get_by_role("checkbox", name="纳入 control-plan.xlsx", exact=True)).to_be_enabled()
                    self.assertEqual(len(posts), 1)
                    self.assertEqual(posts[0]["predecessor_id"], JOB)
                    self.assertEqual(posts[0]["items"], [{key: item[key] for key in ("file_version_id", "role", "rule_version_id")} for item in items])
                    self.assertEqual(prepared["analysis_mode"], "deep")
                    self.assertEqual(set(reads), {("GET", posts[0]["request_id"])})
                    self.assertEqual(saves, [])
                    self.assertEqual(accepted, [])
                    if mode == "normal":
                        with page.expect_response(lambda reply: reply.request.method == "PUT" and reply.url.endswith("/api/drafts/" + posts[0]["request_id"])) as saved:
                            page.get_by_label("客户", exact=True).fill("补充客户")
                        saved.value.finished()
                        self.assertEqual(saves[-1]["items"], posts[0]["items"])
                        page.reload()
                        table = page.get_by_role("table", name="审核资料", exact=True)
                        for name in ("control-plan.xlsx", "process-flow.xlsx", "other-control-plan.xlsx"):
                            expect(table.get_by_role("row").filter(has=page.get_by_role("button", name="阅读 " + name, exact=True)).get_by_text("待审", exact=True)).to_be_visible()
                        expect(table.get_by_role("row").filter(has_text="context.pdf").get_by_text("参考", exact=True)).to_be_visible()
                        self.assertEqual(accepted, [])
            response["task"] = True
            posts.clear()
            page.goto(WEB + "/?round=" + JOB)
            expect(page.get_by_role("heading", name="审核未能完成", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="准备新一轮审核", exact=True)).to_have_count(0)
            self.assertEqual(posts, [])
            browser.close()

    def test_round_read_failure_keeps_acceptance_and_recovers_without_resubmitting(self):
        from playwright.sync_api import expect

        for entry, first_read in (("submit", "connectionreset"), ("deep_link", "connectionreset"), ("submit", "bad_json"), ("deep_link", "bad_json")):
            with self.subTest(entry=entry, first_read=first_read), sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                page.set_default_timeout(4000)
                self.route(page)
                draft_id = "00000000-0000-4000-8000-000000000004"
                rule = {
                    "id": "version-b",
                    "title": "规则 B",
                    "body": BODY,
                    "created_at": "2026-10-18T00:00:00Z",
                    "source_file_id": "rule-source",
                    "source_file_version_id": "rule-source-version",
                    "predecessor_id": None,
                }
                rules = {rule["id"]: rule}
                item = {
                    "file_id": SOURCE,
                    "file_version_id": VERSION,
                    "original_filename": "control-plan.xlsx",
                    "role": "target",
                    "rule_version_id": rule["id"],
                    "status": "ready",
                    "rule": {key: rule[key] for key in ("id", "title", "body")},
                }
                draft = {
                    "id": draft_id,
                    "status": "open",
                    "analysis_mode": "standard",
                    "predecessor_id": None,
                    "updated_at": "2026-10-18T00:00:00Z",
                    "writeback": {"available": False},
                    "items": [item],
                    "case_context": {},
                }
                accepted = {
                    "id": JOB,
                    "source_draft_id": draft_id,
                    "predecessor_id": None,
                    "execution": "queued",
                    "analysis_mode": "standard",
                    "inputs": [item],
                    "findings": [],
                    "coverage": {},
                    "created_at": "2026-10-18T00:00:00Z",
                    "result_attempt": None,
                    "failure": None,
                    "progress": None,
                    "writeback": {"available": False},
                    "case_context": {},
                }
                posts, reads, pending_reads = [], [], {}
                release_reads = False
                page.route("**/api/drafts?*", lambda route: route.fulfill(json={"drafts": [], "next_offset": None}))
                page.route("**/api/rounds?*", lambda route: route.fulfill(json={"rounds": [], "next_offset": None}))
                page.route("**/api/rules", lambda route: route.fulfill(json={"rules": []}))
                page.route("**/api/integrations/feishu/usage/quota?*", lambda route: route.fulfill(json={"accept_allowed": True}))

                def read_rule(route):
                    selected = rules.get(route.request.url.rsplit("/", 1)[1])
                    route.fulfill(status=200 if selected else 404, json=selected or {"error": "not_found"})

                def publish_rule(route):
                    if route.request.method != "POST":
                        route.fulfill(json={"rules": []})
                        return
                    body = route.request.post_data_json
                    published = {**rule, **{key: body[key] for key in ("title", "body", "source_file_version_id", "predecessor_id", "case_context")}, "id": body["request_id"]}
                    rules[published["id"]] = published
                    route.fulfill(status=201, json=published)

                def save_draft(route):
                    if route.request.method == "PUT":
                        body = route.request.post_data_json
                        bound = rules[body["items"][0]["rule_version_id"]]
                        draft.update(body, items=[{**item, **body["items"][0], "rule": {key: bound[key] for key in ("id", "title", "body")}}])
                    route.fulfill(json=draft)

                def accept(route):
                    posts.append(route.request.post_data_json)
                    draft["status"] = "accepted"
                    accepted.update(inputs=draft["items"], case_context=draft["case_context"])
                    route.fulfill(status=201, json=accepted)

                def read_round(route):
                    reads.append((route.request.method, route.request.url.rsplit("/", 1)[1]))
                    if route.request.method != "GET":
                        route.fulfill(status=405, json={"error": "read_only_round"})
                        return
                    if len(reads) == 1:
                        if first_read == "connectionreset":
                            route.abort("connectionreset")
                        else:
                            route.fulfill(status=200, body="{", content_type="application/json")
                    elif release_reads:
                        route.fulfill(json={**accepted, "execution": "completed"})
                    else:
                        pending_reads[route.request] = route

                page.route("**/api/rules", publish_rule)
                page.route("**/api/rules/*", read_rule)
                page.route(
                    "**/api/files/rule-source/versions/rule-source-version",
                    lambda route: route.fulfill(json={"file_id": "rule-source", "id": "rule-source-version", "original_filename": "原始规则.json", "status": "ready"}),
                )
                page.route(
                    "**/api/files/rule-source/versions/rule-source-version/rule-sources",
                    lambda route: route.fulfill(json={"file_version_id": "rule-source-version", "units": [{"id": "B4", "location": "工作表 B4", "excerpt": "检查 A"}]}),
                )
                page.route("**/api/drafts/" + draft_id, save_draft)
                page.route("**/api/drafts/" + draft_id + "/round", lambda route: route.fulfill(json=accepted if posts else None))
                page.route("**/api/drafts/" + draft_id + "/accept", accept)
                page.route("**/api/rounds/" + JOB, read_round)
                page.goto(WEB + ("/?draft=" + draft_id if entry == "submit" else "/?round=" + JOB))
                if entry == "submit":
                    for label, value in (("客户", "测试客户"), ("供应商", "测试供应商"), ("零件与编号", "P-001"), ("图纸修订", "B"), ("生产地点", "测试工厂")):
                        page.get_by_label(label, exact=True).fill(value)
                    page.get_by_role("button", name="核对审核要求", exact=True).click()
                    clause = page.get_by_role("article", name="条款 1", exact=True)
                    expect(clause).to_contain_text("工作表 B4")
                    clause.get_by_role("radio", name="适用", exact=True).check()
                    clause.get_by_role("checkbox", name="1 已核对条款与出处", exact=True).check()
                    page.get_by_role("button", name="确认本次要求", exact=True).click()
                    page.get_by_role("button", name="开始审核", exact=True).click()
                error = "暂时无法连接服务，请重新读取。" if first_read == "connectionreset" else "服务器响应读取失败，请稍后重试。"
                expect(page.get_by_text(error, exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="开始审核", exact=True)).to_have_count(0)
                expect(page.get_by_role("button", name="重试原请求", exact=True)).to_have_count(0)
                if entry == "submit":
                    deadline = time.monotonic() + 10
                    while not pending_reads:
                        self.assertLess(time.monotonic(), deadline, "The automatic poll did not reach its route handler")
                        page.wait_for_timeout(10)
                    expect(page.get_by_text(error, exact=True)).to_be_visible()
                with page.expect_request(lambda request: request.method == "GET" and request.url.endswith("/api/rounds/" + JOB)) as recovery:
                    page.get_by_role("button", name="重新读取", exact=True).click()
                deadline = time.monotonic() + 5
                while recovery.value not in pending_reads:
                    self.assertLess(time.monotonic(), deadline, "The recovery request did not reach its route handler")
                    page.wait_for_timeout(10)
                release_reads = True
                pending_reads.pop(recovery.value).fulfill(json={**accepted, "execution": "completed"})
                expect(page.get_by_text(error, exact=True)).to_have_count(0)
                if entry == "submit":
                    expect(page.get_by_role("heading", name="未获得逐项检查结果", exact=True)).to_be_visible()
                    page.get_by_role("button", name="本轮资料与依据", exact=True).click()
                    expect(page.get_by_role("table", name="审核资料", exact=True)).to_contain_text("control-plan.xlsx")
                else:
                    expect(page.get_by_role("heading", name="人工复核", exact=True)).to_be_visible()
                    expect(page.get_by_role("link", name="下载原件", exact=True)).to_have_attribute("href", WEB + f"/api/rounds/{JOB}/files/{SOURCE}/versions/{VERSION}/download")
                self.assertEqual(len(posts), 1 if entry == "submit" else 0)
                self.assertGreaterEqual(len(reads), 2)
                self.assertEqual(set(reads), {("GET", JOB)})
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
                    if state.get("hold_save_reply"):
                        state.setdefault("held_saves", []).append((route, rule))
                    elif state.get("drop_save_reply"):
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
            job = {
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
            patch = state.get("parse_patch", {})
            for key, value in patch.items():
                if key == "result":
                    result = {**job["result"], **value}
                    if "body" in value:
                        result["body"] = {**job["result"]["body"], **value["body"]}
                    job["result"] = result
                else:
                    job[key] = value
            return job

        def read_parse(route):
            self.assertEqual(route.request.method, "GET")
            route.fulfill(json=parse_job(urlsplit(route.request.url).path.rsplit("/", 1)[1], state["status"]))

        page.route("**/api/files", create_file)
        page.route("**/api/files/*/versions/*", lambda route: route.fulfill(json=state["source"]))
        page.route(
            "**/api/files/*/versions/*/rule-sources",
            lambda route: route.fulfill(
                json={"file_version_id": state["source"]["id"], "units": state.get("source_units", [{"id": "json:0", "location": "rules[0].rule_to_check", "excerpt": "检查 A"}])}
            ),
        )
        page.route("**/api/files/*/versions/*/blob", blob)
        page.route("**/api/rules", rules)
        page.route("**/api/rules/*", rule)
        page.route("**/api/rules/parse", parse)
        page.route("**/api/rules/parse/*", read_parse)
        return state

    def open_requirement(self, page, path="/?view=requirements", *, document_type="控制计划"):
        page.goto(WEB + path)
        page.get_by_label("上传共用要求文件").set_input_files({"name": "A.json", "mimeType": "application/json", "buffer": b"{}"})
        page.get_by_label("适用资料类型").select_option(document_type)
        page.get_by_role("button", name="提取条款", exact=True).click()

    def check_requirement(self, page):
        from playwright.sync_api import expect

        expect(page.get_by_label("要求名称", exact=True)).to_have_value("规则 A")
        page.get_by_label("要求名称", exact=True).fill("已核对要求")
        for label in ("不限定客户", "不限定零件", "不限定图纸修订"):
            page.get_by_label(label, exact=True).check()
        page.get_by_label("来源说明／变更原因", exact=True).fill("已按原件核对。")
        clause = page.get_by_role("article", name="条款 1", exact=True)
        expect(clause).to_contain_text("rules[0].rule_to_check")
        clause.get_by_label("1 已核对来源", exact=True).check()
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
