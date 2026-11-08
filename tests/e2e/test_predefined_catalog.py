"""Predefined directory navigation keeps the source path, including on a phone-width viewport."""

import json
import os
import re
import unittest
from urllib.parse import unquote, urlsplit

from .conftest import isolated_dev_login

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
        {"path": "v5/UAES/U08", "title": "控制计划（CP）审核规则", "suite_key": "UAES:U08", "revision_id": OFFICIAL_V5},
        {"path": "v6/ACME/Z99", "title": "v6 示例检查", "suite_key": "ACME:Z99", "revision_id": OFFICIAL_V6},
    ],
    "predefined_unavailable": [{"path": "v6/NEW/X1", "error": "unseeded"}, {"path": "not-a-directory", "error": "invalid"}],
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
    def test_source_path_stays_distinct_across_keyboard_refresh_and_narrow_viewport(self):
        from playwright.sync_api import expect

        revisions = []
        suite_hits = []
        with sync_playwright() as playwright:
            browser, page = self._open(playwright, revisions, suite_hits, viewport={"width": 1440, "height": 900})
            with page.expect_response(self._catalog_response) as catalog:
                page.goto(WEB + "/requirements?checkerPath=v5%2FUAES%2FU08")
            self._assert_stub_catalog(suite_hits, catalog.value.json())
            directory = page.get_by_role("region", name="预定义检查目录", exact=True)
            enterprise = page.get_by_role("region", name="企业检查版本", exact=True)
            expect(directory.get_by_role("heading", name="v5", exact=True)).to_be_visible()
            expect(directory.get_by_role("heading", name="UAES", exact=True)).to_be_visible()
            expect(directory.get_by_role("heading", name="v6", exact=True)).to_be_visible()
            expect(directory.get_by_role("heading", name="ACME", exact=True)).to_be_visible()
            expect(directory.get_by_text("v6/NEW/X1 · 尚未安装官方修订", exact=True)).to_be_visible()
            expect(directory.get_by_role("button", name="v6/NEW/X1")).to_have_count(0)
            expect(directory.get_by_text("来源路径 v5/UAES/U08", exact=True)).to_be_visible()
            expect(directory.get_by_text(f"官方修订 {OFFICIAL_V5}", exact=True)).to_be_visible()
            expect(directory.get_by_text(f"来源 v5/UAES/U08 的官方修订 {OFFICIAL_V5} 与企业当前采用的修订 {TENANT_CURRENT} 不同。此目录仍只打开该来源的官方修订。", exact=True)).to_be_visible()
            expect(enterprise.get_by_label("选择检查", exact=True)).to_be_visible()
            self.assertIn(OFFICIAL_V5, revisions)
            self.assertNotIn(TENANT_CURRENT, revisions)
            seen = len(suite_hits)
            with page.expect_response(self._catalog_response) as refreshed:
                page.reload()
            self.assertGreater(len(suite_hits), seen)
            self._assert_stub_catalog(suite_hits, refreshed.value.json())
            expect(page).to_have_url(re.compile(r"checkerPath=v5%2FUAES%2FU08"))
            expect(directory.get_by_text(f"官方修订 {OFFICIAL_V5}", exact=True)).to_be_visible()
            self.assertGreaterEqual(revisions.count(OFFICIAL_V5), 2)
            v6 = directory.get_by_role("button", name="v6/ACME/Z99")
            v6.focus()
            page.keyboard.press("Enter")
            expect(page).to_have_url(re.compile(r"checkerPath=v6%2FACME%2FZ99"))
            expect(directory.get_by_text(f"官方修订 {OFFICIAL_V6}", exact=True)).to_be_visible()
            expect(directory.get_by_text(f"来源 v6/ACME/Z99 绑定官方修订 {OFFICIAL_V6}。企业尚未采用 ACME:Z99。", exact=True)).to_be_visible()
            self.assertIn(OFFICIAL_V6, revisions)
            page.go_back()
            expect(directory.get_by_text(f"官方修订 {OFFICIAL_V5}", exact=True)).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            source = directory.get_by_role("button", name="v5/UAES/U08")
            expect(source).to_be_visible()
            box = source.bounding_box()
            self.assertIsNotNone(box)
            self.assertGreaterEqual(box["height"], 44)
            browser.close()

    def test_a_broken_directory_lists_no_paths(self):
        from playwright.sync_api import expect

        suite_hits = []
        with sync_playwright() as playwright:
            browser, page = self._open(playwright, [], suite_hits, broken=True)
            with page.expect_response(self._catalog_response) as catalog:
                page.goto(WEB + "/requirements?checkerPath=v5%2FUAES%2FU08")
            body = catalog.value.json()
            self.assertGreaterEqual(len(suite_hits), 1)
            self.assertEqual(body.get("predefined_error"), "predefined_catalog_unavailable")
            self.assertNotIn("predefined", body)
            directory = page.get_by_role("region", name="预定义检查目录", exact=True)
            expect(directory.get_by_text("预定义检查目录不可用。未列出任何来源路径。", exact=True)).to_be_visible()
            expect(directory.get_by_role("button", name="v5/UAES/U08")).to_have_count(0)
            expect(directory.get_by_text(f"官方修订 {OFFICIAL_V5}", exact=True)).to_have_count(0)
            expect(page.get_by_role("region", name="企业检查版本", exact=True).get_by_label("选择检查", exact=True)).to_be_visible()
            browser.close()

    def _open(self, playwright, revisions, suite_hits, *, viewport=None, broken=False):
        browser = playwright.chromium.launch()
        options = {"service_workers": "block"}
        if viewport is not None:
            options["viewport"] = viewport
        page = browser.new_context(**options).new_page()
        isolated_dev_login(page, WEB)
        # CI serves the API on its own origin. Pin the SPA to this origin's /api before the catalog load.
        page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))
        page.route("**/api/**", lambda route: self._route(route, revisions, suite_hits, broken=broken))
        return browser, page

    @staticmethod
    def _catalog_response(response) -> bool:
        if response.request.method != "GET":
            return False
        return unquote(urlsplit(response.url).path).endswith("/suites")

    def _assert_stub_catalog(self, suite_hits, body):
        self.assertGreaterEqual(len(suite_hits), 1)
        self.assertNotIn("predefined_error", body)
        self.assertEqual([item["path"] for item in body["predefined"]], ["v5/UAES/U08", "v6/ACME/Z99"])

    @staticmethod
    def _route(route, revisions, suite_hits, broken=False):
        request = route.request
        path = unquote(urlsplit(request.url).path)
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
                    "tenant_id": "test-tenant",
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
            if broken:
                route.fulfill(json={"suites": CATALOG["suites"], "predefined_error": "predefined_catalog_unavailable"})
            else:
                route.fulfill(json=CATALOG)
            return
        if path.endswith("/active"):
            key = path.split("/suites/")[1].split("/active")[0]
            current = TENANT_CURRENT if key == "UAES:U08" else None
            route.fulfill(json=active(key, current))
            return
        if "/suite-revisions/" in path:
            identity = path.split("/suite-revisions/")[1].split("/")[0]
            revisions.append(identity)
            titles = {OFFICIAL_V5: ("UAES:U08", "控制计划（CP）审核规则"), OFFICIAL_V6: ("ACME:Z99", "v6 示例检查")}
            key, title = titles.get(identity, ("UAES:U08", "控制计划（CP）审核规则"))
            route.fulfill(json=revision(identity, key, title))
            return
        route.fulfill(status=404, json={"error": "fixture_route_not_found", "path": path})
