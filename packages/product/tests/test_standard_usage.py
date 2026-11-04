"""Real Standard execution keeps SDK receipts separate from customer charges."""

import json
from io import BytesIO

import httpx
import pytest
from audit.audits import RoundInput, RoundWork
from audit.engine.checkers.client import AIClient
from audit.usage import get_current_request_id, get_token_tracker
from docx import Document
from PIL import Image
from product import run_round_from_bytes
from product.settings import get_settings


def psw_input():
    document = Document()
    table = document.add_table(rows=6, cols=2)
    for row, (label, value) in enumerate(
        (("零件提交保证书", "PSW"), ("重量", "1.2"), ("供应商名称/供应商代码", "测试公司/S01"), ("提交原因", "☒ 首次提交"), ("认可情况", "☒ 批准"), ("经授权的供方代表签字", "代表"))
    ):
        table.cell(row, 0).text, table.cell(row, 1).text = label, value
    image_bytes = BytesIO()
    with Image.new("RGB", (8, 8), "red") as image:
        image.save(image_bytes, format="PNG")
    image_bytes.seek(0)
    table.cell(5, 1).paragraphs[0].add_run().add_picture(image_bytes)
    content = BytesIO()
    document.save(content)
    rules = [{"id": "stamp", "rule_to_check": "核对盖章"}, {"id": "signature", "rule_to_check": "核对签字与盖章"}]
    target = RoundInput(
        "target",
        "file-psw",
        "version-psw",
        "PSW.docx",
        "ready",
        rule_version_id="confirmed-psw",
        rule_body={"kind": "engine", "type": "零件提交保证书", "rule_profile": "v3", "rules": rules},
        rule_execution={"mode": "rule_items", "items": [{**item, "coverage": "mapped", "checker_index": index} for item, index in zip(rules, (2, 4), strict=True)]},
    )
    return RoundWork("round-usage", "tenant", [target]), {target.file_version_id: content.getvalue()}


@pytest.fixture
def provider(request, monkeypatch):
    calls = []
    receipt = getattr(request, "param", {"input_tokens": 9, "output_tokens": 5, "total_tokens": 14, "output_tokens_details": {"reasoning_tokens": 2}})

    def send(_client, request, **_kwargs):
        calls.append(request)
        payload = {
            "psw_stamp_ends_with_company": True,
            "authorized_signature_present": False,
            "psw_stamp_present": True,
            "classification": "视觉",
            "item": "签字与印章",
            "priority": "P0",
            "status": "NG",
            "suggestions": "模型返回的不符合结论",
        }
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "response-test",
                "object": "response",
                "created_at": 0,
                "model": "usage-test",
                "status": "completed",
                "output": [{"type": "message", "id": "message-test", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": json.dumps(payload), "annotations": []}]}],
                "usage": receipt,
            },
        )

    monkeypatch.setenv("LLM_API_KEY", "test-only")
    monkeypatch.setenv("LLM_MODEL", "usage-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.invalid/v1")
    monkeypatch.setenv("DEVELOPMENT", "false")

    async def send_async(client, request, **kwargs):
        return send(client, request, **kwargs)

    monkeypatch.setattr(AIClient, "_instance", None)
    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setattr(httpx.AsyncClient, "send", send_async)
    get_settings.cache_clear()
    try:
        yield calls
    finally:
        instance = AIClient._instance
        if instance is not None:
            instance.client.close()
        get_settings.cache_clear()


def test_standard_psw_round_makes_no_model_calls_and_releases_the_tracking_scope(provider):
    work, blobs = psw_input()
    previous = get_current_request_id()

    result = run_round_from_bytes(work, blobs)

    assert provider == []
    assert [row["status"] for row in result["findings"]] == ["UNABLE", "UNABLE"]
    usage = result["usage"]
    assert (usage["total_input_tokens"], usage["total_output_tokens"], usage["total_thinking_tokens"], usage["total_tokens"]) == (0, 0, 0, 0)
    assert usage["llm_calls_count"] == 0
    assert usage["usage_complete"] is True
    assert usage["unreported_calls_count"] == 0
    assert usage["llm_calls"] == []
    assert get_current_request_id() == previous
    assert get_token_tracker().get_request_summary(usage["request_id"]) is None
    assert "模型返回的不符合结论" not in json.dumps(usage, ensure_ascii=False)
    assert "test-only" not in json.dumps(usage)

    second = run_round_from_bytes(work, blobs)
    assert provider == []
    assert [row["status"] for row in second["findings"]] == ["UNABLE", "UNABLE"]
    assert second["usage"]["request_id"] != usage["request_id"]
    assert second["usage"]["total_tokens"] == 0
    assert second["usage"]["llm_calls_count"] == 0
    assert get_current_request_id() == previous
    assert get_token_tracker().get_request_summary(second["usage"]["request_id"]) is None


@pytest.mark.parametrize(
    ("provider", "complete"),
    [({"input_tokens": 9, "output_tokens": 5, "total_tokens": 14, "output_tokens_details": {"reasoning_tokens": 2}}, True), (None, False)],
    indirect=["provider"],
    ids=["visible-receipts", "missing-receipts"],
)
def test_text_model_usage_keeps_visible_receipts_and_unknown_usage_separate(provider, complete):
    # Text calls remain supported. Exercise the SDK boundary without restoring
    # removed PSW image calls just to generate a receipt.
    tracker = get_token_tracker()
    previous = get_current_request_id()
    request_id = tracker.start_request_tracking()
    try:
        for _ in range(2):
            AIClient().structured_completion(system_prompt="Return JSON", user_prompt="Test receipt", operation="text_receipt_test")
        usage = tracker.end_request_tracking(request_id).to_dict()
        assert len(provider) == usage["llm_calls_count"] == 2
        assert usage["usage_complete"] is complete
        if complete:
            assert (usage["total_input_tokens"], usage["total_output_tokens"], usage["total_thinking_tokens"], usage["total_tokens"]) == (18, 10, 4, 28)
            assert usage["unreported_calls_count"] == 0
            assert len(usage["llm_calls"]) == 2
            assert all(call["operation"] == "text_receipt_test" for call in usage["llm_calls"])
        else:
            assert usage["unreported_calls_count"] == 2
            assert usage["total_tokens"] is None
            assert usage["llm_calls"] == []
        assert "模型返回的不符合结论" not in json.dumps(usage, ensure_ascii=False)
        assert "test-only" not in json.dumps(usage)
    finally:
        tracker.cleanup_request(request_id)
        tracker.set_current_request_id(previous)
    assert get_current_request_id() == previous
    assert tracker.get_request_summary(request_id) is None


def test_failed_standard_input_restores_an_existing_tracking_scope():
    tracker = get_token_tracker()
    previous = tracker.get_current_request_id()
    outer = tracker.start_request_tracking()
    try:
        tracker.record_token_usage(input_tokens=2, output_tokens=3)
        with pytest.raises(ValueError, match="target_required"):
            run_round_from_bytes(RoundWork("invalid-round", "tenant", []), {})
        assert get_current_request_id() == outer
        tracker.record_token_usage(input_tokens=1, output_tokens=1)
        assert tracker.end_request_tracking(outer).total_tokens == 7
    finally:
        tracker.cleanup_request(outer)
        tracker.set_current_request_id(previous)
