"""Two peer observations use the same inputs and cannot supply each other's facts."""

from unittest.mock import Mock

import pytest
from audit.engine.checkers.ocr_visual_facts import OcrImage, observe_ocr_images


@pytest.mark.parametrize("unknown_observation", [1, 2])
def test_observations_share_fields_and_criteria_without_sharing_answers(monkeypatch, unknown_observation):
    monkeypatch.setenv("OCR_MODEL_NAME", "controlled")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://controlled.invalid")
    frames = [OcrImage(b"synthetic", {"scope": "pdf:2", "page": 2, "context": True, "located_region": True}, 100, 100)]
    fields = {"psw_stamp_present": "actual seal", "psw_stamp_ending_text": "seal ending"}
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        values = {"psw_stamp_present": None if len(calls) == unknown_observation else True, "psw_stamp_ending_text": "研发专章"}
        return {field: values[field] for field in kwargs["response_schema"]["schema"]["properties"]}

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    cache, witness = {}, Mock()
    for _ in range(2):
        assert observe_ocr_images(frames, fields, witness, context="original context", request_cache=cache) == {"psw_stamp_present": None, "psw_stamp_ending_text": "研发专章"}
    assert len(calls) == 2
    assert calls[0]["images"] == calls[1]["images"] == [b"synthetic"]
    assert calls[0]["response_schema"] == calls[1]["response_schema"]
    assert set(calls[0]["response_schema"]["schema"]["properties"]) == set(fields)
    assert [call["prompt"].split("\n", 1)[0] for call in calls] == ["独立观察编号：1。", "独立观察编号：2。"]
    assert calls[0]["prompt"].split("\n", 1)[1] == calls[1]["prompt"].split("\n", 1)[1]
    assert all("复核" not in call["prompt"] and "研发专章" not in call["prompt"] for call in calls)
    assert {call.args[0]["fact"] for call in witness.call_args_list} == {"psw_stamp_ending_text"}


@pytest.mark.parametrize("second", [True, False])
def test_unknown_observation_is_still_read_twice_and_never_becomes_a_known_fact(monkeypatch, second):
    monkeypatch.setenv("OCR_MODEL_NAME", "controlled")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://controlled.invalid")
    frames = [OcrImage(b"synthetic", {"scope": "pdf:2", "page": 2, "context": True, "located_region": True}, 100, 100)]
    read = Mock(side_effect=[{"seal": None}, {"seal": second}])
    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    witness = Mock()
    assert observe_ocr_images(frames, {"seal": "actual seal"}, witness) == {"seal": None}
    assert read.call_count == 2
    witness.assert_not_called()
