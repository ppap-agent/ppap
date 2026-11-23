from __future__ import annotations

import json
from html import unescape

import httpx
import pytest
from product.feishu_notifications import send_task_notification, task_notification_card


def _text(card):
    return unescape(card["body"]["elements"][0]["content"])


def test_confirmation_card_shows_the_frozen_choices_and_links_to_the_task() -> None:
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
    card = task_notification_card(task, "confirmation", "https://ppap.example.test")
    text = _text(card)
    assert card["schema"] == "2.0" and "elements" not in card
    assert all(value in text for value in ("控制计划.xlsx", "海岸线控制计划要求", "缺少过程流程图", "未匹配.pdf", "不纳入审核", "预计点数：500"))
    assert "metadata_uses_excerpt" not in text
    # Starting is signed in the Web (#355): the card's only action opens the task.
    (opened,) = card["body"]["elements"][1:]
    assert opened["behaviors"][0]["type"] == "open_url" and opened["type"] == "primary_filled"
    task["files"][0]["file"]["original_filename"] = "很长的名称" * 6000
    bounded = task_notification_card(task, "confirmation", "https://ppap.example.test")
    assert len(json.dumps(bounded, ensure_ascii=False).encode()) <= 26000
    assert [button["behaviors"][0]["type"] for button in bounded["body"]["elements"][1:]] == ["open_url"]
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
    card = task_notification_card(task, "confirmation", "https://ppap.example.test")
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
    card = task_notification_card(task, stage, "https://ppap.example.test")
    body = _text(card)
    assert "**比对资料**<br>特殊特性清单.xlsx<br><font color='grey'>已固定版本，不单独审核。</font>" in body
    assert "审核：特殊特性清单.xlsx" not in body
    assert "特殊特性清单.xlsx：无正式结论" not in body
    if stage == "confirmation":
        assert "已匹配 **1 份**，共 1 份" in body
        assert "匹配尚未确认" not in body
        assert card["body"]["elements"][1]["behaviors"][0]["type"] == "open_url"
    elif stage == "started":
        assert "本次审核 **1 份文件**" in body
    else:
        assert "**报告已就绪 1 份**" in body
        assert "报告生成失败" not in body and "审核异常" not in body


def test_zero_match_card_does_not_claim_an_audit_or_report_and_oversized_card_fails_boundedly() -> None:
    task = {"id": "task-a", "number": "001", "completion_reason": "no_matching_rules", "files": []}
    card = task_notification_card(task, "completed", "https://ppap.example.test")
    assert card["header"]["title"]["content"] == "本次任务无送审文件"
    assert "本次未勾选送审文件。未开始审核，未扣费，也没有生成审核报告。" in _text(card)
    assert "报告已就绪" not in _text(card)
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


def test_completed_card_names_pending_clauses_beside_the_aggregated_file_label() -> None:
    audit = {"execution": "completed", "conclusion": "PASS", "counts": {"pass_count": 3, "ng_count": 0, "unknown_count": 1, "not_covered_count": 1, "error_count": 0}, "report": {"state": "ready"}}
    task = {"id": "task-a", "number": "001", "files": [{"selection": "selected", "file": {"id": "version-0", "original_filename": "控制计划.xlsx"}, "audit": audit}]}
    assert "<text_tag color='green'>符合</text_tag> 另 2 项待审核 符合 3 项" in _text(task_notification_card(task, "completed", "https://ppap.example.test"))


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
    card = task_notification_card(task, "confirmation", "https://ppap.example.test")
    text = _text(card)
    assert "阻断目标.xlsx**<br>不纳入审核；已认证的阻断问题，请修正原件" in text
    assert "**比对资料**<br>比对资料.xlsx" in text
    assert f"已匹配 **{0 if all_blocked else 1} 份**" in text
    assert f"预计点数：{0 if all_blocked else 500}" in text
    callbacks = [element for element in card["body"]["elements"] if any(behavior["type"] == "callback" for behavior in element.get("behaviors", []))]
    assert callbacks == []


def test_card_body_names_the_chosen_rule_set_by_its_registered_name() -> None:
    task = {"id": "task-a", "number": "001", "customer_preference": "UAES", "files": []}
    assert "审核客户：联电科技/UAES" in _text(task_notification_card(task, "started", "https://ppap.example.test"))
    assert "客户" not in _text(task_notification_card({**task, "customer_preference": None}, "started", "https://ppap.example.test"))


def test_a_card_that_lists_everything_starts_the_audit_through_the_web_route() -> None:
    """With card callbacks reaching PPAP, the button is the Web's own start call, bound to the card's revision and quote (#355)."""
    from product.feishu_notifications import start_call

    task = {
        "id": "1b4e28ba-2fa1-41d2-883f-0016d3cca427",
        "number": "001",
        "estimated_points": 500,
        "input_revision": 3,
        "confirmation_basis_digest": "q-3",
        "files": [{"selection": "selected", "file": {"id": "v-1", "original_filename": "控制计划.xlsx"}, "match": {"state": "matched", "rule": {"title": "控制计划要求"}}}],
    }
    plain = task_notification_card(task, "confirmation", "https://ppap.example.test")
    assert [button["behaviors"][0]["type"] for button in plain["body"]["elements"][1:]] == ["open_url"]
    card = task_notification_card(task, "confirmation", "https://ppap.example.test", buttons=True)
    start, opened = card["body"]["elements"][1:]
    call = start["behaviors"][0]["value"]["call"]
    assert call == start_call(task) and call["path"] == f"/audit-tasks/{task['id']}/confirm" and call["method"] == "POST"
    assert call["body"]["expected_revision"] == 3 and call["body"]["expected_confirmation_basis_digest"] == "q-3"
    # The same card, clicked twice, is the same request; a newer quote is another one.
    assert start_call(task)["body"]["request_id"] == call["body"]["request_id"] != start_call({**task, "confirmation_basis_digest": "q-4"})["body"]["request_id"]
    assert "预计点数：500" in start["confirm"]["text"]["content"] and opened["behaviors"][0]["type"] == "open_url"


def test_a_card_click_is_a_signing_call_only_on_a_route_cards_may_call() -> None:
    from product.feishu import CardAction, CardCall, UnknownEvent, parse_event

    def click(value):
        return parse_event(
            {
                "schema": "2.0",
                "header": {"event_type": "card.action.trigger"},
                "event": {"operator": {"tenant_key": "t", "open_id": "ou_a"}, "action": {"tag": "button", "value": value}, "context": {"open_message_id": "om_1"}},
            }
        )

    path = "/audit-tasks/1b4e28ba-2fa1-41d2-883f-0016d3cca427/confirm"
    assert click({"call": {"method": "POST", "path": path, "body": {"request_id": "r"}}}) == CardCall("t", "ou_a", "om_1", "POST", path, {"request_id": "r"})
    assert isinstance(click({"call": {"method": "POST", "path": "/rulebooks/uaes/plan", "body": {}}}), UnknownEvent)
    assert isinstance(click({"call": {"method": "DELETE", "path": path, "body": {}}}), UnknownEvent)
    assert isinstance(click({"action": "confirm_audit_task", "notification_id": "n"}), CardAction)
