"""Native re-audit input choices through controlled public HTTP responses."""

from copy import deepcopy
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from .conftest import WEB, pick_audit_files, route_api_address

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
            # The original audit's rule version first; rule A has a newer version since.
            "rule_options": [{"id": "rule-" + letter, "title": f"原规则 {letter} · 原审核版本"}] + ([{"id": "rule-A-new", "title": "原规则 A · 较新版本"}] if letter == "A" else []),
        }
        for letter, name in [("A", "原文件A.xlsx"), ("B", "原文件B.xlsx")]
    ]
    commands = []
    uploaded = {}
    task = None
    preparation = None
    route_api_address(page)

    def api(route):
        nonlocal task, preparation
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
            route.fulfill(json={"customers": [{"customer_id": "uaes", "name": "UAES", "key": "UAES", "coverage": None}], "unregistered": [], "type_names": {}})
        elif path == f"/rounds/{ROUND}/reaudit-inputs":
            route.fulfill(json={"predecessor_id": ROUND, "customer_preference": "UAES", "files": files})
        elif path == "/audit-tasks" and request.method == "GET":
            route.fulfill(json={"tasks": [], "next_offset": None})
        elif path == "/drafts" and request.method == "POST":
            body = request.post_data_json
            assert preparation is None
            assert body["upload_preparation"]["predecessor_id"] == ROUND
            preparation = {
                "id": body["request_id"],
                "status": "open",
                "task_id": None,
                "upload_preparation": deepcopy(body["upload_preparation"]),
                "preparation_revision": 0,
                "items": [],
                "predecessor_id": ROUND,
                "case_context": {},
                "writeback": {"available": False},
                "updated_at": "2026-10-22T00:00:00Z",
            }
            route.fulfill(status=201, json=deepcopy(preparation))
        elif preparation and path == "/drafts/" + preparation["id"] and request.method in ("GET", "PUT"):
            if request.method == "PUT":
                body = request.post_data_json
                assert preparation["task_id"] is None
                assert body["expected_revision"] == preparation["preparation_revision"]
                assert body["upload_preparation"]["predecessor_id"] == ROUND
                preparation.update(upload_preparation=deepcopy(body["upload_preparation"]), preparation_revision=body["expected_revision"] + 1)
            route.fulfill(json=deepcopy(preparation))
        elif task and path == "/audit-tasks/" + task["id"] and request.method == "GET":
            route.fulfill(json=deepcopy(task))
        elif path == "/files" and request.method == "POST":
            body = request.post_data_json
            assert preparation is not None
            assert any(row["request_id"] == body["request_id"] and row["sha256"] == body["sha256"] for row in preparation["upload_preparation"]["uploads"])
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
            assert preparation is not None
            assert body["source_draft_id"] == preparation["id"]
            assert body["expected_draft_revision"] == preparation["preparation_revision"]
            assert body["files"] == preparation["upload_preparation"]["files"]
            commands.append(body)
            if not accept:
                route.fulfill(status=422, json={"error": "controlled_no_acceptance"})
                return
            returned = []
            for choice in body["files"]:
                source = next((file for file in files if file["file"]["id"] == choice["file_version_id"]), None)
                # A new upload carries rule options only as the replacement of an original file, whose options it inherits.
                replaced = next((file for file in files if file["file"]["id"] == choice.get("replacement_of_file_version_id")), None)
                row = {**source} if source else {"file": dict(uploaded), "validation": {"state": "valid", "reasons": []}, **({"rule_options": replaced["rule_options"]} if replaced else {})}
                for field in ("preferred_rule_version_id", "reference_only", "replacement_of_file_version_id"):
                    row.pop(field, None)
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
                "id": body["source_draft_id"],
                "number": "PPAP-受控重审",
                "creator": {"name": "示例用户", "mine": True},
                "state": "pending_confirm",
                "recognition_request_id": body["request_id"],
                "input_revision": 0,
                "source_draft_id": body["source_draft_id"],
                "predecessor_id": ROUND,
                "round_id": None,
                "customer_preference": body["customer_preference"],
                "created_at": "2026-10-20T00:00:00Z",
                "updated_at": "2026-10-20T00:00:00Z",
                "completion_reason": None,
                "estimated_points": 0,
                "progress": {"completed": 0, "total": 2},
                "acceptance": None,
                "files": returned,
            }
            preparation.update(task_id=task["id"], preparation_revision=None)
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
        # The re-audit opens on the upload page with the original's files already selected.
        expect(page.get_by_role("checkbox", name="选择 原文件A.xlsx", exact=True)).to_be_checked()
        pick_audit_files(page, {"name": "补件C.xlsx", "mimeType": "application/octet-stream", "buffer": b"fixture"})
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


def test_a_newer_rule_version_is_used_by_default_and_inherited_by_a_replacement(reaudit_page):
    page, commands = reaudit_page
    # Rule versions are chosen once for the whole re-audit, never per file on the upload page.
    expect(page.get_by_role("combobox", name="原文件A.xlsx 的识别方式", exact=True)).to_have_count(0)
    choose_replacement(page, "替换：原文件A.xlsx")
    files = recognition_command(page, commands)
    assert files[1]["preferred_rule_version_id"] == "rule-B"
    assert files[2]["replacement_of_file_version_id"] == "A"
    assert files[2]["preferred_rule_version_id"] == "rule-A-new"


@pytest.mark.parametrize("reaudit_page", [True], indirect=True)
def test_the_rule_update_reminder_names_the_changed_rules_and_defaults_to_the_new_version(reaudit_page):
    page, commands = reaudit_page
    files = recognition_command(page, commands)
    assert [file["preferred_rule_version_id"] for file in files[:2]] == ["rule-A-new", "rule-B"]
    expect(page.get_by_role("heading", name="预检并匹配规则", exact=True)).to_be_visible()
    # The page names the step; the re-audit is marked beside it.
    expect(page.get_by_text("关联重审", exact=True)).to_be_visible()
    reminder = page.get_by_role("region", name="审核规则已更新", exact=True)
    expect(reminder).to_contain_text("原规则 A在上次审核后有新版本。")
    versions = reminder.get_by_role("group", name="本次使用的规则版本", exact=True)
    expect(versions.get_by_role("button", name="使用新版本", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(versions.get_by_role("button", name="沿用上次版本", exact=True)).to_have_attribute("aria-pressed", "false")
    assert len(commands) == 1


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
    # Recognition results open on the confirm-rules page, also after a reload.
    expect(page.get_by_role("heading", name="预检并匹配规则", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name="预检并匹配规则", exact=True)).to_be_visible()
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
    # Recognition results open on the confirm-rules page, also after a reload.
    expect(page.get_by_role("heading", name="预检并匹配规则", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name="预检并匹配规则", exact=True)).to_be_visible()
    page.get_by_role("button", name="上一步", exact=True).click()
    page.get_by_role("checkbox", name="选择 补件C.xlsx", exact=True).check()
    expect(page.get_by_role("checkbox", name="选择 原文件A.xlsx", exact=True)).to_have_count(0)
    expect(page.get_by_role("combobox", name="补件C.xlsx 的补件关系", exact=True)).to_have_text("替换：原文件A.xlsx")
    assert len(commands) == 1


@pytest.mark.parametrize("reaudit_page", [True], indirect=True)
def test_the_decision_and_the_start_stay_on_screen_at_the_smallest_supported_window(reaudit_page):
    page, commands = reaudit_page
    page.set_viewport_size({"width": 1280, "height": 800})
    recognition_command(page, commands)
    heading = page.get_by_role("heading", name="预检并匹配规则", exact=True)
    start = page.get_by_role("button", name="开始审核", exact=True)
    expect(heading).to_be_in_viewport()
    expect(page.get_by_role("list", name="审核步骤", exact=True)).to_be_in_viewport()
    expect(start).to_be_in_viewport()
    # Only the file list scrolls; the page itself never does.
    assert page.evaluate("document.scrollingElement.scrollHeight <= window.innerHeight")
    # A single-action page keeps the task list folded, with the way back to it.
    expect(page.get_by_role("button", name="展开审核任务", exact=True)).to_be_visible()
    page.mouse.move(800, 400)
    page.mouse.wheel(0, 2000)
    expect(heading).to_be_in_viewport()
    expect(start).to_be_in_viewport()
