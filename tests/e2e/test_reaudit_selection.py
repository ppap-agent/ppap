"""Native re-audit input choices through controlled public HTTP responses."""

import os
from copy import deepcopy
from urllib.parse import urlsplit

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")
ROUND = "00000000-0000-4000-8000-000000000001"


def reaudit_routes(page, *, accept=False):
    files = [
        {
            "file": {"id": letter, "file_id": "file-" + letter, "original_filename": name, "status": "ready", "content_type": "application/octet-stream", "byte_size": 7, "sha256": None},
            "selection": "selected",
            "removal_reason": None,
            "validation": {"state": "valid", "reasons": []},
            "validation_id": "validation-" + letter,
            "match": None,
            "preferred_rule_version_id": "rule-" + letter,
            "rule_options": [{"id": "rule-" + letter, "title": "原规则 " + letter}],
        }
        for letter, name in [("A", "原文件A.xlsx"), ("B", "原文件B.xlsx")]
    ]
    commands = []
    uploaded = {}
    task = None
    page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))

    def api(route):
        nonlocal task
        request = route.request
        path = urlsplit(request.url).path.removeprefix("/api")
        if path == "/public-config":
            route.fulfill(
                json={
                    "git_sha": "controlled-reaudit",
                    "api_url": WEB + "/api",
                    "profile": "isolated-dev",
                    "login": "isolated-dev",
                    "feishu_app_id": "",
                    "deep_offered": False,
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
        elif path == "/audit-customers":
            route.fulfill(json={"customers": []})
        elif path == f"/rounds/{ROUND}/reaudit-inputs":
            route.fulfill(json={"predecessor_id": ROUND, "analysis_mode": "standard", "customer_preference": None, "files": files})
        elif path == "/audit-tasks" and request.method == "GET":
            route.fulfill(json={"tasks": [], "next_offset": None})
        elif task and path == "/audit-tasks/" + task["id"] and request.method == "GET":
            current = deepcopy(task)
            if accept == "legacy":
                # A pre-fix persisted task cannot tell whether A was selected
                # before its automatic exclusion. Do not invent that lost fact.
                for row in current["files"]:
                    if row["file"]["id"] == "A":
                        row["removal_reason"] = "本次使用补件替换"
            route.fulfill(json=current)
        elif path == "/files" and request.method == "POST":
            body = request.post_data_json
            uploaded.update(
                {
                    "id": body["request_id"],
                    "file_id": "file-C",
                    "original_filename": body["original_filename"],
                    "status": "ready",
                    "content_type": body["content_type"],
                    "byte_size": body["byte_size"],
                    "sha256": body["sha256"],
                }
            )
            route.fulfill(status=201, json=uploaded)
        elif path.endswith("/validation"):
            route.fulfill(
                json={
                    "validation": {
                        "id": "validation-C",
                        "source_file_id": uploaded["file_id"],
                        "source_file_version_id": uploaded["id"],
                        "status": "succeeded",
                        "result": {"state": "valid", "reasons": []},
                    }
                }
            )
        elif path == "/audit-tasks" and request.method == "POST":
            body = request.post_data_json
            commands.append(body)
            if not accept:
                route.fulfill(status=422, json={"error": "controlled_no_acceptance"})
                return
            returned = []
            for choice in body["files"]:
                source = next((file for file in files if file["file"]["id"] == choice["file_version_id"]), None)
                row = {**source} if source else {"file": dict(uploaded), "validation": {"state": "valid", "reasons": []}, "rule_options": files[0]["rule_options"]}
                row.update({key: value for key, value in choice.items() if key != "file_version_id"})
                row["match"] = (
                    {
                        "state": "unmatched",
                        "document_type": "控制计划",
                        "metadata": {"customer": None, "model": None, "part": None, "part_number": None},
                        "rule": None,
                        "gaps": ["no_rule_for_customer"],
                    }
                    if choice["selection"] == "selected"
                    else None
                )
                returned.append(row)
            task = {
                "id": body["request_id"],
                "number": "PPAP-受控重审",
                "state": "pending_confirm",
                "recognition_request_id": body["request_id"],
                "input_revision": 0,
                "source_draft_id": body["request_id"],
                "predecessor_id": ROUND,
                "round_id": None,
                "analysis_mode": body["analysis_mode"],
                "customer_preference": body["customer_preference"],
                "created_at": "2026-10-20T00:00:00Z",
                "updated_at": "2026-10-20T00:00:00Z",
                "completion_reason": None,
                "estimated_points": 0,
                "progress": {"completed": 0, "total": 2},
                "acceptance": None,
                "files": returned,
            }
            route.fulfill(status=201, json=task)
        else:
            route.fulfill(status=404, json={"error": "unexpected_request"})

    page.route("**/api/**", api)
    return commands


@pytest.fixture
def reaudit_page(request):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1057, "height": 853})
        page.set_default_timeout(5000)
        commands = reaudit_routes(page, accept=getattr(request, "param", False))
        page.goto(WEB + f"/tasks/new?fromRound={ROUND}")
        expect(page.get_by_role("heading", name="核对本次文件", exact=True)).to_be_visible()
        page.get_by_label("选择审核文件", exact=True).set_input_files({"name": "补件C.xlsx", "mimeType": "application/octet-stream", "buffer": b"fixture"})
        expect(page.get_by_role("checkbox", name="选择 补件C.xlsx", exact=True)).to_be_checked()
        yield page, commands
        browser.close()


def choose_replacement(page, name):
    page.get_by_role("combobox", name="补件C.xlsx 的补件关系", exact=True).click()
    page.get_by_role("option", name=name, exact=True).click()


def recognition_command(page, commands):
    with page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == "/api/audit-tasks"):
        page.get_by_role("button", name="开始识别", exact=True).click()
    assert len(commands) == 1
    assert commands[0]["predecessor_id"] == ROUND
    return commands[0]["files"]


def test_changing_and_clearing_a_replacement_restores_the_previously_selected_originals(reaudit_page):
    page, commands = reaudit_page
    choose_replacement(page, "替换：原文件A.xlsx")
    choose_replacement(page, "替换：原文件B.xlsx")
    expect(page.get_by_role("checkbox", name="选择 原文件A.xlsx", exact=True)).to_be_checked()
    choose_replacement(page, "新增文件 · 重新匹配")
    expect(page.get_by_role("checkbox", name="选择 原文件B.xlsx", exact=True)).to_be_checked()
    files = recognition_command(page, commands)
    assert [file["selection"] for file in files] == ["selected", "selected", "selected"]
    assert all(not file.get("replacement_of_file_version_id") for file in files)


@pytest.mark.parametrize("action", ["uncheck", "remove"])
def test_excluding_a_replacement_restores_its_original(reaudit_page, action):
    page, commands = reaudit_page
    choose_replacement(page, "替换：原文件A.xlsx")
    if action == "uncheck":
        page.get_by_role("checkbox", name="选择 补件C.xlsx", exact=True).uncheck()
    else:
        page.get_by_role("button", name="移除 补件C.xlsx", exact=True).click()
    expect(page.get_by_role("checkbox", name="选择 原文件A.xlsx", exact=True)).to_be_checked()
    if action == "uncheck":
        page.get_by_role("checkbox", name="选择 补件C.xlsx", exact=True).check()
        expect(page.get_by_role("checkbox", name="选择 原文件A.xlsx", exact=True)).to_have_count(0)
        page.get_by_role("checkbox", name="选择 补件C.xlsx", exact=True).uncheck()
    files = recognition_command(page, commands)
    assert [file["selection"] for file in files] == ["selected", "selected", "unselected" if action == "uncheck" else "removed"]
    assert files[0]["removal_reason"] is None


def test_select_none_includes_the_automatically_excluded_original(reaudit_page):
    page, commands = reaudit_page
    choose_replacement(page, "替换：原文件A.xlsx")
    page.get_by_role("checkbox", name="全选可审核文件", exact=True).uncheck()
    expect(page.get_by_role("checkbox", name="选择 原文件A.xlsx", exact=True)).not_to_be_checked()
    expect(page.get_by_role("button", name="开始识别", exact=True)).to_be_disabled()
    page.get_by_role("checkbox", name="全选可审核文件", exact=True).check()
    expect(page.get_by_role("checkbox", name="选择 原文件A.xlsx", exact=True)).to_have_count(0)
    files = recognition_command(page, commands)
    assert [file["selection"] for file in files] == ["removed", "selected", "selected"]
    assert files[2]["replacement_of_file_version_id"] == "A"


@pytest.mark.parametrize("action", ["uncheck", "remove"])
def test_replacement_changes_do_not_reverse_manual_exclusion(reaudit_page, action):
    page, commands = reaudit_page
    if action == "uncheck":
        page.get_by_role("checkbox", name="选择 原文件A.xlsx", exact=True).uncheck()
    else:
        page.get_by_role("button", name="移除 原文件A.xlsx", exact=True).click()
    choose_replacement(page, "替换：原文件A.xlsx")
    choose_replacement(page, "新增文件 · 重新匹配")
    if action == "uncheck":
        expect(page.get_by_role("checkbox", name="选择 原文件A.xlsx", exact=True)).not_to_be_checked()
    else:
        expect(page.get_by_role("checkbox", name="选择 原文件A.xlsx", exact=True)).to_have_count(0)
    files = recognition_command(page, commands)
    assert [file["selection"] for file in files] == ["unselected" if action == "uncheck" else "removed", "selected", "selected"]
    assert files[0]["removal_reason"] == (None if action == "uncheck" else "识别前移除")


@pytest.mark.parametrize("reaudit_page", [True], indirect=True)
def test_server_readback_retains_the_automatic_exclusion_until_replacement_is_cleared(reaudit_page):
    page, commands = reaudit_page
    choose_replacement(page, "替换：原文件A.xlsx")
    recognition_command(page, commands)
    expect(page.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
    page.get_by_role("button", name="上一步", exact=True).click()
    expect(page.get_by_role("checkbox", name="选择 原文件A.xlsx", exact=True)).to_have_count(0)
    choose_replacement(page, "新增文件 · 重新匹配")
    expect(page.get_by_role("checkbox", name="选择 原文件A.xlsx", exact=True)).to_be_checked()
    expect(page.get_by_role("checkbox", name="选择 原文件B.xlsx", exact=True)).to_be_checked()
    assert len(commands) == 1


@pytest.mark.parametrize("reaudit_page", [True], indirect=True)
def test_unselected_replacement_readback_retains_its_explicit_relation(reaudit_page):
    page, commands = reaudit_page
    choose_replacement(page, "替换：原文件A.xlsx")
    page.get_by_role("checkbox", name="选择 补件C.xlsx", exact=True).uncheck()
    files = recognition_command(page, commands)
    assert [file["selection"] for file in files] == ["selected", "selected", "unselected"]
    assert files[2]["replacement_of_file_version_id"] == "A"
    expect(page.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
    page.get_by_role("button", name="上一步", exact=True).click()
    page.get_by_role("checkbox", name="选择 补件C.xlsx", exact=True).check()
    expect(page.get_by_role("checkbox", name="选择 原文件A.xlsx", exact=True)).to_have_count(0)
    expect(page.get_by_role("combobox", name="补件C.xlsx 的补件关系", exact=True)).to_have_text("替换：原文件A.xlsx")
    expect(page.get_by_role("combobox", name="补件C.xlsx 的重审规则", exact=True)).to_have_text("原规则 A")
    assert len(commands) == 1


@pytest.mark.parametrize("reaudit_page", ["legacy"], indirect=True)
@pytest.mark.parametrize("replacement", ["新增文件 · 重新匹配", "替换：原文件B.xlsx"])
def test_legacy_exclusion_without_a_known_prior_choice_returns_unselected(reaudit_page, replacement):
    page, commands = reaudit_page
    choose_replacement(page, "替换：原文件A.xlsx")
    recognition_command(page, commands)
    expect(page.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
    page.get_by_role("button", name="上一步", exact=True).click()
    choose_replacement(page, replacement)
    expect(page.get_by_role("checkbox", name="选择 原文件A.xlsx", exact=True)).not_to_be_checked()
    assert len(commands) == 1
