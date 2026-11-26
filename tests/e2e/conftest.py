"""Shared browser test setup through public HTTP interfaces."""

import copy
import os
import time
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")
expect.set_options(timeout=20_000)


def route_api_address(page) -> None:
    """Point the Web at the controlled API under the same origin."""
    page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))


def pick_audit_files(page, files) -> None:
    """A disabled input still accepts set_input_files, and the page then ignores the files."""
    picker = page.get_by_label("选择审核文件", exact=True)
    expect(picker).to_be_enabled()
    picker.set_input_files(files)


def wait_until(page, predicate, message: str, timeout: float = 5.0):
    """Bounded poll for state Playwright's expect cannot observe, such as the clipboard or a route callback."""
    deadline = time.monotonic() + timeout
    while not (value := predicate()):
        if time.monotonic() >= deadline:
            raise AssertionError(message)
        page.wait_for_timeout(20)
    return value


# A completed task with one frozen round, file and ready report, served entirely by controlled routes.
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
        "checks": [{"rule_item": rule["body"]["rules"][0], "execution": "completed", "coverage": "executed", "finding": finding, "evidence": [], "scope": None, "review": None}],
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
        },
    }
    task = {
        "id": TASK,
        "number": "PPAP-原审核",
        "creator": {"name": "示例用户", "mine": True},
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
    route_api_address(page)

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
