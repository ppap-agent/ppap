from __future__ import annotations

import json
from html import unescape

import httpx
import pytest
from product.feishu_notifications import send_task_notification, task_notification_card


def _text(card):
    return unescape(card["body"]["elements"][0]["content"])


def test_confirmation_card_shows_the_frozen_choices_before_offering_an_explicit_confirmation() -> None:
    task = {
        "id": "task-a",
        "number": "001",
        "estimated_points": 500,
        "files": [
            {
                "selection": "selected",
                "file": {"id": "version-0", "original_filename": "控制计划.xlsx"},
                "match": {"state": "matched", "rule": {"title": "海岸线控制计划要求"}, "gaps": ["metadata_uses_excerpt", "缺少过程流程图"]},
            },
            {"selection": "selected", "file": {"id": "version-1", "original_filename": "未匹配.pdf"}, "match": {"state": "unmatched", "rule": None, "gaps": []}},
        ],
    }
    card = task_notification_card(task, "confirmation", "https://ppap.example.test", confirmation_id="intent-a")
    text = _text(card)
    assert card["schema"] == "2.0" and "elements" not in card
    assert all(value in text for value in ("控制计划.xlsx", "海岸线控制计划要求", "缺少过程流程图", "未匹配.pdf", "不纳入审核", "预计点数：500"))
    assert "metadata_uses_excerpt" not in text
    confirm, opened = card["body"]["elements"][1:]
    assert confirm["behaviors"] == [{"type": "callback", "value": {"action": "confirm_audit_task", "notification_id": "intent-a"}}]
    assert confirm["confirm"]["title"]["content"] == "开始本次审核？"
    assert "预计点数：500" in confirm["confirm"]["text"]["content"]
    assert opened["behaviors"][0]["type"] == "open_url"
    task["files"][0]["file"]["original_filename"] = "很长的名称" * 6000
    bounded = task_notification_card(task, "confirmation", "https://ppap.example.test", confirmation_id="intent-a")
    assert len(json.dumps(bounded, ensure_ascii=False).encode()) <= 26000
    assert [button["behaviors"][0]["type"] for button in bounded["body"]["elements"][1:]] == ["open_url"]
    assert bounded["body"]["elements"][1]["type"] == "primary_filled"
    assert "完整文件与原因请打开审核查看。" in _text(bounded)


def test_card_callback_authenticates_the_app_and_operator_without_treating_card_update_token_as_verification() -> None:
    from product.feishu import AppMismatch, CardAction, UnauthenticatedEvent, UnknownEvent, accept_event
    from product.settings import Settings

    settings = Settings(feishu_app_id="cli_test", feishu_verification_token="verify-test")
    body = {
        "schema": "2.0",
        "header": {"event_type": "card.action.trigger", "app_id": "cli_test", "token": "verify-test", "tenant_key": "app-owner-tenant"},
        "event": {
            "operator": {"tenant_key": "customer-tenant", "open_id": "ou_creator"},
            "token": "card-update-token",
            "action": {"tag": "button", "value": {"action": "confirm_audit_task", "notification_id": "intent-a"}},
            "context": {"open_message_id": "om_original"},
        },
    }
    assert accept_event(settings, body) == CardAction("customer-tenant", "ou_creator", "intent-a", "om_original")
    assert isinstance(accept_event(settings, {**body, "header": {**body["header"], "token": "card-update-token"}}), UnauthenticatedEvent)
    assert isinstance(accept_event(settings, {**body, "header": {**body["header"], "app_id": "other-app"}}), AppMismatch)
    for field in ("operator", "context", "action"):
        assert isinstance(accept_event(settings, {**body, "event": {**body["event"], field: {}}}), UnknownEvent)


def test_confirmation_without_a_rule_or_selection_does_not_offer_a_blind_acceptance() -> None:
    task = {
        "id": "task-a",
        "number": "001",
        "estimated_points": 500,
        "files": [
            {"selection": "selected", "file": {"id": "version-2", "original_filename": "待核对.xlsx"}, "match": {"state": "matched", "rule": None}},
            {"selection": "unselected", "file": {"id": "version-3", "original_filename": "未选择.xlsx"}, "match": None},
        ],
    }
    card = task_notification_card(task, "confirmation", "https://ppap.example.test", confirmation_id="intent-a")
    assert [button["behaviors"][0]["type"] for button in card["body"]["elements"][1:]] == ["open_url"]
    assert "**未选择.xlsx**<br>不纳入审核；未选择此文件" in _text(card)


def test_incomplete_audit_is_an_exception_not_a_ready_report_or_a_raw_unknown_label() -> None:
    task = {
        "id": "task-a",
        "number": "001",
        "files": [
            {
                "selection": "selected",
                "file": {"id": "version-0", "original_filename": "控制计划.xlsx"},
                "audit": {"execution": "completed", "conclusion": "UNKNOWN", "report": {"state": "not_eligible"}},
            }
        ],
    }
    card = task_notification_card(task, "completed", "https://ppap.example.test")
    assert card["header"]["title"]["content"] == "审核已结束，未生成正式报告"
    content = _text(card)
    assert "审核异常 1 份" in content
    assert "**控制计划.xlsx**<br><text_tag color='neutral'>无正式结论</text_tag>" in content
    assert "UNKNOWN" not in content


def test_untyped_provider_message_identifier_does_not_prove_delivery() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"code": 0, "data": {"message_id": 7}}))) as http:
        result = send_task_notification(http, "test-token", "intent-a", "ou_creator", {"header": {"title": {"content": "test"}}})
    assert result == {"state": "unknown", "message_id": None, "reason": "send_receipt_unknown"}


def test_lost_receipt_does_not_retry_the_provider_send() -> None:
    requests = []

    def lose_reply(request):
        requests.append(json.loads(request.content))
        raise httpx.ReadTimeout("reply lost", request=request)

    with httpx.Client(transport=httpx.MockTransport(lose_reply)) as http:
        result = send_task_notification(http, "test-token", "intent-a", "ou_creator", {"header": {"title": {"content": "test"}}})
    assert result["state"] == "unknown"
    assert len(requests) == 1
    assert requests[0]["uuid"] == "intent-a"
    assert requests[0]["receive_id"] == "ou_creator"


@pytest.mark.parametrize(
    ("body", "expected"),
    [({"code": 0, "data": {"message_id": "om_sent"}}, "sent"), ({"code": 230002, "msg": "rejected"}, "failed"), ({"code": False, "data": {"message_id": "om_not_proof"}}, "unknown")],
)
def test_receipt_distinguishes_provider_acceptance_rejection_and_unconfirmed_data(body, expected) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))) as http:
        assert send_task_notification(http, "test-token", "intent-a", "ou_creator", {})["state"] == expected


@pytest.mark.parametrize(("status", "expected"), [(400, "failed"), (403, "failed"), (500, "unknown")])
def test_explicit_permission_rejection_is_not_a_lost_send_receipt(status, expected) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"code": 99991672, "msg": "permission denied"}))) as http:
        receipt = send_task_notification(http, "test-token", "intent-a", "ou_creator", {})
    assert receipt["state"] == expected
    assert receipt["message_id"] is None


@pytest.mark.parametrize("stage", ["confirmation", "started", "completed"])
def test_recall_card_separates_comparison_material_from_audited_files(stage) -> None:
    task = {
        "id": "task-a",
        "number": "001",
        "estimated_points": 500,
        "files": [
            {
                "selection": "selected",
                "file": {"id": "version-0", "original_filename": "控制计划.xlsx"},
                "match": {"state": "matched", "rule": {"title": "控制计划规则"}},
                "audit": {"execution": "completed", "conclusion": "PASS", "report": {"state": "ready"}},
            },
            {"selection": "selected", "file": {"id": "version-4", "original_filename": "特殊特性清单.xlsx"}, "match": {"state": "reference"}, "audit": None},
        ],
    }
    card = task_notification_card(task, stage, "https://ppap.example.test", confirmation_id="intent-a")
    body = _text(card)
    assert "**比对资料**<br>特殊特性清单.xlsx<br><font color='grey'>已固定版本，不单独审核。</font>" in body
    assert "审核：特殊特性清单.xlsx" not in body
    assert "特殊特性清单.xlsx：无正式结论" not in body
    if stage == "confirmation":
        assert "已匹配 **1 份**，共 1 份" in body
        assert "匹配尚未确认" not in body
        assert card["body"]["elements"][1]["behaviors"][0]["value"]["notification_id"] == "intent-a"
    elif stage == "started":
        assert "本次审核 **1 份文件**" in body
    else:
        assert "**报告已就绪 1 份**" in body
        assert "报告生成失败" not in body and "审核异常" not in body


def test_zero_match_card_does_not_claim_an_audit_or_report_and_oversized_card_fails_boundedly() -> None:
    task = {"id": "task-a", "number": "001", "completion_reason": "no_matching_rules", "files": []}
    card = task_notification_card(task, "completed", "https://ppap.example.test")
    assert "未开始审核，未生成正式报告" in _text(card)
    with pytest.raises(ValueError, match="notification_card_too_large"):
        task_notification_card(task, "completed", "https://ppap.example.test/" + "x" * 30000)


def test_zip_card_names_only_the_requested_collection_and_uses_an_authenticated_operation_link() -> None:
    from urllib.parse import parse_qs, urlsplit

    task = {
        "id": "task-a",
        "number": "001",
        "files": [{"selection": "selected", "file": {"id": version, "original_filename": name}} for version, name in (("v1", "控制计划.xlsx"), ("v2", "不在包内.docx"), ("v3", "流程图.pdf"))],
    }
    package = {"state": "ready", "reports": [{"file_version_id": "v1"}, {"file_version_id": "v3"}], "receipt": {"request_id": "zip-request-a"}}
    card = task_notification_card(task, "zip_ready", "https://ppap.example.test", package)
    action = card["body"]["elements"][1]
    assert action["text"]["content"] == "打开本次 ZIP"
    body = _text(card)
    assert "已打包 **2 份报告**" in body and "不在包内.docx" not in body
    outer = urlsplit(action["behaviors"][0]["default_url"])
    assert outer.path == "/enter-current"
    target = urlsplit(parse_qs(outer.query)["return_to"][0])
    assert target.path == "/audit" and parse_qs(target.query) == {"task": ["task-a"], "report": ["1"], "packageRequest": ["zip-request-a"]}


def test_cross_task_zip_notification_restores_the_archive_package_and_frozen_report_names() -> None:
    from urllib.parse import parse_qs, urlsplit

    task = {"id": "task-a", "number": "001", "files": []}
    references = [{"task_id": "task-a", "file_version_id": "v1"}, {"task_id": "task-b", "file_version_id": "v2"}]
    package = {
        "state": "ready",
        "reports": references,
        "receipt": {"request_id": "archive-package"},
        "outcomes": [{"reference": reference, "filename": name} for reference, name in zip(references, ("控制计划-审核报告.pdf", "流程图-审核报告.pdf"), strict=True)],
    }
    card = task_notification_card(task, "zip_ready", "https://ppap.example.test", package)
    body = _text(card)
    assert card["header"]["subtitle"]["content"] == "文件档案"
    assert all(value in body for value in ("控制计划-审核报告.pdf", "流程图-审核报告.pdf"))
    target = urlsplit(parse_qs(urlsplit(card["body"]["elements"][1]["behaviors"][0]["default_url"]).query)["return_to"][0])
    assert target.path == "/audit" and parse_qs(target.query) == {"view": ["archive"], "packageRequest": ["archive-package"]}


@pytest.mark.parametrize("stage", ["confirmation", "started", "completed", "zip_ready"])
def test_json_v2_card_escapes_dynamic_markdown_and_keeps_one_authenticated_main_action(stage) -> None:
    name = "[点击](https://wrong.test)<at id=all></at>_*`\\\n.xlsx"
    task = {
        "id": "task-a",
        "number": "001",
        "files": [{"selection": "selected", "file": {"id": "file-a", "original_filename": name}, "match": {"state": "matched", "rule": {"title": name}, "gaps": [name]}}],
    }
    package = {"state": "ready", "reports": [{"file_version_id": "file-a"}], "receipt": {"request_id": "zip-a"}}
    card = task_notification_card(task, stage, "https://ppap.example.test", package if stage == "zip_ready" else None)
    body = card["body"]["elements"][0]["content"]
    assert card["schema"] == "2.0" and card["config"]["update_multi"] is True
    assert card["header"]["template"] == "blue" and card["header"]["subtitle"]["content"] == "001"
    assert "elements" not in card and "wide_screen_mode" not in card["config"]
    assert card["body"]["elements"][0]["tag"] == "markdown"
    assert "[点击]" not in body and "<at" not in body and "_*`" not in body
    assert name.replace("\n", " ") in _text(card)
    button = card["body"]["elements"][1]
    assert len(card["body"]["elements"]) == 2
    assert button["type"] == "primary_filled" and button["width"] == "fill"
    assert button["behaviors"][0]["type"] == "open_url"
    assert "url" not in button and "value" not in button


def test_completed_card_uses_actual_counts_without_changing_ng_or_inventing_pass_for_missing_results() -> None:
    task = {
        "id": "task-a",
        "number": "001",
        "files": [
            {
                "selection": "selected",
                "file": {"id": "version-0", "original_filename": "控制计划.xlsx"},
                "audit": {
                    "execution": "completed",
                    "conclusion": "NG",
                    "counts": {"pass_count": 2, "ng_count": 9, "unknown_count": 0, "not_covered_count": 0, "error_count": 0},
                    "report": {"state": "ready"},
                },
            },
            {
                "selection": "selected",
                "file": {"original_filename": "流程图.xlsx"},
                "audit": {
                    "execution": "review_failed",
                    "conclusion": None,
                    "counts": {"pass_count": 0, "ng_count": 0, "unknown_count": 1, "not_covered_count": 2, "error_count": 3},
                    "report": {"state": "not_eligible"},
                },
            },
        ],
    }
    card = task_notification_card(task, "completed", "https://ppap.example.test")
    assert "符合 2 项 · 不符合 9 项 · 无法判断 1 项 · 未覆盖 2 项 · 执行异常 3 项" in _text(card)
    assert "<text_tag color='red'>不符合</text_tag> 符合 2 项 · 不符合 9 项" in _text(card)
    assert "<text_tag color='neutral'>无正式结论</text_tag>" in _text(card)
    assert "审核异常 1 份" in _text(card)
    assert card["header"]["template"] != "green"


@pytest.mark.parametrize("state", ["completed", "failed", "unknown", "stopped"])
def test_optimization_recall_does_not_claim_publication_or_unknown_success(state):
    from urllib.parse import parse_qs, urlsplit

    from product.feishu_notifications import optimization_notification_card

    operation = {
        "operation_id": "operation-a",
        "state": state,
        "report": {"validation": {"passed_samples": 1, "total_samples": 1, "new_errors": 0, "verified": False}},
    }
    sample = {"round_id": "round-a", "file_version_id": "file-a", "rule_clause_id": "R1 & <b>", "rationale": "private", "evidence": [{"excerpt": "private original"}]}
    card = optimization_notification_card(operation, sample, "https://ppap.example.test")
    assert card["schema"] == "2.0" and "未自动发布" in _text(card)
    assert "private" not in json.dumps(card)
    assert "验证通过" not in _text(card)
    if state == "completed":
        assert "需要重新验证" in _text(card) and "1/1" in _text(card)
    if state == "unknown":
        assert "未知" in _text(card) and "不会自动重试" in _text(card)
    target = parse_qs(urlsplit(card["body"]["elements"][1]["behaviors"][0]["default_url"]).query)["return_to"][0]
    assert parse_qs(urlsplit(target).query)["clause"] == ["R1 & <b>"]


@pytest.mark.parametrize("all_blocked", [False, True])
def test_confirmation_card_displays_only_server_admission_scope_and_exclusion_reasons(all_blocked):
    task = {
        "id": "task-a",
        "number": "001",
        "estimated_points": 0 if all_blocked else 500,
        "confirmation_scope": {
            "target_file_version_ids": [] if all_blocked else ["good"],
            "reference_file_version_ids": ["ref"],
            "excluded_files": [{"file_version_id": "bad", "reason": "已认证的阻断问题，请修正原件"}],
        },
        "files": [
            {"file": {"id": "bad", "original_filename": "阻断目标.xlsx"}, "selection": "selected", "match": {"state": "matched", "rule": {"title": "目标规则"}}},
            {"file": {"id": "ref", "original_filename": "比对资料.xlsx"}, "selection": "selected", "match": {"state": "reference"}},
        ],
    }
    if not all_blocked:
        task["files"].append({"file": {"id": "good", "original_filename": "可审目标.xlsx"}, "selection": "selected", "match": {"state": "matched", "rule": {"title": "目标规则"}}})
    card = task_notification_card(task, "confirmation", "https://ppap.example.test", confirmation_id="intent-a")
    text = _text(card)
    assert "阻断目标.xlsx**<br>不纳入审核；已认证的阻断问题，请修正原件" in text
    assert "**比对资料**<br>比对资料.xlsx" in text
    assert f"已匹配 **{0 if all_blocked else 1} 份**" in text
    assert f"预计点数：{0 if all_blocked else 500}" in text
    callbacks = [element for element in card["body"]["elements"] if any(behavior["type"] == "callback" for behavior in element.get("behaviors", []))]
    assert len(callbacks) == (0 if all_blocked else 1)


def test_formalization_card_lists_decisions_in_order_and_links_to_the_rule():
    from urllib.parse import parse_qs, urlsplit

    from product.feishu_notifications import formalization_notification_card

    context = {"rule_version_id": "v2", "root_rule_version_id": "v1", "title": "签字*规则", "total": 4, "counts": {"ready": 2, "question": 1, "decided": 1}}
    card = formalization_notification_card(context, "https://ppap.example.test/")
    assert card["schema"] == "2.0"
    text = card["body"]["elements"][0]["content"]
    assert text.index("要选择 **1** 条") < text.index("可以直接确认 **2** 条") < text.index("已处理 **1** 条")
    assert "要补充标准" not in text and "签字&#42;规则" in text
    link = card["body"]["elements"][1]["behaviors"][0]["default_url"]
    target = parse_qs(urlsplit(link).query)["return_to"][0]
    assert target == "https://ppap.example.test/requirements?requirement=v1&reqVersion=v2"
