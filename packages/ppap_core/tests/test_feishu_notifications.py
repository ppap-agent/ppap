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
