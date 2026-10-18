from __future__ import annotations

import json

import httpx
import pytest
from ppap_core.feishu_notifications import send_task_notification, task_notification_card


def test_incomplete_audit_is_an_exception_not_a_ready_report_or_a_raw_unknown_label() -> None:
    task = {
        "id": "task-a",
        "number": "001",
        "files": [{"selection": "selected", "file": {"original_filename": "控制计划.xlsx"}, "audit": {"execution": "completed", "conclusion": "UNKNOWN", "report": {"state": "not_eligible"}}}],
    }
    card = task_notification_card(task, "completed", "https://ppap.example.test")
    assert card["header"]["title"]["content"] == "审核已结束，未生成正式报告"
    content = card["elements"][0]["text"]["content"]
    assert "审核异常 1 份" in content
    assert "控制计划.xlsx：无正式结论" in content
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


def test_zero_match_card_does_not_claim_an_audit_or_report_and_oversized_card_fails_boundedly() -> None:
    task = {"id": "task-a", "number": "001", "completion_reason": "no_matching_rules", "files": []}
    card = task_notification_card(task, "completed", "https://ppap.example.test")
    assert "未开始审核，未生成正式报告" in card["elements"][0]["text"]["content"]
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
    action = card["elements"][1]["actions"][0]
    assert action["text"]["content"] == "打开本次 ZIP"
    body = card["elements"][0]["text"]["content"]
    assert "已打包 2 份报告" in body and "不在包内.docx" not in body
    outer = urlsplit(action["url"])
    assert outer.path == "/enter-current"
    target = urlsplit(parse_qs(outer.query)["return_to"][0])
    assert target.path == "/audit" and parse_qs(target.query) == {"task": ["task-a"], "report": ["1"], "packageRequest": ["zip-request-a"]}
