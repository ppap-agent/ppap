"""Direct vision call usage joins the audit that owns the cross-thread request."""

import asyncio

import pytest
from audit.engine.checkers.ocr_visual_facts import request_ocr_visual_facts
from audit.usage import get_token_tracker, track_model_usage
from documents.ai import AIError, AIResult


@pytest.mark.parametrize("failure", [None, "missing_usage", "transport"])
def test_model_usage_survives_async_thread_bridge_and_unknown_usage(monkeypatch, failure):
    monkeypatch.setenv("OCR_MODEL_NAME", "configured-ocr")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://controlled.invalid/v1")
    calls = []

    async def run(access, **kwargs):
        calls.append(kwargs)
        if failure == "transport":
            raise ConnectionError("controlled transport failure")
        if failure == "missing_usage":
            return AIResult({"read": True}, model=access.model)
        return AIResult({"read": True}, 11, 7, access.model)

    monkeypatch.setattr("documents.ai._run", run)
    tracker = get_token_tracker()
    previous = tracker.get_current_request_id()
    request_id = tracker.start_request_tracking()

    async def run_inside_event_loop():
        # The synchronous evidence bridge must run its async call in a new thread.
        return request_ocr_visual_facts(images=[b"controlled-image"], prompt="Read", response_schema={"name": "read", "strict": True, "schema": {"type": "object"}})

    try:
        with track_model_usage():
            if failure == "transport":
                with pytest.raises(AIError, match="request_failed"):
                    asyncio.run(run_inside_event_loop())
            else:
                assert asyncio.run(run_inside_event_loop()) == {"read": True}
        summary = tracker.end_request_tracking(request_id).to_dict()
    finally:
        tracker.cleanup_request(request_id)
        tracker.set_current_request_id(previous)
    assert len(calls) == 1 and calls[0]["purpose"] == "vision" and calls[0]["images"] == [b"controlled-image"]
    assert len(summary["llm_calls"]) + summary["unreported_calls_count"] == 1
    if failure:
        # Unknown usage is reported as unknown, never as zero.
        assert summary["unreported_calls_count"] == 1
        assert summary["total_tokens"] is None
        assert summary["usage_complete"] is False
    else:
        assert summary["total_tokens"] == 18
        assert summary["usage_complete"] is True
        receipt = summary["llm_calls"][0]
        assert receipt["model_name"] == "configured-ocr"
        assert receipt["operation"] == "ocr_evidence"
        assert receipt["elapsed_seconds"] >= 0
