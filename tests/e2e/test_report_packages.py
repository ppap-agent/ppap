"""Archive ZIP recovery through the native adapter, with controlled HTTP receipts."""

import io
import os
import zipfile
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


def test_archive_zip_refresh_reads_original_cross_task_package_and_downloads_fixed_bytes():
    reports = [{"task_id": f"task-{n}", "round_id": f"round-{n}", "file_version_id": f"version-{n}", "result_attempt": 1, "report_id": f"report-{n}"} for n in (1, 2)]
    names = ["控制计划.xlsx-审核报告.pdf", "尺寸检验报告.xlsx-审核报告.pdf"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, b"%PDF-frozen-report")
    content = buffer.getvalue()
    job = {
        "package_id": "fixed-package",
        "reports": reports,
        "receipt": {"request_id": "original-request", "state": "accepted", "reason": None},
        "state": "ready",
        "outcomes": [{"reference": ref, "filename": name, "state": "prepared", "reason": None} for ref, name in zip(reports, names)],
        "artifact": {"artifact_id": "fixed-package:1", "filename": "审核报告.zip", "content_type": "application/zip", "byte_size": len(content)},
        "reason": None,
        "retry_allowed": False,
    }
    calls, mismatch, unavailable, held = [], [False], [False], []
    hold_next_read = [False]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1079, "height": 853})
        page.set_default_timeout(5000)
        page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))

        def api(route):
            path = urlsplit(route.request.url).path.removeprefix("/api")
            calls.append((route.request.method, path))
            if path == "/public-config":
                route.fulfill(
                    json={
                        "git_sha": "controlled-native-test",
                        "api_url": WEB + "/api",
                        "profile": "isolated-dev",
                        "login": "feishu",
                        "feishu_app_id": "test",
                        "deep_offered": False,
                        "current_access": "allowed",
                        "usage_charge_enabled": False,
                    }
                )
            elif path == "/me":
                route.fulfill(
                    json={
                        "user_id": "reader",
                        "tenant_id": "organization",
                        "membership": "member",
                        "display_name": "测试用户",
                        "identity": {"provider": "feishu", "app_id": "test", "tenant_key": "organization", "subject": "reader"},
                    }
                )
            elif path == "/audit-archive":
                query = {"search": "", "category_id": None, "document_type": None, "customer": None, "initiator": None, "from_date": None, "through_date": None, "page": 1, "page_size": 10}
                route.fulfill(json={"query": query, "documents": [], "total": 0, "document_count": 0, "categories": [], "document_types": [], "customers": [], "initiators": []})
            elif path == "/audit-report-packages/original-request":
                if hold_next_read[0]:
                    hold_next_read[0] = False
                    held.append(route)
                elif unavailable[0]:
                    route.fulfill(status=503, json={"error": "temporarily_unavailable"})
                else:
                    route.fulfill(json={**job, "receipt": {**job["receipt"], "request_id": "other-request"}} if mismatch[0] else job)
            elif path == "/audit-report-packages/original-request/zip":
                assert parse_qs(urlsplit(route.request.url).query) == {"package_id": ["fixed-package"], "artifact_id": ["fixed-package:1"]}
                route.fulfill(body=content, headers={"Content-Type": "application/zip"})
            else:
                route.fulfill(status=404, json={"error": "unexpected_request"})

        page.route("**/api/**", api)
        page.goto(WEB + "/audit?view=archive&packageRequest=original-request")
        package = page.get_by_role("region", name="ZIP 工作", exact=True)
        expect(package.get_by_role("heading", name="ZIP 已就绪", exact=True)).to_be_visible()
        for name in names:
            expect(package.get_by_text(name, exact=True)).to_be_visible()
        page.reload()
        expect(package.get_by_role("button", name="下载已就绪 ZIP", exact=True)).to_be_enabled()
        with page.expect_download() as download:
            package.get_by_role("button", name="下载已就绪 ZIP", exact=True).click()
        with open(download.value.path(), "rb") as source:
            assert source.read() == content
        assert calls.count(("GET", "/audit-report-packages/original-request")) == 2
        assert not any(method != "GET" for method, _path in calls)

        unavailable[0] = True
        page.reload()
        expect(page.get_by_role("alert")).to_contain_text("未能读取此打包请求")
        expect(package).to_have_count(0)
        unavailable[0] = False
        page.get_by_role("button", name="重新读取 ZIP", exact=True).click()
        expect(package.get_by_role("heading", name="ZIP 已就绪", exact=True)).to_be_visible()
        mismatch[0] = True
        page.reload()
        expect(page.get_by_role("alert")).to_contain_text("未能读取此打包请求")
        expect(package).to_have_count(0)
        assert not any(method != "GET" for method, _path in calls)
        mismatch[0] = False
        hold_next_read[0] = True
        page.reload()
        expect(page.get_by_text("正在恢复 ZIP 工作…", exact=True)).to_be_visible()
        page.get_by_role("button", name="用量记录", exact=True).click()
        assert "packageRequest" not in parse_qs(urlsplit(page.url).query)
        with page.expect_response(lambda response: urlsplit(response.url).path.endswith("/audit-report-packages/original-request")):
            held.pop().fulfill(json=job)
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        assert urlsplit(page.url).path == "/usage"
        expect(package).to_have_count(0)
        page.go_back()
        expect(package.get_by_role("heading", name="ZIP 已就绪", exact=True)).to_be_visible()
        assert parse_qs(urlsplit(page.url).query)["packageRequest"] == ["original-request"]
        assert not any(method != "GET" for method, _path in calls)
        browser.close()
