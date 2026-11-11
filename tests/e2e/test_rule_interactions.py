"""Deterministic browser/API interleavings; no live backend or model calls.

The retained Draft upload/receipt consumer is exercised at /dev. Customer home
and Task navigation use the unified workbench; these are separate contracts.
"""

import os
import re
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .conftest import pick_audit_files  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional until the e2e job installs Playwright
    sync_playwright = None

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


def open_customer_requirements(page):
    from playwright.sync_api import expect

    button = page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="审核规则", exact=True)
    menu = page.get_by_role("button", name="打开产品导航", exact=True)
    # The login shell also has a banner; wait for authorized product navigation.
    button.or_(menu).wait_for(state="visible")
    if button.is_visible():
        button.click()
    else:
        menu.click()
        page.get_by_role("dialog", name="产品导航", exact=True).get_by_role("button", name="审核规则", exact=True).click()
    expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()


def back_to_templates(page):
    """Rules sit in templates; the trail's first link returns to the template list."""
    from playwright.sync_api import expect

    page.get_by_role("navigation", name="位置", exact=True).get_by_role("link", name="规则模板", exact=True).click()
    expect(page.get_by_role("searchbox", name="搜索规则模板", exact=True)).to_be_visible()


def find_rule_template(page, title):
    """Search the template list for a rule and return the one matching template link."""
    from playwright.sync_api import expect

    search = page.get_by_role("searchbox", name="搜索规则模板", exact=True)
    search.fill(title)
    template = page.get_by_role("main").get_by_role("listitem").get_by_role("link").first
    expect(template).to_be_visible()
    return template


def open_rule(page, title):
    """Open a rule the way a person does: template list, then its file type in that template."""

    if page.get_by_role("navigation", name="位置", exact=True).count():
        back_to_templates(page)
    find_rule_template(page, title).click()
    row = page.get_by_role("row").filter(has_text=title).get_by_role("link")
    item = page.get_by_role("link").filter(has_text=title).filter(visible=True)
    row.or_(item).first.click()


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
    def test_rule_navigation_waits_for_authorized_shell_on_desktop_and_mobile(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for width in (1440, 390):
                with self.subTest(width=width):
                    page = browser.new_page(viewport={"width": width, "height": 1000})
                    try:
                        state = self.requirement_routes(page)
                        held, probes = [], []
                        page.route("**/api/me", lambda route: held.append(route))
                        page.goto(WEB + "/audit")
                        expect(page.get_by_text("正在确认登录状态…", exact=True)).to_be_visible()
                        self.assertEqual(len(held), 1)
                        self.assertTrue(page.get_by_role("banner").is_visible())
                        desktop = page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="审核规则", exact=True)
                        mobile = page.get_by_role("button", name="打开产品导航", exact=True)
                        self.assertFalse(desktop.is_visible())
                        self.assertFalse(mobile.is_visible())

                        def authorize():
                            # Keep the first readiness probe in the login shell. The next
                            # probe releases the existing /me fixture, without a new login.
                            probes.append(True)
                            if len(probes) == 2:
                                self.assertFalse(desktop.is_visible())
                                self.assertFalse(mobile.is_visible())
                                held.pop().fallback()

                        page.add_locator_handler(page.get_by_text("正在确认登录状态…", exact=True), authorize, no_wait_after=True, times=2)
                        open_customer_requirements(page)
                        expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
                        self.assertEqual(len(probes), 2)
                        self.assertEqual(held, [])
                        self.assertEqual(state["writes"], [])
                    finally:
                        page.close()
            browser.close()

    def test_native_history_timeout_offers_explicit_read_of_the_same_document(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
            page.clock.install()
            reads, ready = [], False

            def history(route):
                reads.append(route.request.url)
                if ready:
                    route.fulfill(
                        json={
                            "document": {
                                "document_id": "history-document",
                                "provider": "feishu",
                                "title": "历史原件.xlsx",
                                "metadata": {},
                                "document_type": None,
                                "updated_at": "2026-10-01T00:00:00Z",
                                "latest_completed": None,
                                "latest_activity": None,
                            },
                            "entries": [],
                            "default_reference": None,
                        }
                    )

            page.route("**/api/audit-archive/history-document/history", history)
            with page.expect_request("**/api/audit-archive/history-document/history"):
                page.goto(WEB + "/files?archiveDoc=history-document")
            expect(page.get_by_text("正在读取这次审核…", exact=True)).to_be_visible()
            page.clock.fast_forward(45_000)
            expect(page.get_by_role("alert")).to_contain_text("读取审核文件超时，请重新读取。")
            self.assertEqual(len(reads), 1)
            ready = True
            page.get_by_role("button", name="重新读取这次审核", exact=True).click()
            expect(page.get_by_role("heading", name="历史原件.xlsx", exact=True)).to_be_visible()
            expect(page.get_by_text("尚无完成审核。可以选择已有记录查看进度或异常。", exact=True)).to_be_visible()
            self.assertEqual(len(reads), 2)
            self.assertEqual(reads[0], reads[1])
            browser.close()

    def test_native_history_does_not_read_the_unused_customer_catalog(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
            pending_catalog, reads = [], []
            page.on("request", lambda request: reads.append(urlsplit(request.url).path))
            page.route("**/api/audit-customers", lambda route: pending_catalog.append(route))
            page.route(
                "**/api/audit-archive/history-document/history",
                lambda route: route.fulfill(
                    json={
                        "document": {
                            "document_id": "history-document",
                            "provider": "feishu",
                            "title": "历史原件.xlsx",
                            "metadata": {},
                            "document_type": "控制计划",
                            "updated_at": "2026-10-01T00:00:00Z",
                            "latest_completed": None,
                            "latest_activity": None,
                        },
                        "entries": [],
                        "default_reference": None,
                    }
                ),
            )
            page.goto(WEB + "/audit?view=archive&archiveDoc=history-document")
            expect(page.get_by_role("heading", name="历史原件.xlsx", exact=True)).to_be_visible()
            expect(page.get_by_text("尚无完成审核。可以选择已有记录查看进度或异常。", exact=True)).to_be_visible()
            self.assertEqual(len(pending_catalog), 0)
            self.assertEqual(reads.count("/api/audit-archive/history-document/history"), 1)
            self.assertNotIn("/api/audit-archive", reads)
            self.assertNotIn("/api/audit-tasks", reads)
            expect(page.get_by_role("heading", name="历史原件.xlsx", exact=True)).to_be_visible()
            browser.close()

    def test_native_rule_directory_reads_only_selected_versions_and_ignores_late_reads(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)
            state["source"].update(id=VERSION, file_id=SOURCE, original_filename="规则原件.json", content_type="application/json", byte_size=2, sha256=None, status="ready")
            for identity, title, text, at, parent in (
                ("rule-a1", "规则 A", "旧版条款 A", "2026-10-16T00:00:00Z", None),
                ("rule-a2", "规则 A", "新版条款 A", "2026-10-18T00:00:00Z", "rule-a1"),
                ("rule-b1", "规则 B", "条款 B", "2026-10-17T00:00:00Z", None),
            ):
                state["rules"][identity] = {
                    "id": identity,
                    "title": title,
                    "created_at": at,
                    "predecessor_id": parent,
                    "body": {**BODY, "rules": [{**BODY["rules"][0], "rule_to_check": text}]},
                    "source_file_id": SOURCE,
                    "source_file_version_id": VERSION,
                }
            reads, held = [], []
            page.on("request", lambda request: reads.append(urlsplit(request.url).path.removeprefix("/api")))
            # Deliberately retain the transport after cancellation: the commit gate must still reject it.
            page.add_init_script(
                "const realFetch = window.fetch.bind(window);window.fetch = (url, init) => realFetch(url, /\\/(rules|files)\\//.test(String(url)) ? {...init, signal: undefined} : init)"
            )
            page.route("**/api/rules/rule-b1", lambda route: held.append(route))
            page.goto(WEB + "/requirements?rulesArea=requirements")
            find_rule_template(page, "规则 B")
            self.assertEqual(reads.count("/rule-library"), 1)
            self.assertFalse(any(path.startswith(("/rules/", "/files/")) for path in reads))
            open_rule(page, "规则 A")
            expect(page.get_by_role("article", name="条款 1", exact=True)).to_contain_text("新版条款 A")
            self.assertEqual([path for path in reads if path.startswith("/rules/")], ["/rules/rule-a2"])
            page.get_by_role("tab", name="版本记录", exact=True).click()
            expect(page.get_by_role("region", name="版本差异", exact=True)).to_contain_text("旧版条款 A")
            self.assertNotIn("/rules/rule-b1", reads)
            open_rule(page, "规则 B")
            expect(page.get_by_role("status", name="正在读取…", exact=True)).to_be_visible()
            open_rule(page, "规则 A")
            expect(page.get_by_role("article", name="条款 1", exact=True)).to_contain_text("新版条款 A")
            self.assertEqual(len(held), 1)
            with page.expect_response(lambda response: response.url.endswith("/api/rules/rule-b1")) as late:
                held[0].fulfill(json=state["rules"]["rule-b1"])
            late.value.finished()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            expect(page.get_by_role("heading", name="规则 A", exact=True)).to_be_visible()
            expect(page.get_by_role("article", name="条款 1", exact=True)).to_contain_text("新版条款 A")
            self.assertEqual(state["writes"], [])
            browser.close()

    def test_native_rule_customer_scope_is_explained_without_publishing_or_replacing_the_draft(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)
            self.open_requirement(page)
            expect(page.get_by_label("规则名称", exact=True)).to_have_value("规则 A")
            notice = page.get_by_role("status").filter(has_text="还没选模板")
            expect(notice).to_be_visible()
            page.get_by_label("放入通用规则", exact=True).check()
            expect(notice).to_have_count(0)
            page.get_by_label("放入通用规则", exact=True).uncheck()
            expect(notice).to_be_visible()
            page.get_by_label("所属模板", exact=True).fill("指定客户")
            expect(notice).to_have_count(0)
            expect(page.get_by_label("规则名称", exact=True)).to_have_value("规则 A")
            self.assertEqual(state["saves"], [])
            browser.close()

    def test_json_type_conflict_allows_explicit_correction_without_losing_the_source(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page, status="queued")
            self.native_rule_routes(page, state)
            self.open_requirement(page, document_type="全功能检验报告")
            state["status"] = "failed"
            state["parse_patch"] = {"result": {"error": "rule_document_type_mismatch"}}
            page.get_by_role("button", name="刷新提取进度", exact=True).click()
            expect(page.get_by_role("alert").filter(has_text="原件中的资料类型与所选类型不同")).to_be_visible()
            page.get_by_label("适用资料类型").click()
            page.get_by_role("option", name="控制计划", exact=True).click()
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
                    self.open_requirement(page)
                    page.get_by_label("规则名称", exact=True).fill("保留的草稿名称")
                    page.get_by_role("textbox", name="这次改了什么", exact=True).fill("保留的来源说明")
                    state["parse_patch"] = patch
                    state["status"] = "succeeded"
                    page.get_by_role("button", name="刷新提取进度", exact=True).click()
                    expect(page.get_by_role("alert").filter(has_text="提取结果与当前原件或资料类型不一致")).to_be_visible()
                    expect(page.get_by_label("规则名称", exact=True)).to_have_value("保留的草稿名称")
                    expect(page.get_by_role("textbox", name="这次改了什么", exact=True)).to_have_value("保留的来源说明")
                    expect(page.get_by_role("article", name="条款 1", exact=True)).to_have_count(0)
                    expect(page.get_by_text("正在提取条款，完成后逐条核对原文。", exact=True)).to_have_count(0)
                    self.assertEqual(len(state["parses"]), 1)
                    self.assertEqual(state["saves"], [])
                    state["parse_patch"] = {}
                    page.get_by_role("button", name="查看提取进度", exact=True).click()
                    expect(page.get_by_role("article", name="条款 1", exact=True)).to_contain_text("检查 A")
                    expect(page.get_by_text("提取结果与当前原件或资料类型不一致", exact=False)).to_have_count(0)
                    expect(page.get_by_role("textbox", name="这次改了什么", exact=True)).to_have_value("保留的来源说明")
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
            self.open_requirement(page)
            self.check_requirement(page)
            address = page.url
            page.evaluate("""() => {
                const key = Object.keys(localStorage).find(key => key.endsWith(':requirement-library'));
                const saved = JSON.parse(localStorage.getItem(key));
                const basis = saved.value.collections.find(item => item.draft).draft.basis;
                delete basis.server.sources;
                for (const clause of basis.clauses) {
                    delete clause.provenance;
                    clause.location = `条款 ${clause.id}（原件位置待确认）`;
                }
                localStorage.setItem(key, JSON.stringify(saved));
            }""")
            page.reload()
            expect(page.get_by_label("规则名称", exact=True)).to_have_value("已核对要求")
            expect(page.get_by_role("textbox", name="这次改了什么", exact=True)).to_have_value("已按原件核对。")
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
            self.open_requirement(page)
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
            # Scope and template are still missing: saving marks them and sends nothing.
            page.get_by_role("button", name="保存版本", exact=True).click()
            expect(page.get_by_role("status").filter(has_text=re.compile(r"^还差 \d+ 处$"))).to_be_visible()
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
            page.goto(WEB + "/requirements?rulesArea=requirements")
            with page.expect_request(lambda request: request.method == "PUT" and request.url.endswith("/blob")):
                page.get_by_label("上传共用规则文件").set_input_files({"name": "迟到规则.json", "mimeType": "application/json", "buffer": b"{}"})
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="工作台", exact=True).click()
            address = page.url
            held[0].fulfill(json={**state["source"], "status": "ready"})
            # The late reply lands as an unsorted draft in the hidden rules page, without leaving this page.
            page.locator("a").filter(has_text="待整理").first.wait_for(state="attached")
            expect(page.get_by_role("heading", name="工作台", exact=True)).to_be_visible()
            self.assertEqual(page.url, address)
            open_customer_requirements(page)
            find_rule_template(page, "迟到规则")
            self.assertEqual(state["saves"], [])
            browser.close()

    def test_terminal_rule_read_survives_a_late_queued_acceptance_receipt(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for status in ("succeeded", "failed"):
                with self.subTest(status=status):
                    page = browser.new_page()
                    # Deliver both decoded responses in one microtask turn, before
                    # React can commit the terminal GET to the requirement basis.
                    page.add_init_script("""(() => {
                        const fetch = window.fetch; let release;
                        window.parseReceiptOrder = [];
                        window.fetch = async (...args) => {
                            const response = await fetch(...args), url = String(args[0]);
                            const json = response.json.bind(response);
                            if (url.endsWith('/rules/parse') && args[1]?.method === 'POST') {
                                response.json = async () => {
                                    const value = await json();
                                    return await new Promise(resolve => {release = () => {
                                        window.parseReceiptOrder.push(value.status); resolve(value);
                                    }});
                                };
                            } else if (url.includes('/rules/parse/')) {
                                response.json = async () => {
                                    const value = await json();
                                    if (['succeeded', 'failed'].includes(value.status) && release) {
                                        window.parseReceiptOrder.push(value.status);
                                        const complete = release; release = null; queueMicrotask(complete);
                                    }
                                    return value;
                                };
                            }
                            return response;
                        };
                    })()""")
                    state = self.requirement_routes(page, status=status)
                    self.open_requirement(page)
                    page.wait_for_function("window.parseReceiptOrder.length === 2")
                    self.assertEqual(page.evaluate("window.parseReceiptOrder"), [status, "queued"])
                    if status == "succeeded":
                        expect(page.get_by_label("规则名称", exact=True)).to_have_value("规则 A")
                        expect(page.get_by_role("article", name="条款 1", exact=True)).to_be_visible()
                    else:
                        expect(page.get_by_role("alert").filter(has_text="要求提取失败")).to_be_visible()
                        expect(page.get_by_role("button", name="重新提取条款", exact=True)).to_be_enabled()
                    expect(page.get_by_text("正在提取条款，完成后逐条核对原文。", exact=True)).to_have_count(0)
                    self.assertEqual(len(state["parses"]), 1)
                    self.assertEqual(state["saves"], [])
                    page.close()
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
                    self.open_requirement(page)
                    self.check_requirement(page)
                    state["hold_save_reply"] = True
                    with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/rules")):
                        page.get_by_role("button", name="保存版本", exact=True).click()
                    page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="工作台", exact=True).click()
                    if destination == "list":
                        open_customer_requirements(page)
                    elif destination == "same-draft":
                        page.go_back()
                    address = page.url
                    reply, rule = state["held_saves"][0]
                    with page.expect_response("**/api/audit-customers"):
                        reply.fulfill(status=201, json=rule)
                    if destination == "completed-then-back":
                        expect(page.get_by_role("heading", name="工作台", exact=True)).to_be_visible()
                        self.assertEqual(page.url, address)
                        page.go_back()
                    if destination in {"same-draft", "completed-then-back"}:
                        expect(page.get_by_role("heading", name="已核对要求", exact=True)).to_be_visible()
                        self.assertIn("requirement=" + rule["id"], page.url)
                        self.assertIn("reqVersion=" + rule["id"], page.url)
                        back_to_templates(page)
                    else:
                        expect(page.get_by_role("heading", name="审核规则" if destination == "list" else "工作台", exact=True)).to_be_visible()
                        self.assertEqual(page.url, address)
                    if destination == "audit":
                        open_customer_requirements(page)
                    find_rule_template(page, "已核对要求")
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
            self.open_requirement(page)
            self.check_requirement(page)
            page.get_by_role("button", name="保存版本", exact=True).click()
            expect(page.get_by_role("button", name="创建新版本", exact=True)).to_be_visible()
            page.get_by_role("button", name="创建新版本", exact=True).click()
            page.get_by_label("版本号", exact=True).fill("v2")
            page.get_by_label("这次改了什么", exact=True).fill("第二次核对")
            page.get_by_role("checkbox", name="1 已核对来源", exact=True).check()
            page.get_by_role("button", name="保存版本", exact=True).click()
            expect(page.get_by_role("button", name="创建新版本", exact=True)).to_be_visible()
            page.get_by_role("button", name="创建新版本", exact=True).click()
            page.get_by_label("版本号", exact=True).fill("v3")
            address = page.url
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="工作台", exact=True).click()
            page.go_back()
            expect(page.get_by_label("版本号", exact=True)).to_have_value("v3")
            self.assertEqual(page.url, address)
            self.assertEqual(len(state["saves"]), 2)
            browser.close()

    def test_identical_source_read_after_version_confirmation_keeps_the_save_action_available(self):
        self.check_source_reply_after_version_confirmation(matching=True)

    def test_changed_source_validity_after_version_confirmation_still_blocks_publication(self):
        self.check_source_reply_after_version_confirmation(matching=False)

    def check_source_reply_after_version_confirmation(self, *, matching):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)
            self.open_requirement(page)
            self.check_requirement(page)
            page.get_by_role("button", name="保存版本", exact=True).click()
            expect(page.get_by_role("button", name="创建新版本", exact=True)).to_be_visible()
            held = []
            page.route("**/api/files/*/versions/*/rule-sources", lambda route: held.append(route))
            with page.expect_request("**/api/files/*/versions/*/rule-sources"):
                page.get_by_role("button", name="创建新版本", exact=True).click()
            page.get_by_label("版本号", exact=True).fill("v2")
            reason = page.get_by_role("textbox", name="这次改了什么", exact=True)
            reason.fill("第二次核对")
            page.get_by_role("checkbox", name="1 已核对来源", exact=True).check()
            save = page.get_by_role("button", name="保存版本", exact=True)
            expect(save).to_be_enabled()
            self.assertEqual(len(held), 1)
            units = [{"id": "json:0", "location": "rules[0].rule_to_check", "excerpt": "检查 A"}] if matching else []
            with page.expect_response("**/api/files/*/versions/*/rule-sources") as response:
                held[0].fulfill(json={"file_version_id": state["source"]["id"], "units": units})
            response.value.finished()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            expect(page.get_by_label("版本号", exact=True)).to_have_value("v2")
            expect(reason).to_have_value("第二次核对")
            if not matching:
                expect(page.get_by_label("1 已核对来源", exact=True)).to_be_disabled()
                # Saving marks what is missing in place and sends nothing.
                save.click()
                expect(page.get_by_text("还差 1 处", exact=True)).to_be_visible()
                self.assertEqual(len(state["saves"]), 1)
                browser.close()
                return
            expect(page.get_by_label("1 已核对来源", exact=True)).to_be_checked()
            reason.fill("用户补充版本说明")
            save.click()
            expect(page.get_by_role("button", name="创建新版本", exact=True)).to_be_visible()
            self.assertEqual(len(state["saves"]), 2)
            self.assertNotEqual(state["saves"][0]["request_id"], state["saves"][1]["request_id"])
            browser.close()

    def test_late_published_version_read_does_not_replace_current_draft_edits(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)
            page.add_init_script(
                "const realFetch = window.fetch.bind(window);window.fetch = (url, init) => realFetch(url, /\\/(rules|files)\\//.test(String(url)) ? {...init, signal: undefined} : init)"
            )
            self.open_requirement(page)
            self.check_requirement(page)
            page.get_by_role("button", name="保存版本", exact=True).click()
            expect(page.get_by_role("button", name="创建新版本", exact=True)).to_be_visible()
            published = next(iter(state["rules"].values()))
            page.get_by_role("button", name="创建新版本", exact=True).click()
            page.get_by_label("版本号", exact=True).fill("v2")
            expect(page.get_by_label("版本号", exact=True)).to_have_value("v2")
            held = []
            page.route("**/api/rules/" + published["id"], lambda route: held.append(route))
            page.get_by_role("tab", name="版本记录", exact=True).click()
            expect(page.get_by_role("status", name="正在读取…", exact=True)).to_be_visible()
            open_rule(page, "已核对要求")
            expect(page.get_by_label("版本号", exact=True)).to_have_value("v2")
            page.get_by_label("这次改了什么", exact=True).fill("等待旧版本期间补充的说明")
            expect(page.get_by_label("版本号", exact=True)).to_have_value("v2")
            self.assertEqual(len(held), 1)
            with page.expect_response(lambda response: response.url.endswith("/api/rules/" + published["id"])) as late:
                held[0].fulfill(json=published)
            late.value.finished()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            expect(page.get_by_label("版本号", exact=True)).to_have_value("v2")
            expect(page.get_by_role("textbox", name="这次改了什么", exact=True)).to_have_value("等待旧版本期间补充的说明")
            self.assertEqual(len(state["saves"]), 1)
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
                    page.goto(WEB + "/requirements?rulesArea=requirements")
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
                    expect(page).to_have_url(WEB + "/requirements")
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
            self.upload_preparation_routes(page)

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
            pick_audit_files(page, {"name": "未知回执.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"})
            retry = page.get_by_role("button", name="重新读取 未知回执.xlsx 检查结果", exact=True)
            expect(retry).to_be_enabled()
            open_customer_requirements(page)
            page.get_by_label("上传共用规则文件").set_input_files({"name": "新规则.json", "mimeType": "application/json", "buffer": b"{}"})
            page.get_by_label("适用资料类型").click()
            page.get_by_role("option", name="控制计划", exact=True).click()
            page.get_by_role("button", name="提取条款", exact=True).click()
            self.check_requirement(page)
            with page.expect_response("**/api/audit-customers"):
                page.get_by_role("button", name="保存版本", exact=True).click()
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="工作台", exact=True).click()
            page.get_by_role("button", name="继续当前审核", exact=True).click()
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
            open_customer_requirements(page)
            expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
            bar = page.get_by_role("banner")
            expect(bar.get_by_role("button", name="上传规则", exact=True)).to_be_visible()
            expect(page.get_by_role("status").filter(has_text="份要求")).to_have_count(0)
            with page.expect_file_chooser():
                bar.get_by_role("button", name="上传规则", exact=True).click()
            page.get_by_label("上传共用规则文件").set_input_files({"name": "客户规则.json", "mimeType": "application/json", "buffer": b"{}"})
            expect(bar.get_by_role("button", name="上传规则", exact=True)).to_have_count(0)
            page.get_by_label("适用资料类型").click()
            page.get_by_role("option", name="控制计划", exact=True).click()
            page.get_by_role("button", name="提取条款", exact=True).click()
            self.check_requirement(page)
            page.get_by_label("放入通用规则", exact=True).uncheck()
            page.get_by_label("所属模板", exact=True).fill("测试客户")
            page.get_by_role("button", name="保存版本", exact=True).click()
            expect(page.get_by_role("status").filter(has_text="版本 v1 已保存")).to_be_visible()
            self.assertEqual(len(state["parses"]), 1)
            self.assertEqual(len(state["saves"]), 1)
            self.assertEqual(state["saves"][0]["body"]["customers"], ["测试客户"])
            self.assertEqual(state["saves"][0]["source_file_version_id"], state["source"]["id"])
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="工作台", exact=True).click()
            page.get_by_role("button", name="继续当前审核", exact=True).click()
            page.get_by_role("combobox", name="审核客户", exact=True).click()
            expect(page.get_by_role("option", name="测试客户", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            open_customer_requirements(page)
            open_rule(page, "已核对要求")
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
            page.goto(WEB + "/requirements?rulesArea=requirements")
            expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
            page.get_by_label("上传共用规则文件").set_input_files({"name": "草稿规则.json", "mimeType": "application/json", "buffer": b"{}"})
            page.get_by_label("规则名称", exact=True).fill("未发布的规则草稿")
            page.get_by_role("button", name="打开产品导航", exact=True).click()
            page.get_by_role("dialog").get_by_role("button", name="工作台", exact=True).click()
            expect(page.get_by_role("heading", name="工作台", exact=True)).to_be_visible()
            open_customer_requirements(page)
            open_rule(page, "未发布的规则草稿")
            expect(page.get_by_label("规则名称", exact=True)).to_have_value("未发布的规则草稿")
            page.reload()
            expect(page.get_by_label("规则名称", exact=True)).to_have_value("未发布的规则草稿")
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
                    "customers": [{"customer_id": name, "name": name} for name in customers],
                }
            )

        page.route("**/api/suites", lambda route: route.fulfill(json={"suites": [], "predefined": []}))
        page.route("**/api/audit-customers", catalog)
        page.route("**/api/audit-tasks?*", lambda route: route.fulfill(json={"tasks": [], "next_offset": None}))

        def library(route):
            import json

            versions = []
            for rule in state["rules"].values():
                registration = json.loads(rule.get("case_context", {}).get("registration") or "{}")
                scope = registration.get("scope", {"customer": "", "part": "", "revision": "", "allCustomers": False, "allParts": True, "allRevisions": True})
                versions.append(
                    {
                        "id": rule["id"],
                        "title": rule["title"],
                        "version": registration.get("basis", {}).get("source", {}).get("version", rule["created_at"][:16].replace("T", " ")),
                        "scope": scope,
                        "reason": registration.get("reason", ""),
                        "created_at": rule["created_at"],
                        "predecessor_id": rule.get("predecessor_id"),
                        "clause_count": len(rule["body"]["rules"]),
                        **({"suite_key": rule["body"]["suite_key"]} if rule["body"].get("suite_key") else {}),
                    }
                )
            route.fulfill(json={"versions": versions})

        page.route("**/api/rule-library", library)

    def test_built_in_rules_form_one_system_template_per_customer(self):
        from audit.presets import customer_presets, preset_rule_body
        from playwright.sync_api import expect
        from product.rules import document_types

        rules, details = [], {}
        for preset in customer_presets():
            body = preset_rule_body(preset.customer, preset.entry_id)
            identity = {"id": preset.entry_id, "title": preset.title, "kind": "engine", "created_at": "2026-10-01T00:00:00Z", "predecessor_id": None}
            listed = {"customer_scope": "restricted", "type": body["type"], "rule_profile": "v4", "customers": body["customers"], "models": [], "parts": []}
            rules.append({**identity, **listed, "preset_key": body.get("preset_key", preset.entry_id), "clause_count": len(body["rules"])})
            detail = {"source_file_version_id": None, "source_file_id": None, "created_by": None, "case_context": {}, "customer_scope": "restricted"}
            details[preset.entry_id] = {**identity, "body": body, **detail}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)
            page.route("**/api/rules", lambda route: route.fulfill(json={"rules": rules}))
            page.route("**/api/rule-types", lambda route: route.fulfill(json={"types": document_types()}))
            page.route("**/api/suites", lambda route: route.fulfill(json={"suites": [], "predefined": []}))

            def preset_detail(route):
                found = details.get(urlsplit(route.request.url).path.rsplit("/", 1)[1])
                route.fulfill(json=found) if found else route.fallback()

            page.route("**/api/rules/*", preset_detail)
            page.goto(WEB + "/requirements")
            system = page.get_by_role("region", name="系统提供", exact=True)
            for customer, count in (("UAES", 19), ("理想", 17), ("蔚来", 17)):
                expect(system.get_by_role("link").filter(has_text=f"{customer} 规则").filter(has_text=f"{count} 类文件")).to_be_visible()
            expect(page.get_by_text("检查功能尚待完善")).to_have_count(0)
            system.get_by_role("link").filter(has_text="UAES 规则").click()
            expect(page.get_by_role("heading", name="UAES 规则", exact=True)).to_be_visible()
            photo = page.get_by_role("row").filter(has_text="生产件样品照片")
            expect(photo).to_contain_text("尚无检查程序")
            expect(page.get_by_text(re.compile("本模板没有这些文件的规则：.*检具验收报告"))).to_be_visible()
            photo.get_by_role("link").click()
            expect(page.get_by_role("navigation", name="位置", exact=True)).to_contain_text("UAES 规则")
            expect(page.get_by_role("region", name="规则条款", exact=True).get_by_role("article").first).to_be_visible()
            page.get_by_role("navigation", name="位置", exact=True).get_by_role("link", name="UAES 规则", exact=True).click()
            page.get_by_role("navigation", name="位置", exact=True).get_by_role("link", name="规则模板", exact=True).click()
            page.get_by_role("link").filter(has_text="蔚来 规则").click()
            expect(page.get_by_role("main")).not_to_contain_text("生产件样品照片")
            browser.close()

    def test_native_rule_catalog_refresh_failure_keeps_the_saved_version(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)
            self.open_requirement(page)
            self.check_requirement(page)
            state["catalog_failed"] = True
            page.get_by_role("button", name="保存版本", exact=True).click()
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

    def upload_preparation_routes(self, page):
        preparation = {}

        def save_preparation(route):
            value = route.request.post_data_json
            if route.request.method == "POST":
                self.assertEqual(preparation, {})
                preparation.update(
                    id=value["request_id"],
                    task_id=None,
                    status="open",
                    preparation_revision=0,
                    items=[],
                    analysis_mode="standard",
                    predecessor_id=None,
                    case_context={},
                    writeback={"available": False},
                    updated_at="2026-10-22T00:00:00Z",
                )
            else:
                self.assertEqual(route.request.method, "PUT")
                self.assertEqual(route.request.url.rsplit("/", 1)[1], preparation["id"])
                self.assertEqual(value["expected_revision"], preparation["preparation_revision"])
                preparation["preparation_revision"] += 1
            preparation["upload_preparation"] = value["upload_preparation"]
            route.fulfill(json=preparation)

        page.route("**/api/drafts", save_preparation)
        page.route("**/api/drafts/*", save_preparation)
        return preparation

    def test_native_rule_upload_keeps_unconfirmed_audit_files_and_selection(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            state = self.requirement_routes(page)
            self.native_rule_routes(page, state)
            preparation = self.upload_preparation_routes(page)

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
            pick_audit_files(page, {"name": "保留选择.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"})
            selected = page.get_by_role("checkbox", name="选择 保留选择.xlsx", exact=True)
            expect(selected).to_be_checked()
            selected.uncheck()
            upload_identity = page.evaluate("history.state.auditUploadDraft")
            self.assertTrue(upload_identity)
            open_customer_requirements(page)
            page.get_by_label("上传共用规则文件").set_input_files({"name": "新增规则.json", "mimeType": "application/json", "buffer": b"{}"})
            page.get_by_label("规则名称", exact=True).fill("待核对规则")
            page.get_by_role("tab", name="版本记录", exact=True).click()
            back_to_templates(page)
            page.get_by_role("searchbox", name="搜索规则模板", exact=True).fill("待核对")
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="工作台", exact=True).click()
            page.get_by_role("button", name="继续当前审核", exact=True).click()
            expect(page.get_by_role("heading", name="核对本次文件", exact=True)).to_be_visible()
            expect(selected).not_to_be_checked()
            self.assertEqual(page.evaluate("history.state.auditUploadDraft"), upload_identity)
            self.assertEqual(state["parses"], [])
            self.assertEqual(state["saves"], [])
            self.assertFalse(any(path.startswith(("/audit-tasks", "/rounds")) for method, path in state["writes"]))
            self.assertEqual(preparation["id"], upload_identity)
            self.assertEqual(preparation["upload_preparation"]["files"][0]["selection"], "unselected")
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
            selected = page.get_by_role("tab", name="全部任务", exact=False)
            expect(selected).to_have_attribute("aria-selected", "true")
            expect(selected).to_have_text("全部任务")
            selected.click()
            self.assertEqual(len(pending), 2)
            for route, body in pending:
                route.fulfill(json=body)
            expect(page.get_by_role("button", name="刷新任务列表", exact=True)).to_be_enabled()
            expect(selected).to_have_attribute("aria-selected", "true")
            expect(selected).to_have_text("全部任务1")
            expect(page.get_by_role("tab", name="进行中", exact=False)).to_have_text("进行中0")
            expect(page.get_by_role("table", name="全部任务审核记录", exact=True)).to_contain_text("history.xlsx")
            self.assertEqual(writes, [])
            browser.close()

    def test_draft_network_failure_has_chinese_message(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
            drafts_failed = True
            tasks_failed = False
            writes = []
            page.on("request", lambda request: writes.append(request.url) if request.method in ("POST", "PUT", "PATCH", "DELETE") else None)
            page.route("**/api/rules", lambda route: route.fulfill(json={"rules": []}))

            def read_tasks(route):
                if tasks_failed:
                    route.abort("failed")
                else:
                    route.fulfill(json={"tasks": [], "next_offset": None})

            page.route("**/api/audit-tasks?*", read_tasks)
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
                                    # Only upload preparations are listed; drafts of the retired pages are not.
                                    "upload_preparation": {"analysis_mode": "standard", "customer_preference": None, "files": [], "predecessor_id": None, "uploads": []},
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
            table = page.get_by_role("table", name="全部任务审核记录", exact=True)
            expect(table.get_by_text("已读取的审核.xlsx", exact=True)).to_be_visible()
            expect(page.get_by_text("已读取的记录仍可打开；状态可能已变化。", exact=True)).to_be_visible()
            expect(page.get_by_role("tab", name="全部任务", exact=False)).to_have_text("全部任务")
            drafts_failed = False
            page.get_by_role("button", name="刷新任务列表", exact=True).click()
            expect(alert).to_have_count(0)
            expect(table.get_by_text("恢复的准备.xlsx", exact=True)).to_be_visible()
            expect(table.get_by_text("已读取的审核.xlsx", exact=True)).to_be_visible()
            expect(page.get_by_role("tab", name="全部任务", exact=False)).to_have_text("全部任务2")
            # Failed refreshes preserve a previously complete snapshot, including
            # its known count. A partial first read above cannot do the same.
            drafts_failed = True
            page.get_by_role("button", name="刷新任务列表", exact=True).click()
            expect(alert).to_be_visible()
            expect(table.get_by_text("恢复的准备.xlsx", exact=True)).to_be_visible()
            expect(table.get_by_text("已读取的审核.xlsx", exact=True)).to_be_visible()
            expect(page.get_by_role("tab", name="全部任务", exact=False)).to_have_text("全部任务2")
            # Prior success for another search does not prove that this search's
            # task source was read, even when both legacy sources now succeed.
            drafts_failed, tasks_failed = False, True
            with page.expect_response(lambda response: "/api/rounds?" in response.url and "q=another-search" in response.url) as searched:
                page.get_by_role("textbox", name="搜索审核任务", exact=True).fill("another-search")
            searched.value.finished()
            expect(page.get_by_role("alert").filter(has_text="任务列表读取失败。当前文件与任务仍保留。")).to_be_visible()
            expect(alert).to_have_count(0)
            expect(table.get_by_text("恢复的准备.xlsx", exact=True)).to_be_visible()
            expect(table.get_by_text("已读取的审核.xlsx", exact=True)).to_be_visible()
            expect(page.get_by_role("tab", name="全部任务", exact=False)).to_have_text("全部任务")
            tasks_failed = False
            page.get_by_role("button", name="刷新任务列表", exact=True).click()
            expect(page.get_by_role("alert")).to_have_count(0)
            expect(page.get_by_role("tab", name="全部任务", exact=False)).to_have_text("全部任务2")
            self.assertEqual(writes, [])
            browser.close()

    def test_empty_workbench_count_requires_successful_reads_from_every_source(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
            pending = {}
            page.route("**/api/audit-tasks?*", lambda route: pending.update(tasks=route))
            page.route("**/api/drafts?*", lambda route: pending.update(drafts=route))
            page.route("**/api/rounds?*", lambda route: pending.update(rounds=route))
            page.goto(WEB)
            all_tasks = page.get_by_role("tab", name="全部任务", exact=False)
            expect(all_tasks).to_have_text("全部任务")
            expect(page.get_by_role("button", name="刷新任务列表", exact=True)).to_be_disabled()
            deadline = time.monotonic() + 5
            while len(pending) < 3 and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            self.assertEqual(set(pending), {"tasks", "drafts", "rounds"})
            pending["tasks"].fulfill(json={"tasks": [], "next_offset": None})
            pending["rounds"].fulfill(json={"rounds": [], "next_offset": None})
            expect(all_tasks).to_have_text("全部任务")
            pending["drafts"].fulfill(json={"drafts": [], "next_offset": None})
            expect(page.get_by_role("button", name="刷新任务列表", exact=True)).to_be_enabled()
            expect(all_tasks).to_have_text("全部任务0")
            browser.close()

    def test_workbench_count_does_not_treat_a_partial_history_page_as_the_total(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
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

            def read_rounds(route):
                if "offset=50" in route.request.url:
                    route.fulfill(json={"rounds": [{**summary, "id": SOURCE, "inputs": [{**summary["inputs"][0], "original_filename": "second.xlsx"}]}], "next_offset": None})
                else:
                    route.fulfill(json={"rounds": [summary], "next_offset": 50})

            page.route("**/api/rounds?*", read_rounds)
            page.goto(WEB)
            table = page.get_by_role("table", name="全部任务审核记录", exact=True)
            expect(table.get_by_text("history.xlsx", exact=True)).to_be_visible()
            expect(page.get_by_role("tab", name="全部任务", exact=False)).to_have_text("全部任务")
            page.get_by_role("button", name="加载更多记录", exact=True).click()
            expect(table.get_by_text("second.xlsx", exact=True)).to_be_visible()
            expect(page.get_by_role("tab", name="全部任务", exact=False)).to_have_text("全部任务2")
            browser.close()

    def test_workbench_does_not_reuse_a_previous_search_cursor_after_the_new_first_page_fails(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
            reads, ready, tail_failed = [], False, False
            summary = {
                "id": JOB,
                "number": "A-head",
                "state": "pending_confirm",
                "analysis_mode": "standard",
                "created_at": "2026-11-04T00:00:00Z",
                "updated_at": "2026-11-04T00:00:00Z",
                "round_id": None,
                "input_revision": 0,
                "files": [],
            }

            def read_tasks(route):
                query = parse_qs(urlsplit(route.request.url).query, keep_blank_values=True)
                search, offset = query.get("q", [""])[0], int(query["offset"][0])
                reads.append((search, offset))
                if search == "B" and (offset == 0 and not ready or offset == 50 and tail_failed):
                    route.abort("failed")
                else:
                    task = summary if not search else {**summary, "id": VERSION if offset == 0 else SOURCE, "number": "B-head" if offset == 0 else "B-tail"}
                    route.fulfill(json={"tasks": [task], "next_offset": 50 if offset == 0 else None})

            page.route("**/api/audit-tasks?*", read_tasks)
            page.goto(WEB)
            more = page.get_by_role("button", name="加载更多记录", exact=True)
            expect(more).to_be_enabled()
            page.get_by_role("textbox", name="搜索审核任务", exact=True).fill("B")
            expect(page.get_by_role("alert").filter(has_text="任务列表读取失败。当前文件与任务仍保留。")).to_be_visible()
            expect(page.get_by_role("tab", name="全部任务", exact=False)).to_have_text("全部任务")
            expect(more).to_have_count(0)
            self.assertEqual(reads, [("", 0), ("B", 0)])
            ready = True
            page.get_by_role("button", name="刷新任务列表", exact=True).click()
            expect(page.get_by_role("alert")).to_have_count(0)
            expect(more).to_be_enabled()
            expect(page.get_by_role("tab", name="全部任务", exact=False)).to_have_text("全部任务")
            more.click()
            expect(page.get_by_role("tab", name="全部任务", exact=False)).to_have_text("全部任务2")
            self.assertEqual(reads, [("", 0), ("B", 0), ("B", 0), ("B", 50)])
            page.get_by_role("button", name="刷新任务列表", exact=True).click()
            expect(page.get_by_role("button", name="刷新任务列表", exact=True)).to_be_enabled()
            expect(page.get_by_role("tab", name="全部任务", exact=False)).to_have_text("全部任务2")
            expect(more).to_have_count(0)
            self.assertEqual(reads, [("", 0), ("B", 0), ("B", 0), ("B", 50), ("B", 0), ("B", 50)])
            tail_failed = True
            page.get_by_role("button", name="刷新任务列表", exact=True).click()
            expect(page.get_by_role("alert").filter(has_text="任务列表读取失败。当前文件与任务仍保留。")).to_be_visible()
            expect(page.get_by_role("tab", name="全部任务", exact=False)).to_have_text("全部任务2")
            expect(more).to_have_count(0)
            self.assertEqual(reads[-2:], [("B", 0), ("B", 50)])
            browser.close()

    def test_workbench_uses_the_successful_current_list_even_when_older_tasks_are_cached(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            self.route(page)
            empty = False
            summary = {
                "id": JOB,
                "number": "B-visible",
                "state": "pending_confirm",
                "analysis_mode": "standard",
                "created_at": "2026-11-04T00:00:00Z",
                "updated_at": "2026-11-04T00:00:00Z",
                "round_id": None,
                "input_revision": 0,
                "files": [],
            }
            page.route("**/api/audit-tasks?*", lambda route: route.fulfill(json={"tasks": [] if empty or "q=B" in route.request.url else [summary], "next_offset": None}))
            page.goto(WEB)
            all_tasks = page.get_by_role("tab", name="全部任务", exact=False)
            expect(all_tasks).to_have_text("全部任务1")
            empty = True
            page.get_by_role("button", name="刷新任务列表", exact=True).click()
            expect(all_tasks).to_have_text("全部任务0")
            expect(page.get_by_role("alert")).to_have_count(0)
            empty = False
            page.get_by_role("button", name="刷新任务列表", exact=True).click()
            expect(all_tasks).to_have_text("全部任务1")
            with page.expect_response(lambda response: "/api/audit-tasks?" in response.url and "q=B" in response.url) as searched:
                page.get_by_role("textbox", name="搜索审核任务", exact=True).fill("B")
            searched.value.finished()
            expect(all_tasks).to_have_text("全部任务0")
            expect(page.get_by_role("alert")).to_have_count(0)
            browser.close()

    def test_native_validation_read_failure_keeps_other_file_without_creating_another_job(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_default_timeout(5000)
            self.route(page)
            versions, reads, creates, writes = {}, [], [], []
            self.upload_preparation_routes(page)
            page.on("request", lambda request: writes.append((request.method, urlsplit(request.url).path)) if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None)
            page.route("**/api/audit-customers", lambda route: route.fulfill(json={"customers": []}))
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
            pick_audit_files(page, [{"name": name, "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"fixture"} for name in ("flow.xlsx", "control.xlsx")])
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
            self.assertEqual(sum(method == "POST" and path == "/api/drafts" for method, path in writes), 1)
            self.assertCountEqual(
                [(method, path) for method, path in writes if not path.startswith("/api/drafts")],
                [("POST", "/api/files")] * 2 + [("PUT", f"/api/files/{version['file_id']}/versions/{version['id']}/blob") for version in versions.values()],
            )
            browser.close()

    def route(self, page, *, status="succeeded", hold=False, publish_status=201):
        # These cases control HTTP replies; offline behavior has its own API contract.
        page.context.set_offline(False)
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
            elif path == "/audit-customers/default" and request.method == "GET":
                route.fulfill(json={"customer_preference": None, "revision": 0}, headers=cors)
            elif path == "/rule-types":
                route.fulfill(json={"types": ["控制计划"]}, headers=cors)
            elif path in {"/audit-tasks", "/drafts", "/rounds"} and request.method == "GET":
                key = {"/audit-tasks": "tasks", "/drafts": "drafts", "/rounds": "rounds"}[path]
                route.fulfill(json={key: [], "next_offset": None}, headers=cors)
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
            expect(page.get_by_label("规则名称", exact=True)).to_be_visible()
            first = state["parses"][0]
            self.assertEqual(len(state["held"]), 1)
            back_to_templates(page)
            page.get_by_label("上传共用规则文件").set_input_files({"name": "B.json", "mimeType": "application/json", "buffer": b"second"})
            state["status"] = "succeeded"
            page.get_by_label("适用资料类型").click()
            page.get_by_role("option", name="控制计划", exact=True).click()
            page.get_by_role("button", name="提取条款", exact=True).click()
            expect(page.get_by_label("规则名称", exact=True)).to_have_value("规则 A")
            page.get_by_label("规则名称", exact=True).fill("第二份要求")
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
            expect(page.get_by_label("规则名称", exact=True)).to_have_value("第二份要求")
            expect(page.get_by_role("region", name="规则条款", exact=True).get_by_role("article")).to_have_count(1)
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
            page.goto(WEB + "/requirements?rulesArea=requirements")
            page.get_by_label("上传共用规则文件").set_input_files({"name": "待核对要求.json", "mimeType": "application/json", "buffer": b"{}"})
            page.get_by_label("适用资料类型").click()
            page.get_by_role("option", name="控制计划", exact=True).click()
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
                page.get_by_role("button", name="查看提取进度", exact=True).click()
            expect(unknown).to_be_visible()
            self.assertEqual(len(posts), 1)
            self.assertEqual(writes, [])
            page.get_by_role("button", name="重新提取条款", exact=True).click()
            confirmation = page.get_by_role("dialog", name="重新提取条款？", exact=True)
            expect(confirmation).to_contain_text("重新提取会重新开始")
            confirmation.get_by_role("button", name="取消", exact=True).click()
            self.assertEqual(len(posts), 1)
            self.assertEqual(writes, [])
            hold_original = True
            with page.expect_request("**/api/rules/parse/" + original):
                page.get_by_role("button", name="刷新提取进度", exact=True).click()
            # The request event precedes the route callback under concurrent
            # browser load. Wait for the callback before releasing its reply.
            deadline = time.monotonic() + 5
            while not delayed:
                self.assertLess(time.monotonic(), deadline, "The original read did not reach its route handler")
                page.wait_for_timeout(10)
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
                page.get_by_role("button", name="刷新提取进度", exact=True).click()
            expect(unknown).to_be_visible()
            expect(page.get_by_label("规则名称", exact=True)).to_have_value("待核对要求")
            expect(page.get_by_role("region", name="规则条款", exact=True).get_by_role("article")).to_have_count(0)
            self.assertIn(("GET", original), reads)
            self.assertTrue(all(method == "GET" for method, _identity in reads))
            self.assertEqual(len(delayed_posts), 1)
            page.get_by_role("button", name="录入条款", exact=True).click()
            manual = page.get_by_role("textbox", name="条款内容", exact=True)
            manual.fill("人工核对的要求，不能被迟到提取覆盖")
            with page.expect_response("**/api/rules/parse") as response:
                delayed_posts[0].fulfill(status=202, json=job(posts[1]["request_id"], "succeeded"))
            response.value.finished()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            expect(manual).to_have_value("人工核对的要求，不能被迟到提取覆盖")
            expect(page.get_by_role("region", name="规则条款", exact=True).get_by_role("article")).to_have_count(1)
            expect(page.get_by_label("规则名称", exact=True)).to_have_value("待核对要求")
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
            page.get_by_label("适用资料类型").click()
            page.get_by_role("option", name="全功能检验报告", exact=True).click()
            state["status"] = "succeeded"
            page.get_by_role("button", name="重新提取条款", exact=True).click()
            expect(page.get_by_label("规则名称", exact=True)).to_have_value("规则 A")
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
            page.get_by_role("button", name="保存版本", exact=True).click()
            expect(page.get_by_role("status").filter(has_text="版本 v1 已保存")).to_be_visible()
            self.assertEqual(len(state["saves"]), 1)
            published = state["saves"][0]
            self.assertEqual(published["title"], "已核对要求")
            self.assertEqual(published["body"]["rules"][0]["rule_to_check"], "检查 A")
            self.assertEqual(published["source_file_version_id"], state["source"]["id"])
            expect(page.get_by_label("规则名称", exact=True)).to_have_count(0)
            state["writes"].clear()
            open_rule(page, "已核对要求")
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
                    import json

                    registration = json.loads(value.get("case_context", {}).get("registration") or "{}")
                    customer_scope = "restricted" if normalized["customers"] else "all" if registration.get("scope", {}).get("allCustomers") else "unconfirmed"
                    rule = {
                        **value,
                        "body": normalized,
                        "id": value["request_id"],
                        "kind": "engine",
                        "type": value["body"]["type"],
                        "source_file_id": SOURCE,
                        "created_at": "2026-10-18T00:00:00Z",
                        "customer_scope": customer_scope,
                    }
                    rule.update(state.get("reply_patch", {}))
                    if state.get("custom_suite"):
                        # The real API gives every uploaded rule its own program key at publication.
                        rule["body"] = {**rule["body"], "suite_key": "custom:" + rule["id"], "suite_revision_id": "00000000-0000-4000-8000-0000000000aa"}
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
        self.native_rule_routes(page, state)
        return state

    def open_requirement(self, page, *, document_type="控制计划"):
        page.goto(WEB + "/requirements?rulesArea=requirements")
        page.get_by_label("上传共用规则文件").set_input_files({"name": "A.json", "mimeType": "application/json", "buffer": b"{}"})
        page.get_by_label("适用资料类型").click()
        page.get_by_role("option", name=document_type, exact=True).click()
        page.get_by_role("button", name="提取条款", exact=True).click()

    def check_requirement(self, page):
        from playwright.sync_api import expect

        expect(page.get_by_label("规则名称", exact=True)).to_have_value("规则 A")
        page.get_by_label("规则名称", exact=True).fill("已核对要求")
        for label in ("放入通用规则", "不限定零件", "不限定图纸修订"):
            page.get_by_label(label, exact=True).check()
        page.get_by_label("这次改了什么", exact=True).fill("已按原件核对。")
        clause = page.get_by_role("article", name="条款 1", exact=True)
        clause.get_by_role("button", name="核对出处", exact=True).click()
        page.get_by_role("combobox", name="原文位置", exact=True).click()
        page.get_by_role("option", name="rules[0].rule_to_check · 检查 A", exact=True).click()
        page.get_by_role("button", name="返回条款", exact=True).click()
        expect(clause).to_contain_text("rules[0].rule_to_check")
        clause.get_by_label("1 已核对来源", exact=True).check()

    def test_save_failure_returns_to_the_editable_candidate(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            state = self.requirement_routes(page)
            state["save_status"] = 500
            self.open_requirement(page)
            self.check_requirement(page)
            save = page.get_by_role("button", name="保存版本", exact=True)
            save.click()
            expect(page.get_by_role("alert").filter(has_text="审核服务暂时不可用，请稍后重试。")).to_be_visible()
            expect(save).to_be_enabled()
            expect(page.get_by_label("规则名称", exact=True)).to_be_enabled()
            expect(page.get_by_label("规则名称", exact=True)).to_have_value("已核对要求")
            expect(page.get_by_role("textbox", name="这次改了什么", exact=True)).to_have_value("已按原件核对。")
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
            expect(page.get_by_label("规则名称", exact=True)).to_have_value("规则 A")
            state["writes"].clear()
            page.get_by_role("button", name="放弃草稿", exact=True).click()
            dialog = page.get_by_role("dialog", name="放弃这份草稿？", exact=True)
            expect(dialog).to_be_visible()
            dialog.get_by_role("button", name="继续编辑", exact=True).click()
            expect(dialog).to_have_count(0)
            expect(page.get_by_label("规则名称", exact=True)).to_have_value("规则 A")
            page.get_by_role("button", name="放弃草稿", exact=True).click()
            dialog.get_by_role("button", name="放弃草稿", exact=True).click()
            expect(dialog).to_have_count(0)
            expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
            expect(page.get_by_role("heading", name="还没有规则模板", exact=True)).to_be_visible()
            page.reload()
            expect(page.get_by_role("heading", name="还没有规则模板", exact=True)).to_be_visible()
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
                    page.get_by_role("button", name="保存版本", exact=True).click()
                    expect(page.get_by_role("alert").filter(has_text="保存回执与本次要求不一致")).to_be_visible()
                    expect(page.get_by_role("status").filter(has_text="已保存")).to_have_count(0)
                    expect(page.get_by_label("规则名称", exact=True)).to_have_value("已核对要求")
                    expect(page.get_by_role("button", name="保存版本", exact=True)).to_be_enabled()
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
            page.get_by_role("button", name="保存版本", exact=True).click()
            expect(page.get_by_role("status").filter(has_text="版本 v1 已保存")).to_be_visible()
            self.assertEqual(len(state["saves"]), 1)
            self.assertEqual(state["writes"], [("POST", "/rules")])
            expect(page.get_by_label("规则名称", exact=True)).to_have_count(0)
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
            page.get_by_label("放入通用规则", exact=True).uncheck()
            page.get_by_label("所属模板", exact=True).fill("  测试客户  ")
            page.get_by_label("不限定零件", exact=True).uncheck()
            page.get_by_label("适用零件", exact=True).fill("  零件 P-1  ")
            page.get_by_role("button", name="保存版本", exact=True).click()
            expect(page.get_by_role("status").filter(has_text="版本 v1 已保存")).to_be_visible()
            self.assertEqual(len(state["saves"]), 1)
            self.assertEqual(state["saves"][0]["body"]["customers"], ["测试客户"])
            self.assertEqual(state["saves"][0]["body"]["parts"], ["零件 P-1"])
            browser.close()

    def test_saved_clause_confirms_one_reading_and_unknown_generation_is_not_repost(self):
        """Scripted UI contract only. This does not accept model semantic quality."""
        from playwright.sync_api import expect

        source_digest = "ab" * 32
        display_digest = "cd" * 32
        cnl = "每一条记录都必须有尺寸值。"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for outcome in ("completed", "unknown"):
                with self.subTest(outcome=outcome):
                    page = browser.new_page()
                    state = self.requirement_routes(page)
                    self.native_rule_routes(page, state)
                    formal = self.formalization_routes(page, source_digest=source_digest, display_digest=display_digest, cnl=cnl, outcome=outcome)
                    state["custom_suite"] = True
                    self.open_requirement(page)
                    self.check_requirement(page)
                    page.get_by_role("button", name="保存版本", exact=True).click()
                    expect(page.get_by_role("status").filter(has_text="版本 v1 已保存")).to_be_visible()
                    open_rule(page, "已核对要求")
                    # The rule page shows the rule; check points are prepared only on request in the wizard.
                    expect(page.get_by_text("系统是这样理解的")).to_have_count(0)
                    page.get_by_role("button", name="生成检查程序", exact=True).click()
                    expect(page.get_by_role("heading", name="生成检查程序", exact=True)).to_be_visible()
                    program = page.get_by_role("region", name="逐条确认", exact=True)
                    expect(program).to_contain_text("1 条还没整理。")
                    section = program.get_by_role("article", name="条款 1", exact=True)
                    expect(section).to_contain_text("还没整理。")
                    self.assertEqual(formal["generations"], [])
                    state["writes"].clear()
                    with page.expect_request("**/api/rule-formalization-generations/*"):
                        section.get_by_role("button", name="整理这条", exact=True).click()
                    expect(section.get_by_role("status").filter(has_text="正在整理")).to_be_visible()
                    # Asking again while the clause is being prepared posts nothing.
                    program.get_by_role("button", name="开始整理", exact=True).click()
                    self.assertEqual(len(formal["generations"]), 1)
                    self.assertEqual(formal["generations"][0]["expected_source_digest"], source_digest)
                    self.assertEqual(len(formal["held"]), 1)
                    formal["held"].pop().fulfill(json=formal["receipt"](formal["generations"][0]["operation_id"], outcome, None))
                    if outcome == "unknown":
                        unknown = section.get_by_role("status").filter(has_text="还不知道整理结果")
                        expect(unknown).to_be_visible()
                        page.reload()
                        # The wizard is part of the address; reloading reopens it, not the rule page.
                        expect(page.get_by_role("heading", name="生成检查程序", exact=True)).to_be_visible()
                        program = page.get_by_role("region", name="逐条确认", exact=True)
                        section = program.get_by_role("article", name="条款 1", exact=True)
                        expect(unknown).to_be_visible()
                        program.get_by_role("button", name="开始整理", exact=True).click()
                        self.assertEqual(len(formal["generations"]), 1)
                        with page.expect_request("**/api/rule-formalization-generations/*"):
                            section.get_by_role("button", name="刷新", exact=True).click()
                        operation_id = formal["generations"][0]["operation_id"]
                        self.assertEqual(formal["reads"], [operation_id, operation_id])
                        formal["outcome"] = "completed"
                        formal["held"].pop().fulfill(json=formal["receipt"](operation_id, "completed", "cand-1"))
                    expect(section).to_contain_text(cnl)
                    expect(section.get_by_role("heading", name="检查步骤", exact=True)).to_be_visible()
                    section.get_by_role("button", name="写得不对", exact=True).click()
                    expect(section.get_by_role("button", name="重新整理", exact=True)).to_be_disabled()
                    section.get_by_label("哪里写得不对？", exact=True).fill("编号必须逐字相等")
                    expect(section.get_by_role("button", name="重新整理", exact=True)).to_be_enabled()
                    section.get_by_role("button", name="取消", exact=True).click()
                    section.get_by_role("button", name="确认", exact=True).click()
                    expect(section.get_by_role("button", name="撤销确认", exact=True)).to_be_visible()
                    expect(program).to_contain_text("已确认 1 / 1")
                    expect(program.get_by_role("status").filter(has_text="还要一份示例文件。")).to_be_visible()
                    self.assertEqual(len(formal["generations"]), 1)
                    self.assertEqual([method for method, path in state["writes"] if path.endswith("/generations")], ["POST"])
                    self.assertEqual(len(formal["confirmations"]), 1)
                    self.assertEqual(formal["confirmations"][0]["candidate_id"], "cand-1")
                    self.assertEqual(formal["confirmations"][0]["expected_display_digest"], display_digest)
                    self.assertFalse(any(path.endswith("/checker-generations") for _method, path in state["writes"]))
                    page.close()
            browser.close()

    def formalization_routes(self, page, *, source_digest, display_digest, cnl, outcome):
        formal = {"generations": [], "reads": [], "confirmations": [], "held": [], "confirmed": False, "outcome": outcome}

        def receipt(operation_id, state_name, candidate_id):
            return {
                "operation_id": operation_id,
                "state": state_name,
                "candidate_id": candidate_id,
                "failure": None,
                "rule_version_id": formal.get("rule_id"),
                "clause_id": formal.get("clause_id"),
            }

        formal["receipt"] = receipt

        def detail(rule_id, clause_id):
            candidate = {
                "id": "cand-1",
                "formalization_revision": 1,
                "completeness": "complete",
                "cnl_text": cnl,
                "owl_content": "Class: Record",
                "clif_content": None,
                "unresolved": [],
                "display_digest": display_digest,
                "formal_profile": "owl2-dl-v1",
                "obligations": [],
            }
            started = bool(formal["generations"]) and formal["outcome"] == "completed"
            open_operation = None
            if formal["generations"] and not formal["confirmed"] and formal["outcome"] != "completed":
                open_operation = receipt(formal["generations"][0]["operation_id"], formal["outcome"], None)
            return {
                "rule_version_id": rule_id,
                "clause_id": clause_id,
                "source_rule_digest": source_digest,
                "state": "confirmed" if formal["confirmed"] else "candidate" if started else "not_started",
                "candidates": [candidate] if started or formal["confirmed"] else [],
                "confirmations": [{"current": True, "candidate_id": "cand-1"}] if formal["confirmed"] else [],
                "operation": open_operation,
            }

        def formalizations(route):
            parts = [item for item in urlsplit(route.request.url).path.split("/") if item]
            rule_id, clause_id = parts[2], parts[4]
            tail = parts[5] if len(parts) > 5 else ""
            if route.request.method == "GET" and not tail:
                route.fulfill(json=detail(rule_id, clause_id))
            elif route.request.method == "POST" and tail == "generations":
                body = route.request.post_data_json
                formal["rule_id"] = rule_id
                formal["clause_id"] = clause_id
                formal["generations"].append(body)
                route.fulfill(status=202, json=receipt(body["operation_id"], "queued", None))
            elif route.request.method == "POST" and tail == "confirmations":
                formal["confirmations"].append(route.request.post_data_json)
                formal["confirmed"] = True
                route.fulfill(status=201, json={"current": True, "candidate_id": "cand-1"})
            else:
                route.fulfill(status=404, json={"error": "unexpected_formalization"})

        def operations(route):
            operation_id = urlsplit(route.request.url).path.rsplit("/", 1)[1]
            formal["reads"].append(operation_id)
            if not formal.get("rule_id"):
                formal["clause_id"] = "1"
            formal["held"].append(route)

        def clauses(route):
            rule_id = [item for item in urlsplit(route.request.url).path.split("/") if item][2]
            route.fulfill(json={"rule_version_id": rule_id, "clauses": [{"clause_id": "1"}]})

        def program(route):
            rule_id = [item for item in urlsplit(route.request.url).path.split("/") if item][2]
            route.fulfill(json={"rule_version_id": rule_id, "program": "missing", "operation": None})

        page.route("**/api/rules/*/formalizations/**", formalizations)
        page.route("**/api/rules/*/formalizations", clauses)
        page.route("**/api/rules/*/checker-generation", program)
        page.route("**/api/rule-formalization-generations/*", operations)
        return formal


if __name__ == "__main__":
    unittest.main()
