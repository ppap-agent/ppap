"""The native shared-report entry reuses the fixed-artifact PDF reader."""

import hashlib
import io
import os
import zipfile
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


def test_shared_report_switch_download_and_denied_revisit_never_open_private_inputs(tmp_path):
    import fitz

    reports, bodies = [], {}
    for index, (name, conclusion) in enumerate((("控制计划.xlsx", "PASS"), ("尺寸检验报告.xlsx", "NG")), 1):
        with fitz.open() as document:
            document.new_page().insert_text((40, 60), "FROZEN REPORT " + str(index))
            body = document.tobytes()
        reference = {"task_id": "private-task", "round_id": "frozen-round", "file_version_id": "version-" + str(index), "result_attempt": 1, "report_id": "report-" + str(index)}
        bodies[reference["report_id"]] = body
        reports.append(
            {
                "name": name,
                "conclusion": conclusion,
                "rule": {"id": "rule-" + str(index), "title": "固定要求 " + str(index)},
                "reference": reference,
                "report": {
                    "state": "ready",
                    "report_id": reference["report_id"],
                    "reason": None,
                    "artifact": {"artifact_id": "artifact-" + str(index), "filename": name + "-审核报告.pdf", "byte_size": len(body), "content_type": "application/pdf"},
                },
            }
        )
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as archive:
        for row in reports:
            archive.writestr(row["report"]["artifact"]["filename"], bodies[row["reference"]["report_id"]])
    zip_body = zipped.getvalue()
    zip_artifact = {"artifact_id": "frozen-zip:1", "filename": "审核报告.zip", "byte_size": len(zip_body), "content_type": "application/zip"}
    calls, denied = [], [False]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(10000)
        page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))

        def api(route):
            request, path = route.request, urlsplit(route.request.url).path.removeprefix("/api")
            calls.append((request.method, path))
            if path == "/public-config":
                route.fulfill(json={"git_sha": "fixture", "api_url": WEB + "/api", "profile": "isolated-dev", "login": "feishu", "feishu_app_id": "test-app", "deep_offered": False, "current_access": "allowed", "usage_charge_enabled": False})
            elif path == "/me":
                route.fulfill(
                    json={
                        "user_id": "reader",
                        "tenant_id": "same-organization",
                        "membership": "member",
                        "display_name": "报告接收人",
                        "identity": {"provider": "feishu", "app_id": "test-app", "tenant_key": "tenant-one", "subject": "ou_reader"},
                    }
                )
            elif path == "/audit-report-shares/shared-one/reports":
                route.fulfill(status=403 if denied[0] else 200, json={"error": "access_denied"} if denied[0] else {"reports": reports, "archive": zip_artifact})
            elif path == "/audit-report-shares/shared-one/pdf":
                selected = parse_qs(urlsplit(request.url).query)
                report_id = selected["report_id"][0]
                assert selected["artifact_id"] == ["artifact-" + report_id[-1]]
                body = bodies[report_id]
                route.fulfill(body=body, headers={"Content-Type": "application/pdf", "X-Content-SHA256": hashlib.sha256(body).hexdigest()})
            elif path == "/audit-report-shares/shared-one/zip":
                assert parse_qs(urlsplit(request.url).query)["artifact_id"] == ["frozen-zip:1"]
                route.fulfill(body=zip_body, headers={"Content-Type": "application/zip", "X-Content-SHA256": hashlib.sha256(zip_body).hexdigest()})
            else:
                route.fulfill(status=404, json={"error": "unexpected_request"})

        page.route("**/api/**", api)
        page.goto(WEB + "/audit?share=shared-one")
        navigation = page.get_by_role("navigation", name="分享的报告")
        expect(navigation.get_by_role("button")).to_have_count(2)
        expect(page.get_by_role("heading", name="控制计划.xlsx-审核报告", exact=True)).to_be_visible()
        expect(page.get_by_role("img", name="控制计划.xlsx-审核报告.pdf · 第 1 页", exact=True)).to_be_visible()
        navigation.get_by_role("button").filter(has_text="尺寸检验报告.xlsx").click()
        expect(page.get_by_role("heading", name="尺寸检验报告.xlsx-审核报告", exact=True)).to_be_visible()
        expect(page.get_by_role("img", name="尺寸检验报告.xlsx-审核报告.pdf · 第 1 页", exact=True)).to_be_visible()
        with page.expect_download() as waiting:
            page.get_by_role("button", name="下载当前报告", exact=True).click()
        download = waiting.value
        assert download.suggested_filename == "尺寸检验报告.xlsx-审核报告.pdf"
        target = tmp_path / download.suggested_filename
        download.save_as(target)
        assert target.read_bytes() == bodies["report-2"]
        with page.expect_download() as waiting:
            page.get_by_role("button", name="下载报告 ZIP", exact=True).click()
        waiting.value.save_as(tmp_path / "selected.zip")
        assert (tmp_path / "selected.zip").read_bytes() == zip_body
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.get_by_role("heading", name="审核报告", exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth") <= 390
        assert page.locator("h1").count() == 1
        denied[0] = True
        page.reload()
        expect(page.get_by_role("alert")).to_contain_text("分享暂不可用")
        expect(page.get_by_role("button", name="下载当前报告", exact=True)).to_have_count(0)
        assert all(method == "GET" for method, _path in calls)
        assert not any(path.startswith(("/audit-tasks", "/files", "/rounds")) for _method, path in calls)
        browser.close()
