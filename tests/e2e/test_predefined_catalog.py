"""Predefined directory navigation keeps the source path, including on a phone-width viewport."""

import json
import os
import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional until the e2e job installs Playwright
    sync_playwright = None

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")
OFFICIAL_V5 = "official-v5"
OFFICIAL_V6 = "official-v6"
TENANT_CURRENT = "tenant-current"


def validation():
    return {
        "status": "unverified",
        "revision": 0,
        "write_revision": 0,
        "proof_scope": "none",
        "total_samples": 0,
        "passed_samples": 0,
        "new_errors": 0,
        "verified": False,
        "samples_not_rerun": False,
        "reasons": ["no_validation_proof"],
    }


def suite(key, title, current):
    return {
        "title": title,
        "candidate_revision_id": current or "candidate-" + key,
        "version": 1 if current else 0,
        "origin": "official",
        "selection_mode": "pinned" if current else "candidate_only",
        "suite_key": key,
        "revision": 1 if current else 0,
        "current_revision_id": current,
        "latest_official_revision_id": None,
        "latest_official_version": None,
        "official_base_revision_id": None,
        "official_update_available": False,
        "official_update_notice": False,
        "verification_status": "unverified",
        "force_unverified": False,
        "validation": validation(),
        "history": [],
    }


CATALOG = {
    "suites": [suite("UAES:U08", "企业采用的控制计划", TENANT_CURRENT), suite("ACME:Z99", "尚未采用的示例", None), suite("control-plan", "企业自定义检查", None)],
    "predefined": [
        {
            "path": "v5/UAES/U08",
            "availability": "installed",
            "title": "控制计划（CP）审核规则",
            "suite_key": "UAES:U08",
            "revision_id": OFFICIAL_V5,
            "clause_count": 12,
            "bound_clause_count": 2,
            "evidence_reader_clause_ids": ["U08:1", "U08:4"],
        },
        {
            "path": "v6/ACME/Z99",
            "availability": "installed",
            "title": "v6 示例检查",
            "suite_key": "ACME:Z99",
            "revision_id": OFFICIAL_V6,
            "clause_count": 8,
            "bound_clause_count": 8,
            "evidence_reader_clause_ids": [],
        },
        {"path": "v5/UAES/U07", "title": "潜在失效模式（PFMEA）审核规则", "availability": "unseeded"},
        {"path": "v5/UAES/U17", "title": "零件提交保证书（PSW）审核规则", "availability": "unseeded"},
        {"path": "v5/理想/L07", "title": "潜在失效模式审核规则", "availability": "unseeded"},
        {"path": "v5/蔚来/N03", "title": "控制计划审核规则", "availability": "unseeded"},
        {"path": "v6/NEW/X1", "title": "新客户规则", "availability": "unseeded"},
        {"path": "v6/ACME/BAD", "title": "暂不可读的检查", "availability": "unavailable"},
        {"path": "v4", "title": "v4", "availability": "historical"},
        {"path": "v4/uaes", "title": "uaes", "availability": "historical"},
        {"path": "v3", "title": "v3", "availability": "historical"},
    ],
    "predefined_unavailable": [{"path": "v6/NEW/X1", "error": "unseeded"}],
}


def revision(identity, key, title):
    return {
        "id": identity,
        "tenant_id": None,
        "suite_key": key,
        "title": title,
        "version": 2,
        "diff": [],
        "files": {"rules.json": json.dumps({"rules": [{"id": "C1", "rule_to_check": "条款"}]}), "checker.py": "def check(context):\n    return []\n"},
        "changed_paths": [],
        "validation": validation(),
        "round_count": 0,
        "rounds": [],
        "created_at": "2026-11-08T00:00:00Z",
        "created_by": None,
        "created_by_name": "官方",
        "resolved_digest": "a" * 64,
        "related_suite_keys": [],
        "base_revision_id": None,
    }


def active(key, current):
    return suite(key, "读取中的检查", current)


@unittest.skipUnless(sync_playwright, "playwright not installed")
class PredefinedCatalogBrowserTests(unittest.TestCase):
    def test_main_tree_keyboard_deep_link_refresh_and_dynamic_versions(self):
        from playwright.sync_api import expect

        revisions, hits, controls = [], [], {}
        with sync_playwright() as playwright:
            browser, page = self._open(playwright, revisions, hits, controls=controls)
            page.goto(WEB + "/requirements")
            directory = page.get_by_role("region", name="预定义检查目录", exact=True)
            expect(directory.get_by_role("heading", name="Predefined Checkers", exact=True)).to_be_visible()
            expect(page.get_by_role("combobox", name="选择检查", exact=True)).to_have_count(0)
            v5 = directory.get_by_role("treeitem", name="v5", exact=True)
            expect(v5).to_be_visible()
            v5.focus()
            page.keyboard.press("ArrowRight")
            uaes = directory.get_by_role("treeitem", name="UAES", exact=True)
            expect(uaes).to_be_visible()
            if uaes.get_attribute("aria-expanded") == "false":
                uaes.press("Enter")
            for code in ("U07", "U08", "U17"):
                expect(directory.locator(f'[data-tree-path="v5/UAES/{code}"]')).to_be_visible()
            for customer, code in (("理想", "L07"), ("蔚来", "N03")):
                directory.get_by_role("treeitem", name=customer, exact=True).click()
                expect(directory.locator(f'[data-tree-path="v5/{customer}/{code}"]')).to_be_visible()
            v4 = directory.get_by_role("treeitem", name=re.compile("^v4 历史"))
            expect(v4).to_have_attribute("aria-expanded", "false")
            v4.click()
            expect(directory.locator('[data-tree-path="v4/uaes"]')).to_be_visible()
            self._select(page, "v5/UAES/U08")
            expect(page).to_have_url(re.compile(r"checkerPath=v5%2FUAES%2FU08"))
            detail = page.get_by_role("region", name="Checker 来源详情", exact=True)
            expect(detail.get_by_text("官方修订 " + OFFICIAL_V5, exact=True)).to_be_visible()
            expect(detail.get_by_text(re.compile("tenant-current.*不同"))).to_be_visible()
            expect(detail.get_by_text("声明可取证 2/12 条款 · 2 条已绑定", exact=True)).to_be_visible()
            expect(detail.get_by_text(re.compile("其余条款未知或未支持"))).to_be_visible()
            self.assertNotIn(TENANT_CURRENT, revisions)
            page.reload()
            expect(detail.get_by_text("官方修订 " + OFFICIAL_V5, exact=True)).to_be_visible()
            self._select(page, "v6/ACME/Z99")
            expect(detail.get_by_text("官方修订 " + OFFICIAL_V6, exact=True)).to_be_visible()
            page.go_back()
            expect(detail.get_by_text("官方修订 " + OFFICIAL_V5, exact=True)).to_be_visible()
            self.assertGreaterEqual(len(hits), 2)
            self.assertEqual(controls.get("writes", []), [])
            screenshots = Path(os.environ.get("PPAP_SCREENSHOT_DIR", "/tmp/ppap291-screens"))
            screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshots / "predefined-desktop.png"), full_page=True)
            browser.close()

    def test_phone_returns_to_tree_and_never_opens_uninstalled_or_historical_sources(self):
        from playwright.sync_api import expect

        revisions = []
        with sync_playwright() as playwright:
            browser, page = self._open(playwright, revisions, [], viewport={"width": 390, "height": 844})
            page.goto(WEB + "/requirements?checkerPath=v5%2FUAES%2FU08")
            detail = page.get_by_role("region", name="Checker 来源详情", exact=True)
            expect(detail.get_by_text("官方修订 " + OFFICIAL_V5, exact=True)).to_be_visible()
            detail.get_by_role("button", name="返回目录", exact=True).click()
            self._select(page, "v6/NEW/X1")
            expect(detail.get_by_text(re.compile("待安装："))).to_be_visible()
            expect(detail.get_by_role("region", name="检查发布与版本记录")).to_have_count(0)
            detail.get_by_role("button", name="返回目录", exact=True).click()
            self._select(page, "v4/uaes")
            expect(detail.get_by_text(re.compile("真实历史源码目录"))).to_be_visible()
            self.assertEqual(revisions, [OFFICIAL_V5])
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            page.get_by_role("tab", name="客户要求与上传", exact=True).click()
            expect(page.get_by_role("button", name="上传规则", exact=True)).to_be_visible()
            page.get_by_role("tab", name="Predefined Checkers", exact=True).click()
            detail.get_by_role("button", name="返回目录", exact=True).click()
            screenshots = Path(os.environ.get("PPAP_SCREENSHOT_DIR", "/tmp/ppap291-screens"))
            screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshots / "predefined-mobile.png"), full_page=True)
            browser.close()

    def test_failed_catalog_or_permission_does_not_show_previous_source(self):
        from playwright.sync_api import expect

        for controls in ({"broken": True}, {"denied": True}):
            with self.subTest(controls=controls), sync_playwright() as playwright:
                browser, page = self._open(playwright, [], [], controls=controls)
                page.goto(WEB + "/requirements?checkerPath=v5%2FUAES%2FU08")
                directory = page.get_by_role("region", name="预定义检查目录", exact=True)
                expect(directory.get_by_role("alert").first).to_be_visible()
                expect(directory.get_by_role("treeitem")).to_have_count(0)
                expect(directory.get_by_text("官方修订 " + OFFICIAL_V5, exact=True)).to_have_count(0)
                browser.close()

    def test_late_source_response_cannot_replace_new_selection(self):
        from playwright.sync_api import expect

        controls = {"hold_source": OFFICIAL_V5}
        with sync_playwright() as playwright:
            browser, page = self._open(playwright, [], [], controls=controls)
            page.goto(WEB + "/requirements")
            self._select(page, "v5/UAES/U08")
            expect(page.get_by_label("正在读取检查版本…", exact=True)).to_be_visible()
            page.go_back()
            self._select(page, "v6/ACME/Z99")
            detail = page.get_by_role("region", name="Checker 来源详情", exact=True)
            expect(detail.get_by_text("官方修订 " + OFFICIAL_V6, exact=True)).to_be_visible()
            for route in controls.get("held", []):
                route.fulfill(json=revision(OFFICIAL_V5, "UAES:U08", "不应显示的迟到版本"))
            expect(detail.get_by_text("不应显示的迟到版本", exact=False)).to_have_count(0)
            expect(detail.get_by_text("官方修订 " + OFFICIAL_V6, exact=True)).to_be_visible()
            browser.close()

    def test_tenant_change_discards_old_source_response_and_adoption_state(self):
        from playwright.sync_api import expect

        controls = {"hold_source": OFFICIAL_V5}
        with sync_playwright() as playwright:
            browser, page = self._open(playwright, [], [], controls=controls)
            page.goto(WEB + "/requirements?checkerPath=v5%2FUAES%2FU08")
            expect(page.get_by_label("正在读取检查版本…", exact=True)).to_be_visible()
            controls["hold_source"] = None
            controls["tenant"] = "second-tenant"
            page.evaluate("window.dispatchEvent(new Event('ppap-session-expired'))")
            expect(page.get_by_role("heading", name="登录审核工作台", exact=True)).to_be_visible()
            page.get_by_role("button", name="重新读取登录状态", exact=True).click()
            detail = page.get_by_role("region", name="Checker 来源详情", exact=True)
            expect(detail.get_by_text(re.compile("second-current.*不同"))).to_be_visible()
            for route in controls.get("held", []):
                route.fulfill(json=revision(OFFICIAL_V5, "UAES:U08", "前一企业迟到的详情"))
            expect(detail.get_by_text("前一企业迟到的详情", exact=False)).to_have_count(0)
            expect(detail.get_by_text(re.compile("tenant-current"))).to_have_count(0)
            browser.close()

    def test_unreadable_revision_allows_returning_to_the_directory(self):
        from playwright.sync_api import expect

        with sync_playwright() as playwright:
            browser, page = self._open(playwright, [], [], controls={"deny_source": OFFICIAL_V5})
            page.goto(WEB + "/requirements?checkerPath=v5%2FUAES%2FU08")
            detail = page.get_by_role("region", name="Checker 来源详情", exact=True)
            expect(detail.get_by_role("alert")).to_be_visible()
            expect(detail.get_by_role("button", name="返回目录", exact=True)).to_be_enabled()
            detail.get_by_role("button", name="返回目录", exact=True).click()
            self._select(page, "v6/ACME/Z99")
            expect(detail.get_by_text("官方修订 " + OFFICIAL_V6, exact=True)).to_be_visible()
            browser.close()

    def _select(self, page, path):
        from playwright.sync_api import expect

        directory = page.get_by_role("region", name="预定义检查目录", exact=True)
        parts = path.split("/")
        for name in parts[:-1]:
            branch = directory.get_by_role("treeitem", name=re.compile("^" + re.escape(name) + "(?: 历史目录，只读)?$"))
            if branch.get_attribute("aria-expanded") == "false":
                branch.click()
        target = directory.locator(f'[data-tree-path="{path}"]')
        expect(target).to_have_attribute("role", "treeitem")
        expect(target).to_be_enabled()
        target.focus()
        page.keyboard.press("Enter")

    def _open(self, playwright, revisions, suite_hits, *, viewport=None, controls=None):
        browser = playwright.chromium.launch()
        options = {"service_workers": "block", "viewport": viewport or {"width": 1440, "height": 900}}
        page = browser.new_context(**options).new_page()
        # This navigation fixture controls HTTP reads; product authorization is
        # separately exercised against PostgreSQL by the API owner tests.
        page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))
        page.route("**/api/**", lambda route: self._route(route, revisions, suite_hits, broken=bool((controls or {}).get("broken")), controls=controls))
        return browser, page

    @staticmethod
    def _route(route, revisions, suite_hits, broken=False, controls=None):
        controls = controls if controls is not None else {}
        request = route.request
        path = unquote(urlsplit(request.url).path)
        if request.method != "GET":
            controls.setdefault("writes", []).append(path)
            route.fulfill(status=409, json={"error": "unexpected_fixture_write"})
            return
        if path.endswith("/runtime-config.json"):
            route.fulfill(json={"api_url": WEB + "/api"})
            return
        if path.endswith("/public-config"):
            route.fulfill(
                json={
                    "git_sha": "fixture",
                    "api_url": "/api",
                    "profile": "isolated-dev",
                    "login": "isolated-dev",
                    "deep_offered": False,
                    "feishu_app_id": "",
                    "current_access": "allowed",
                    "usage_charge_enabled": False,
                }
            )
            return
        if path.endswith("/me"):
            route.fulfill(
                json={
                    "display_name": "alice",
                    "tenant_id": controls.get("tenant", "test-tenant"),
                    "user_id": "test-user",
                    "membership": "member",
                    "identity": {"provider": "isolated-dev", "app_id": "test-app", "tenant_key": "test-tenant", "subject": "fixture"},
                }
            )
            return
        if path.endswith("/drafts") or "/drafts?" in path or path.endswith("/rounds") or "/rounds?" in path:
            name = "drafts" if "draft" in path else "rounds"
            route.fulfill(json={name: [], "next_offset": None})
            return
        if path.endswith("/rules"):
            route.fulfill(json={"rules": []})
            return
        if path.endswith("/rule-types"):
            route.fulfill(json={"types": []})
            return
        if path.endswith("/rule-library"):
            route.fulfill(json={"versions": []})
            return
        if request.method == "GET" and path.endswith("/suites"):
            suite_hits.append(request.url)
            if controls.get("denied"):
                route.fulfill(status=403, json={"error": "tenant_access_denied"})
                return
            if broken:
                route.fulfill(json={"suites": CATALOG["suites"], "predefined_error": "predefined_catalog_unavailable"})
            else:
                body = json.loads(json.dumps(CATALOG))
                if controls.get("tenant"):
                    body["suites"][0]["current_revision_id"] = "second-current"
                route.fulfill(json=body)
            return
        if path.endswith("/active"):
            key = path.split("/suites/")[1].split("/active")[0]
            current = ("second-current" if controls.get("tenant") else TENANT_CURRENT) if key == "UAES:U08" else None
            route.fulfill(json=active(key, current))
            return
        if "/suite-revisions/" in path:
            identity = path.split("/suite-revisions/")[1].split("/")[0]
            revisions.append(identity)
            if controls.get("deny_source") == identity:
                route.fulfill(status=403, json={"error": "suite_access_denied"})
                return
            if controls.get("hold_source") == identity:
                controls.setdefault("held", []).append(route)
                return
            titles = {OFFICIAL_V5: ("UAES:U08", "控制计划（CP）审核规则"), OFFICIAL_V6: ("ACME:Z99", "v6 示例检查")}
            key, title = titles.get(identity, ("UAES:U08", "控制计划（CP）审核规则"))
            route.fulfill(json=revision(identity, key, title))
            return
        route.fulfill(status=404, json={"error": "fixture_route_not_found", "path": path})
