"""Read a frozen Round's existing report from its Task in the authorized native UI."""

import copy
import json
import os
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")
TASK = "00000000-0000-4000-8000-000000000001"
ROUND = "00000000-0000-4000-8000-000000000002"
VERSION = "00000000-0000-4000-8000-000000000003"
REPORT = "00000000-0000-4000-8000-000000000004"


def round_report_routes(page, *, report_state="ready", verification=None):
    import fitz

    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "Frozen original report")
    content = pdf.tobytes()
    pdf.close()
    rule = {
        "id": "frozen-rule",
        "title": "原控制计划规则",
        "body": {
            "kind": "engine",
            "type": "控制计划",
            "rule_profile": "v3",
            "customers": [],
            "models": [],
            "parts": [],
            "rules": [{"id": "1", "category": "原签字要求", "priority": None, "rule_to_check": "原件必须有签字", "type_of_rule": "text"}],
        },
    }
    version = {"id": VERSION, "file_id": "original-file", "original_filename": "原控制计划.xlsx", "status": "ready", "content_type": "application/octet-stream", "byte_size": 8, "sha256": None}
    finding = {"item_key": "1", "status": "NG", "item": "签字为空", "classification": "签字", "suggestions": "", "priority": None, "locator": None, "review": None}
    report = {
        "state": report_state,
        "report_id": REPORT,
        "reason": "生成服务暂不可用" if report_state == "failed" else None,
        "generated_at": "2026-10-20T00:00:00Z" if report_state == "ready" else None,
        "artifact": {"artifact_id": REPORT + ":1", "filename": "原控制计划.xlsx-审核报告.pdf", "content_type": "application/pdf", "byte_size": len(content)} if report_state == "ready" else None,
        "regenerate_allowed": False,
        "regenerate_reason": "此测试只读取原报告。",
    }
    audit = {
        "round_id": ROUND,
        "file_version_id": VERSION,
        "result_attempt": 1,
        "rule": rule,
        "execution": "completed",
        "progress": {"completed": 1, "total": 1},
        "failure": None,
        "conclusion": "NG",
        "counts": {"pass_count": 0, "ng_count": 1, "unknown_count": 0, "not_covered_count": 0, "error_count": 0},
        "checks": [
            {"rule_item": rule["body"]["rules"][0], "execution": "completed", "coverage": "executed", "finding": finding, "evidence": [], "scope": None, "evidence_verified": False, "review": None}
        ],
        "unmapped_findings": [],
        "summary": {"conclusion": "NG", "text": "不符合 1 项。", "corrective_actions": [], "extended_suggestions": []},
        "report": report,
    }
    if verification:
        audit["checker_verification"] = verification
        audit["conclusion"] = audit["checks"][0]["finding"]["status"] = "PASS"
        audit["counts"].update(pass_count=1, ng_count=0)
        audit["summary"] = {"conclusion": "PASS", "text": "符合 1 项。", "corrective_actions": [], "extended_suggestions": []}
    round = {
        "id": ROUND,
        "execution": "completed",
        "coverage": {},
        "predecessor_id": None,
        "source_draft_id": TASK,
        "preparation_allowed": False,
        "result_attempt": 1,
        "created_at": "2026-10-20T00:00:00Z",
        "writeback": {"available": False},
        "case_context": {},
        "progress": None,
        "failure": None,
        "inputs": [
            {
                "role": "target",
                "file_id": version["file_id"],
                "file_version_id": VERSION,
                "original_filename": version["original_filename"],
                "status": "ready",
                "rule_version_id": rule["id"],
                "rule": rule,
            }
        ],
        "findings": [{**finding, "item_key": VERSION + ":rule:1"}],
        "read_view": {
            "files": [{"file": version, "role": "target", "rule": rule, "audit": copy.deepcopy(audit)}],
            "unassigned_findings": [],
            "report_source": {"task_id": TASK, "access": {"state": "allowed", "reason": None}},
        },
    }
    task = {
        "id": TASK,
        "number": "PPAP-原审核",
        "state": "reviewing" if report_state in {"pending", "generating"} else "completed",
        "confirmation_basis_digest": None,
        "confirmation_scope": None,
        "created_at": "2026-10-20T00:00:00Z",
        "updated_at": "2026-10-20T00:00:00Z",
        "source_draft_id": TASK,
        "input_revision": 0,
        "round_id": ROUND,
        "customer_preference": None,
        "completion_reason": "processed" if report_state in {"ready", "failed"} else None,
        "estimated_points": 500,
        "acceptance": {"request_id": "original-confirmation", "state": "accepted", "reason": None},
        "progress": {"completed": 1, "total": 1},
        "files": [
            {
                "file": version,
                "selection": "selected",
                "removal_reason": None,
                "validation": {"state": "valid", "reasons": []},
                "match": {"state": "matched", "document_type": "控制计划", "metadata": {"customer": None, "model": None, "part": None, "part_number": None}, "rule": rule, "gaps": []},
                "audit": audit,
            }
        ],
    }
    calls = []
    page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))

    def api(route):
        request = route.request
        path = urlsplit(request.url).path.removeprefix("/api")
        calls.append((request.method, path))
        if path == "/public-config":
            route.fulfill(
                json={
                    "git_sha": "controlled-round-report",
                    "api_url": WEB + "/api",
                    "profile": "isolated-dev",
                    "login": "isolated-dev",
                    "feishu_app_id": "",
                    "current_access": "allowed",
                    "usage_charge_enabled": False,
                }
            )
        elif path == "/me":
            route.fulfill(
                json={
                    "display_name": "测试用户",
                    "tenant_id": "test-tenant",
                    "user_id": "test-user",
                    "membership": "member",
                    "identity": {"provider": "isolated-dev", "app_id": "test-app", "tenant_key": "test-tenant", "subject": "test-user"},
                }
            )
        elif path == "/rounds/" + ROUND:
            route.fulfill(json=round)
        elif path == "/audit-tasks":
            route.fulfill(json={"tasks": [], "next_offset": None})
        elif path == "/audit-tasks/" + TASK:
            route.fulfill(json=task)
        elif path == f"/audit-tasks/{TASK}/reports/{REPORT}/pdf":
            assert parse_qs(urlsplit(request.url).query) == {"round_id": [ROUND], "file_version_id": [VERSION], "result_attempt": ["1"], "artifact_id": [REPORT + ":1"]}
            route.fulfill(body=content, content_type="application/pdf")
        else:
            route.fulfill(status=404, json={"error": "unexpected_request"})

    page.route("**/api/**", api)
    return calls, content


def test_task_opens_its_frozen_existing_report_and_return_preserves_result_context():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1057, "height": 853})
        page.set_default_timeout(5000)
        calls, content = round_report_routes(page)
        address = WEB + f"/tasks/{TASK}?file={VERSION}&clause=1&focus=review&checkFilter=ng"
        page.goto(address)
        expect(page.get_by_role("heading", name="原控制计划.xlsx", exact=True)).to_be_visible()
        expect(page.get_by_role("region", name="专注复核", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="结果数据需要核对", exact=True)).to_have_count(0)
        report = page.get_by_role("button", name="查看报告", exact=True)
        expect(report).to_be_enabled()
        report.click()
        expect(page.get_by_role("toolbar", name="PDF 阅读工具", exact=True)).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role("button", name="下载当前报告", exact=True).click()
        with open(download.value.path(), "rb") as source:
            assert source.read() == content
        # Opening the report replaces the task entry; the breadcrumb returns to the same result position.
        page.get_by_role("navigation", name="当前位置", exact=True).get_by_role("link", name="返回审核结果", exact=True).click()
        expect(page).to_have_url(address)
        expect(page.get_by_role("heading", name="原控制计划.xlsx", exact=True)).to_be_visible()
        expect(page.get_by_role("region", name="专注复核", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="第 1 条 · 原签字要求", exact=True)).to_be_visible()
        assert all(method == "GET" for method, _path in calls)
        browser.close()


def test_embedded_native_task_report_keeps_the_selected_report_when_its_host_reloads():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(5000)
        calls, _content = round_report_routes(page)
        initial = f"/tasks/{TASK}?file={VERSION}"
        # The external host only carries the existing public navigation message.
        host = """<iframe title="原生客户端" id="product" style="width:100%;height:90vh"></iframe><script>
        const frame = document.getElementById('product');
        const address = new URL(location.href);
        frame.src = address.searchParams.has('task')
          ? '/tasks/' + encodeURIComponent(address.searchParams.get('task')) + '?report=' + encodeURIComponent(address.searchParams.get('report') || '')
          : INITIAL;
        addEventListener('message', event => {
          if (event.origin !== location.origin || event.source !== frame.contentWindow || event.data?.type !== 'ppap-audit-task') return;
          const next = new URL(location.href);
          next.searchParams.set('task', event.data.task || '');
          next.searchParams.set('report', event.data.report || '');
          history.replaceState(null, '', next);
        });</script>""".replace("INITIAL", json.dumps(initial))
        page.route(WEB + "/round-report-host.html*", lambda route: route.fulfill(body=host, content_type="text/html"))
        page.goto(WEB + "/round-report-host.html")
        product = page.frame_locator("#product")
        product.get_by_role("button", name="查看报告", exact=True).click()
        expect(product.get_by_role("toolbar", name="PDF 阅读工具", exact=True)).to_be_visible()
        expect(page).to_have_url(WEB + f"/round-report-host.html?task={TASK}&report={VERSION}")
        page.reload()
        expect(product.get_by_role("heading", name="原控制计划.xlsx", exact=True)).to_be_visible()
        expect(product.get_by_role("toolbar", name="PDF 阅读工具", exact=True)).to_be_visible()
        assert all(method == "GET" for method, _path in calls)
        browser.close()


def test_failed_round_report_generation_reaches_the_existing_report_recovery_ui():
    state, label = "failed", "生成失败"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 844})
        calls, _content = round_report_routes(page, report_state=state)
        page.goto(WEB + f"/tasks/{TASK}?file={VERSION}")
        page.get_by_role("button", name="查看报告", exact=True).click()
        preview = page.get_by_role("region", name="当前报告", exact=True)
        expect(preview.get_by_role("heading", name="原控制计划.xlsx", exact=True)).to_be_visible()
        expect(preview.get_by_text(label, exact=True)).to_be_visible()
        expect(preview.get_by_role("button", name="下载当前报告", exact=True)).to_be_disabled()
        expect(preview.get_by_text("生成服务暂不可用", exact=True)).to_be_visible()
        expect(preview.get_by_role("button", name="重生成同一结果", exact=True)).to_be_disabled()
        assert not any(path.endswith("/pdf") for _method, path in calls)
        assert all(method == "GET" for method, _path in calls)
        browser.close()


def test_all_pass_frozen_verification_remains_visible_in_result_focus_and_report():
    status, not_rerun = "unverified", False
    from product.checker_verification import frozen_checker_verification

    verification = frozen_checker_verification({"mode": "checker_core", "suite_revision_id": "frozen-checker-3", "suite_revision_number": 3, "verification": status, "samples_not_rerun": not_rerun})
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1057, "height": 853})
        page.set_default_timeout(5000)
        round_report_routes(page, verification=verification)
        page.goto(WEB + f"/tasks/{TASK}?file={VERSION}&clause=1")
        notice = page.get_by_role("note", name="本轮验证状态")
        expect(notice).to_contain_text(verification["notice"])
        expect(notice).to_contain_text("部分判定的证据未经验证")
        page.get_by_role("button", name="逐条复核", exact=True).click()
        expect(notice).to_be_visible()
        expect(notice).to_contain_text(verification["notice"])
        page.get_by_role("button", name="查看报告", exact=True).click()
        expect(page.get_by_role("region", name="当前报告").get_by_role("note", name="本轮验证状态")).to_contain_text(verification["notice"])
        page.get_by_role("navigation", name="当前位置", exact=True).get_by_role("link", name="返回审核结果", exact=True).click()
        expect(page.get_by_role("region", name="审核详情").get_by_role("note", name="本轮验证状态")).to_contain_text(verification["notice"])
        browser.close()
