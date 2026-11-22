"""Deterministic browser/API interleavings; no live backend or model calls.

The rules page (P12) is read only: customers on the left, each customer's file types and current rules,
and a read-only rule detail. Rules are delivered by the platform; the Web has no upload, draft or publication.
"""

import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from .conftest import WEB, pick_audit_files, route_api_address, wait_until

BODY = {
    "kind": "engine",
    "type": "控制计划",
    "rule_profile": "v3",
    "customers": ["测试客户"],
    "models": [],
    "parts": [],
    "rules": [{"id": "1", "category": "A", "priority": None, "rule_to_check": "检查 A", "type_of_rule": "table"}],
}
CUSTOMER = {
    "customer_id": "test-customer",
    "name": "测试客户",
    "key": "测试客户",
    "coverage": {"version": "1", "effective_from": "2026-01-01", "types": [{"code": "P10", "rule_type": "控制计划"}], "template_types": [], "rule_types": ["P10"]},
}


def open_customer_requirements(page):
    button = page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="审核规则库", exact=True)
    # The login shell also has a banner; wait for authorized product navigation.
    button.wait_for(state="visible")
    button.click()
    expect(page.get_by_role("region", name="审核规则库", exact=True)).to_be_visible()


def listed(rule):
    """A rule as GET /rules lists it."""
    return {key: rule[key] for key in ("id", "title", "created_at", "predecessor_id")} | {
        "kind": "engine",
        "type": rule["body"]["type"],
        "rule_profile": rule["body"]["rule_profile"],
        "customers": rule["body"]["customers"],
        "models": [],
        "parts": [],
        "customer_scope": "restricted",
        "clause_count": len(rule["body"]["rules"]),
    }


class RuleInteractionTests(unittest.TestCase):
    def test_rule_navigation_waits_for_authorized_shell(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            try:
                state = self.rule_routes(page, [CUSTOMER], {})
                held, probes = [], []
                page.route("**/api/me", lambda route: held.append(route))
                page.goto(WEB + "/audit")
                expect(page.get_by_text("正在确认登录状态…", exact=True)).to_be_visible()
                # The page may render before Playwright runs the route callback; wait for it, bounded.
                wait_until(page, lambda: held, "The /me read did not reach its route handler")
                self.assertEqual(len(held), 1)
                expect(page.get_by_role("banner")).to_be_visible()
                desktop = page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="审核规则库", exact=True)
                expect(desktop).to_be_hidden()

                def authorize():
                    # Keep the first readiness probe in the login shell. The next
                    # probe releases the existing /me fixture, without a new login.
                    probes.append(True)
                    if len(probes) == 2:
                        self.assertFalse(desktop.is_visible())
                        held.pop().fallback()

                page.add_locator_handler(page.get_by_text("正在确认登录状态…", exact=True), authorize, no_wait_after=True, times=2)
                open_customer_requirements(page)
                expect(page.get_by_role("complementary", name="客户", exact=True).get_by_role("link")).to_contain_text("测试客户")
                self.assertEqual(len(probes), 2)
                self.assertEqual(held, [])
                self.assertEqual(state["writes"], [])
            finally:
                page.close()
            browser.close()

    def test_native_history_timeout_offers_explicit_read_of_the_same_document(self):
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

    def test_rule_directory_reads_only_the_opened_rule_and_ignores_late_reads(self):
        rules = {}
        for identity, title, text, at, parent in (
            ("rule-a1", "规则 A", "旧版条款 A", "2026-10-16T00:00:00Z", None),
            ("rule-a2", "规则 A", "新版条款 A", "2026-10-18T00:00:00Z", "rule-a1"),
            ("rule-b1", "规则 B", "条款 B", "2026-10-17T00:00:00Z", None),
        ):
            rules[identity] = {
                "id": identity,
                "title": title,
                "created_at": at,
                "predecessor_id": parent,
                "body": {**BODY, "rules": [{**BODY["rules"][0], "rule_to_check": text}]},
                "source_file_id": None,
                "source_file_version_id": None,
            }
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            state = self.rule_routes(page, [CUSTOMER], rules)
            reads, held = [], []
            page.on("request", lambda request: reads.append(urlsplit(request.url).path.removeprefix("/api")))
            # Deliberately retain the transport after cancellation: the commit gate must still reject it.
            page.add_init_script("const realFetch = window.fetch.bind(window);window.fetch = (url, init) => realFetch(url, /\\/rules\\//.test(String(url)) ? {...init, signal: undefined} : init)")
            page.route("**/api/rules/rule-b1", lambda route: held.append(route))
            page.goto(WEB + "/requirements")
            table = page.get_by_role("table", name="测试客户 的规则列表", exact=True)
            # Only the current version of a rule is listed; reading the list reads no rule detail.
            expect(table.get_by_role("link", name="规则 A", exact=True)).to_have_count(1)
            expect(table.get_by_role("link", name="规则 B", exact=True)).to_be_visible()
            # The page reads the catalog and the rule list once; later navigation inside it reads only the opened rule.
            catalog_reads = (reads.count("/audit-customers"), reads.count("/rules"))
            self.assertGreaterEqual(min(catalog_reads), 1)
            self.assertFalse(any(path.startswith("/rules/") for path in reads))
            table.get_by_role("link", name="规则 A", exact=True).click()
            expect(page.get_by_role("heading", name="规则 A", exact=True, level=1)).to_be_visible()
            expect(page.get_by_role("article", name="第 1 条", exact=True)).to_contain_text("新版条款 A")
            self.assertEqual([path for path in reads if path.startswith("/rules/")], ["/rules/rule-a2"])
            trail = page.get_by_role("navigation", name="位置", exact=True)
            trail.get_by_role("link", name="测试客户", exact=True).click()
            table.get_by_role("link", name="规则 B", exact=True).click()
            expect(page.get_by_role("status", name="正在读取规则…", exact=True)).to_be_visible()
            trail.get_by_role("link", name="测试客户", exact=True).click()
            table.get_by_role("link", name="规则 A", exact=True).click()
            expect(page.get_by_role("article", name="第 1 条", exact=True)).to_contain_text("新版条款 A")
            self.assertEqual(len(held), 1)
            with page.expect_response(lambda response: response.url.endswith("/api/rules/rule-b1")) as late:
                held[0].fulfill(json=rules["rule-b1"])
            late.value.finished()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            expect(page.get_by_role("heading", name="规则 A", exact=True, level=1)).to_be_visible()
            expect(page.get_by_role("article", name="第 1 条", exact=True)).to_contain_text("新版条款 A")
            # The rules page did not read the catalog again and never writes.
            self.assertEqual((reads.count("/audit-customers"), reads.count("/rules")), catalog_reads)
            self.assertEqual(state["writes"], [])
            # Nothing on the page uploads, edits or publishes a rule.
            for name in ("上传规则", "创建新版本", "保存版本", "采用"):
                expect(page.get_by_role("button", name=name, exact=True)).to_have_count(0)
            browser.close()

    def test_built_in_rules_are_listed_under_their_customer(self):
        from audit.presets import customer_presets, preset_rule_body
        from product import customers as register

        rules = {}
        for preset in customer_presets():
            body = preset_rule_body(preset.customer, preset.entry_id)
            rules[preset.entry_id] = {
                "id": preset.entry_id,
                "title": preset.title,
                "created_at": "2026-10-01T00:00:00Z",
                "predecessor_id": None,
                "body": body,
                "source_file_id": None,
                "source_file_version_id": None,
            }
        latest = [{"kind": "engine", "type": rule["body"]["type"], "customers": rule["body"]["customers"]} for rule in rules.values()]
        # The catalog exactly as GET /audit-customers builds it for these rules.
        customers = []
        for customer in register.CUSTOMERS:
            coverage = register.coverage(customer, [], register.rule_type_codes(customer, latest))
            customers.append({"customer_id": customer.id, "name": customer.name, "key": customer.key, "coverage": coverage})
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            state = self.rule_routes(page, customers, rules)
            page.goto(WEB + "/requirements")
            directory = page.get_by_role("complementary", name="客户", exact=True)
            for customer in register.CUSTOMERS:
                expect(directory.get_by_role("link").filter(has_text=customer.name)).to_be_visible()
            uaes = register.find("uaes")
            directory.get_by_role("link").filter(has_text=uaes.name).click()
            expect(page.get_by_role("heading", name=uaes.name, exact=True)).to_be_visible()
            # A search that names a rule narrows the chosen customer's types to it.
            page.get_by_role("searchbox", name="搜索客户", exact=True).fill("生产件样品照片")
            table = page.get_by_role("table", name=f"{uaes.name} 的规则列表", exact=True)
            expect(table.get_by_role("row")).to_have_count(2)
            table.get_by_role("link").filter(has_text="生产件样品照片").click()
            expect(page.get_by_role("heading", name="生产件样品照片审核规则", exact=True, level=1)).to_be_visible()
            expect(page.get_by_role("region", name="规则条款", exact=True).get_by_role("article").first).to_be_visible()
            page.get_by_role("navigation", name="位置", exact=True).get_by_role("link", name=uaes.name, exact=True).click()
            expect(page.get_by_role("heading", name=uaes.name, exact=True)).to_be_visible()
            nio = register.find("nio")
            page.get_by_role("searchbox", name="搜索客户", exact=True).fill("")
            directory.get_by_role("link").filter(has_text=nio.name).click()
            expect(page.get_by_role("heading", name=nio.name, exact=True)).to_be_visible()
            expect(page.get_by_role("main")).not_to_contain_text("生产件样品照片")
            self.assertEqual(state["writes"], [])
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

    def test_native_validation_read_failure_keeps_other_file_without_creating_another_job(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_default_timeout(5000)
            self.route(page)
            versions, reads, creates, writes = {}, [], [], []
            self.upload_preparation_routes(page)
            page.on("request", lambda request: writes.append((request.method, urlsplit(request.url).path)) if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None)
            page.route("**/api/audit-customers", lambda route: route.fulfill(json={"customers": [], "unregistered": [], "type_names": {}}))
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
            sheet = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            pick_audit_files(page, [{"name": name, "mimeType": sheet, "buffer": b"fixture " + name.encode()} for name in ("flow.xlsx", "control.xlsx")])
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

    def route(self, page):
        """Signed-in shell; every other API read is answered explicitly or fails as unexpected."""
        page.context.set_offline(False)
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
            elif path == "/audit-tasks" and request.method == "GET":
                route.fulfill(json={"tasks": [], "next_offset": None}, headers=cors)
            elif path == "/rules" and request.method == "GET":
                route.fulfill(json={"rules": []}, headers=cors)
            else:
                route.fulfill(status=404, json={"error": "unexpected_test_request"}, headers=cors)

        route_api_address(page)
        page.route("**/api/**", respond)

    def rule_routes(self, page, customers, rules):
        """The read-only rules page protocol: the customer catalog, the rule list and one rule's detail."""
        self.route(page)
        state = {"writes": []}
        page.on("request", lambda request: state["writes"].append((request.method, urlsplit(request.url).path.removeprefix("/api"))) if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None)
        from product.customers import type_names

        catalog = {"customers": customers, "unregistered": [], "type_names": {**type_names(), "P10": "控制计划"}}
        page.route("**/api/audit-customers", lambda route: route.fulfill(json=catalog))
        page.route("**/api/audit-tasks?*", lambda route: route.fulfill(json={"tasks": [], "next_offset": None}))
        page.route("**/api/rules", lambda route: route.fulfill(json={"rules": [listed(rule) for rule in rules.values()]}))

        def detail(route):
            found = rules.get(urlsplit(route.request.url).path.rsplit("/", 1)[1])
            route.fulfill(json=found) if found else route.fulfill(status=404, json={"error": "missing"})

        page.route("**/api/rules/*", detail)
        return state
