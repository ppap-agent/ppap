"""Provider receipts are operational observations, not customer billing."""

from audit.usage import TokenTracker


def test_missing_provider_receipt_does_not_claim_a_zero_token_call():
    tracker = TokenTracker()
    request_id = tracker.start_request_tracking()
    tracker.record_unreported_usage()

    usage = tracker.end_request_tracking(request_id).to_dict()

    assert usage["llm_calls_count"] == 1
    assert usage["unreported_calls_count"] == 1
    assert usage["usage_complete"] is False
    assert usage["llm_calls"] == []
    assert [usage[key] for key in ("total_input_tokens", "total_output_tokens", "total_thinking_tokens", "total_tokens")] == [None] * 4


def test_partial_receipts_keep_known_call_details_without_claiming_a_complete_total():
    tracker = TokenTracker()
    request_id = tracker.start_request_tracking()
    tracker.record_token_usage(input_tokens=9, output_tokens=5, thinking_tokens=2, model_name="test", operation="image_analysis")
    tracker.record_unreported_usage()

    usage = tracker.end_request_tracking(request_id).to_dict()

    assert usage["llm_calls_count"] == 2
    assert usage["unreported_calls_count"] == 1
    assert usage["usage_complete"] is False
    assert usage["total_tokens"] is None
    assert len(usage["llm_calls"]) == 1
    assert usage["llm_calls"][0]["input_tokens"] == 9
    assert usage["llm_calls"][0]["output_tokens"] == 5
    assert usage["llm_calls"][0]["thinking_tokens"] == 2
    assert usage["llm_calls"][0]["total_tokens"] == 14


def test_all_reported_receipts_preserve_existing_totals():
    tracker = TokenTracker()
    request_id = tracker.start_request_tracking()
    tracker.record_token_usage(input_tokens=9, output_tokens=5, thinking_tokens=2)
    tracker.record_token_usage(input_tokens=4, output_tokens=3, thinking_tokens=0)

    usage = tracker.end_request_tracking(request_id).to_dict()

    assert usage["llm_calls_count"] == 2
    assert usage["unreported_calls_count"] == 0
    assert usage["usage_complete"] is True
    assert (usage["total_input_tokens"], usage["total_output_tokens"], usage["total_thinking_tokens"], usage["total_tokens"]) == (13, 8, 2, 21)


def test_optional_reasoning_is_unknown_without_discarding_known_input_and_output():
    tracker = TokenTracker()
    request_id = tracker.start_request_tracking()
    tracker.record_token_usage(input_tokens=9, output_tokens=5)

    usage = tracker.end_request_tracking(request_id).to_dict()

    assert usage["usage_complete"] is True
    assert usage["unreported_calls_count"] == 0
    assert (usage["total_input_tokens"], usage["total_output_tokens"], usage["total_tokens"]) == (9, 5, 14)
    assert usage["total_thinking_tokens"] is None
    assert usage["llm_calls"][0]["thinking_tokens"] is None
