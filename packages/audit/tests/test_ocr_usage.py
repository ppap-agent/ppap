"""Actual Responses receipts join the audit that owns the cross-thread OCR call."""

import asyncio
from types import SimpleNamespace

import pytest
from audit.engine.checkers.ocr_visual_facts import request_ocr_visual_facts
from audit.usage import get_token_tracker, track_ocr_usage
from documents.ocr import client


@pytest.mark.parametrize("failure", [None, "invalid_json", "transport"])
def test_ocr_usage_survives_async_thread_bridge_and_invalid_output(monkeypatch, failure):
    monkeypatch.setenv("OCR_MODEL_NAME", "configured-ocr")
    monkeypatch.delenv("OCR_OPENAI_EXTRA_BODY_JSON", raising=False)
    calls = []
    response = SimpleNamespace(
        model="configured-ocr",
        status="completed",
        output_text="broken" if failure == "invalid_json" else '{"read":true}',
        usage=SimpleNamespace(input_tokens=11, output_tokens=7, output_tokens_details=SimpleNamespace(reasoning_tokens=3), cost=0.002),
    )

    class SDK:
        responses = None

        def __init__(self):
            self.responses = self

        async def create(self, **kwargs):
            calls.append(kwargs)
            if failure == "transport":
                raise ConnectionError("controlled transport failure")
            return response

        async def close(self):
            pass

    monkeypatch.setattr(client, "_get_llm_client", SDK)
    tracker = get_token_tracker()
    previous = tracker.get_current_request_id()
    request_id = tracker.start_request_tracking()

    async def run_inside_event_loop():
        # The synchronous evidence bridge must run its async transport in a new thread.
        return request_ocr_visual_facts(images=[b"controlled-image"], prompt="Read", response_schema={"name": "read", "strict": True, "schema": {"type": "object"}})

    try:
        with track_ocr_usage():
            if failure:
                with pytest.raises((ConnectionError, client.ResponsesEvidenceError)):
                    asyncio.run(run_inside_event_loop())
            else:
                assert asyncio.run(run_inside_event_loop()) == {"read": True}
        summary = tracker.end_request_tracking(request_id).to_dict()
    finally:
        tracker.cleanup_request(request_id)
        tracker.set_current_request_id(previous)
    assert len(calls) == summary["llm_calls_count"] == 1
    if failure == "transport":
        assert summary["unreported_calls_count"] == 1
        assert summary["total_tokens"] is None
        assert summary["usage_complete"] is False
    else:
        assert summary["total_tokens"] == 18
        assert summary["total_thinking_tokens"] == 3
        assert summary["usage_complete"] is True
        receipt = summary["llm_calls"][0]
        assert receipt["model_name"] == "configured-ocr"
        assert receipt["provider_cost_usd"] == 0.002
        assert receipt["elapsed_seconds"] >= 0
