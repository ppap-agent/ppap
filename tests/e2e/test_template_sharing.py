"""Native template sharing entry and fixed-file reader at the public HTTP seam."""

import copy
import hashlib
import os
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


@pytest.mark.parametrize("delivery_state,directory_status", [("sent", 200), ("unknown", 200), ("pre-accept", 200), ("unknown", 403), ("unknown", 503)])
def test_native_library_shares_the_whole_folder_not_the_search_result(delivery_state, directory_status):
    allowed = {"state": "allowed", "reason": None}
    files = [
        {
            "reference": {"customer_id": "客户 A", "template_id": f"template-{index}", "file_version_id": f"version-{index}"},
            "file": {
                "file_id": f"template-{index}",
                "id": f"version-{index}",
                "original_filename": f"模板-{index}.xlsx",
                "content_type": "application/octet-stream",
                "byte_size": 3,
                "status": "ready",
                "sha256": "a" * 64,
            },
            "document_type": "控制计划",
            "updated_at": "2026-10-19T00:00:00Z",
            "preview_access": allowed,
            "download_access": allowed,
            "share_access": allowed,
        }
        for index in (1, 2)
    ]
    scope = {"customer_id": "客户 A", "customer_name": "客户 A", "folder_id": "fixed-folder", "folder_revision": "fixed-revision", "templates": files, "link_access": allowed, "zip_access": allowed}
    target = {"kind": "user", "id": "reader", "display_name": "测试接收人", "organization_id": "tenant-one", "eligibility": "eligible", "reason": None}
    sends, recovery = [], [None]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))

        def api(route):
            request, path = route.request, urlsplit(route.request.url).path.removeprefix("/api")
            params = parse_qs(urlsplit(request.url).query)
            if path == "/public-config":
                route.fulfill(
                    json={
                        "git_sha": "fixture",
                        "api_url": WEB + "/api",
                        "profile": "isolated-dev",
                        "login": "feishu",
                        "feishu_app_id": "test-app",
                        "deep_offered": False,
                        "current_access": "allowed",
                        "usage_charge_enabled": False,
                    }
                )
            elif path == "/me":
                route.fulfill(
                    json={
                        "user_id": "sender",
                        "tenant_id": "same-organization",
                        "membership": "member",
                        "display_name": "发送人",
                        "identity": {"provider": "feishu", "app_id": "test-app", "tenant_key": "tenant-one", "subject": "sender"},
                    }
                )
            elif path == "/audit-template-catalog":
                route.fulfill(json={"coverage_universe": [str(index) for index in range(17)], "customers": [{"customer_id": "客户 A", "name": "客户 A", "effective_rule_types": ["1"]}]})
            elif path == "/audit-tasks":
                route.fulfill(json={"tasks": [], "next_offset": None})
            elif path == "/audit-templates":
                if sends and directory_status != 200:
                    route.fulfill(status=directory_status, json={"error": "feishu_templates_unavailable"})
                    return
                search = params.get("search", [""])[0]
                rows = [row for row in files if search in row["file"]["original_filename"]]
                route.fulfill(
                    json={
                        "query": {"customer_id": "客户 A", "search": search, "page": 1, "page_size": 10},
                        "folder_id": "fixed-folder",
                        "folder_revision": "fixed-revision",
                        "folder_template_count": len(files),
                        "creator_name": None,
                        "manage_access": {"state": "denied", "reason": "只读"},
                        "zip_access": allowed,
                        "items": rows,
                        "total": len(rows),
                    }
                )
            elif path == "/audit-template-share-scope":
                assert params["folder_template_count"] == ["2"]
                route.fulfill(json=scope)
            elif path == "/audit-template-share-restore":
                if sends and delivery_state == "pre-accept":
                    assert params["request_id"] == [sends[0]["request_id"]]
                    route.fulfill(status=409, json={"error": "share_request_pending"})
                    return
                if recovery[0] is None:
                    route.fulfill(body="null", content_type="application/json")
                else:
                    route.fulfill(json=recovery[0])
            elif path == "/audit-share-targets":
                route.fulfill(
                    json={"organization_id": "tenant-one", "organization_name": "客户 A", "kind": "user", "query": params.get("query", [""])[0], "targets": [target], "next_page_token": None}
                )
            elif path == "/audit-template-shares" and request.method == "POST":
                command = request.post_data_json
                sends.append(command)
                if delivery_state == "pre-accept":
                    route.abort("timedout")
                    return
                receipt = {
                    "request": command,
                    "receipt": {"request_id": command["request_id"], "state": "accepted", "reason": None},
                    "deliveries": [{"target": target, "attempt_id": command["request_id"], "state": delivery_state, "reason": None, "retry_allowed": False}],
                }
                recovery[0] = {"scope": copy.deepcopy(scope), "share": receipt}
                route.fulfill(json=receipt)
            elif path.startswith("/audit-template-shares/"):
                assert request.method == "GET"
                route.fulfill(json=recovery[0]["share"])
            else:
                route.fulfill(status=404, json={"error": "unexpected_request"})

        page.route("**/api/**", api)
        page.goto(WEB + "/audit?view=templates")
        page.get_by_role("textbox", name="搜索模板", exact=True).fill("模板-1")
        expect(page.get_by_role("table", name="客户模板", exact=True).get_by_role("row")).to_have_count(2)
        page.get_by_role("button", name="分享客户模板", exact=True).click()
        dialog = page.get_by_role("dialog", name="分享客户模板", exact=True)
        expect(dialog.get_by_role("list", name="本次分享的模板").get_by_role("listitem")).to_have_count(2)
        dialog.get_by_role("checkbox", name="选择人员 测试接收人", exact=True).check()
        dialog.get_by_role("button", name="发送给 1 个目标", exact=True).click()
        expect(dialog.get_by_role("listitem", name="发送到 测试接收人", exact=True)).to_contain_text("已发送" if delivery_state == "sent" else "结果未知")
        assert len(sends) == 1
        assert sends[0]["scope"]["templates"] == [row["reference"] for row in files]
        assert sends[0]["content"] == {"kind": "link", "package": None}
        if delivery_state != "sent":
            files.clear()
            for _ in range(2):
                page.reload()
                page.get_by_role("button", name="读取已有模板分享", exact=True).click()
                if delivery_state == "pre-accept":
                    expect(dialog.get_by_text("分享范围未确认", exact=True)).to_be_visible()
                    expect(dialog.get_by_role("button", name="发送给 0 个目标", exact=True)).to_be_disabled()
                    assert len(sends) == 1
                    continue
                expect(dialog.get_by_role("list", name="本次分享的模板").get_by_role("listitem")).to_have_count(2)
                expect(dialog.get_by_role("listitem", name="发送到 测试接收人", exact=True)).to_contain_text("结果未知")
                expect(dialog.get_by_role("button", name="新的模板分享", exact=True)).to_have_count(0)
                dialog.get_by_role("button", name="读取发送结果", exact=True).click()
                expect(dialog.get_by_role("listitem", name="发送到 测试接收人", exact=True)).to_contain_text("结果未知")
                assert len(sends) == 1
        browser.close()


def test_shared_templates_preview_download_and_revoked_revisit(tmp_path):
    import fitz

    with fitz.open() as document:
        document.new_page().insert_text((40, 60), "FROZEN CUSTOMER TEMPLATE")
        body = document.tobytes()
    allowed = {"state": "allowed", "reason": None}
    denied = {"state": "denied", "reason": "当前飞书账号不可导出。"}
    digest = hashlib.sha256(body).hexdigest()
    templates = [
        {
            "reference": {"customer_id": "客户 A", "template_id": f"template-{index}", "file_version_id": f"version-{index}"},
            "file": {
                "file_id": f"template-{index}",
                "id": f"version-{index}",
                "original_filename": f"模板-{index}.pdf",
                "content_type": "application/pdf",
                "byte_size": len(body),
                "status": "ready",
                "sha256": digest,
            },
            "document_type": "控制计划",
            "updated_at": "2026-10-19T00:00:00Z",
            "preview_access": allowed,
            "download_access": allowed if index == 1 else denied,
            "share_access": denied,
        }
        for index in (1, 2)
    ]
    scope = {"customer_id": "客户 A", "customer_name": "客户 A", "folder_id": "fixed-folder", "folder_revision": "fixed-revision", "templates": templates, "link_access": denied, "zip_access": denied}
    calls, revoked = [], [False]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844}, accept_downloads=True)
        page.set_default_timeout(10000)
        page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))

        def api(route):
            request, path = route.request, urlsplit(route.request.url).path.removeprefix("/api")
            calls.append((request.method, path))
            if path == "/public-config":
                route.fulfill(
                    json={
                        "git_sha": "fixture",
                        "api_url": WEB + "/api",
                        "profile": "isolated-dev",
                        "login": "feishu",
                        "feishu_app_id": "test-app",
                        "deep_offered": False,
                        "current_access": "allowed",
                        "usage_charge_enabled": False,
                    }
                )
            elif path == "/me":
                route.fulfill(
                    json={
                        "user_id": "reader",
                        "tenant_id": "same-organization",
                        "membership": "member",
                        "display_name": "模板接收人",
                        "identity": {"provider": "feishu", "app_id": "test-app", "tenant_key": "tenant-one", "subject": "reader"},
                    }
                )
            elif path == "/audit-template-shares/fixed-share/templates":
                route.fulfill(status=403 if revoked[0] else 200, json={"error": "access_denied"} if revoked[0] else scope)
            elif path == "/audit-template-shares/fixed-share/files/template-1/versions/version-1":
                assert parse_qs(urlsplit(request.url).query)["purpose"][0] in {"preview", "download"}
                route.fulfill(body=body, headers={"Content-Type": "application/pdf", "X-Content-SHA256": digest})
            else:
                route.fulfill(status=404, json={"error": "unexpected_request"})

        page.route("**/api/**", api)
        page.goto(WEB + "/audit?templateShare=fixed-share")
        expect(page.get_by_role("heading", name="客户模板", exact=True)).to_be_visible()
        rows = page.get_by_role("table", name="分享的客户模板").get_by_role("row")
        expect(rows).to_have_count(3)
        rows.nth(1).get_by_role("button", name="预览", exact=True).click()
        dialog = page.get_by_role("dialog", name="模板预览", exact=True)
        expect(dialog.get_by_role("img", name="模板-1.pdf · 第 1 页", exact=True)).to_be_visible()
        dialog.get_by_role("button", name="关闭", exact=True).click()
        expect(rows.nth(2).get_by_role("button", name="下载", exact=True)).to_be_disabled()
        with page.expect_download() as pending:
            rows.nth(1).get_by_role("button", name="下载", exact=True).click()
        downloaded = pending.value
        assert downloaded.suggested_filename == "模板-1.pdf"
        target = tmp_path / downloaded.suggested_filename
        downloaded.save_as(target)
        assert target.read_bytes() == body
        assert page.evaluate("document.documentElement.scrollWidth") <= 390
        revoked[0] = True
        page.reload()
        expect(page.get_by_role("alert")).to_contain_text("分享暂不可用")
        expect(page.get_by_role("table", name="分享的客户模板")).to_have_count(0)
        assert not any(method != "GET" or path.startswith(("/files/", "/audit-tasks")) for method, path in calls)
        browser.close()
