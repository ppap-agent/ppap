"""Two peer observations use the same inputs and cannot supply each other's facts."""

from unittest.mock import Mock

import pytest
from audit.engine.checkers.ocr_visual_facts import OcrImage, observe_ocr_images
from documents.ocr.observation import current_ocr_observation


@pytest.mark.parametrize("unknown_observation", [1, 2])
def test_observations_share_fields_and_criteria_without_sharing_answers(monkeypatch, unknown_observation):
    monkeypatch.setenv("PPAP_MODEL_VISION_MODEL", "controlled")
    monkeypatch.setenv("PPAP_MODEL_VISION_API_KEY", "controlled")
    monkeypatch.setenv("PPAP_MODEL_VISION_BASE_URL", "https://controlled.invalid")
    frames = [OcrImage(b"synthetic", {"scope": "pdf:2", "page": 2, "context": True, "located_region": True}, 100, 100)]
    fields = {"psw_stamp_present": "actual seal", "psw_stamp_ending_text": "seal ending"}
    calls = []
    numbers = []

    def read(**kwargs):
        calls.append(kwargs)
        numbers.append(current_ocr_observation())
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
    assert numbers == [1, 2]
    assert current_ocr_observation() is None
    assert calls[0]["prompt"] == calls[1]["prompt"]
    assert all("复核" not in call["prompt"] and "研发专章" not in call["prompt"] for call in calls)
    assert {call.args[0]["fact"] for call in witness.call_args_list} == {"psw_stamp_ending_text"}


@pytest.mark.parametrize("second", [True, False])
def test_unknown_observation_is_still_read_twice_and_never_becomes_a_known_fact(monkeypatch, second):
    monkeypatch.setenv("PPAP_MODEL_VISION_MODEL", "controlled")
    monkeypatch.setenv("PPAP_MODEL_VISION_API_KEY", "controlled")
    monkeypatch.setenv("PPAP_MODEL_VISION_BASE_URL", "https://controlled.invalid")
    frames = [OcrImage(b"synthetic", {"scope": "pdf:2", "page": 2, "context": True, "located_region": True}, 100, 100)]
    read = Mock(side_effect=[{"seal": None}, {"seal": second}])
    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    witness = Mock()
    assert observe_ocr_images(frames, {"seal": "actual seal"}, witness) == {"seal": None}
    assert read.call_count == 2
    witness.assert_not_called()


def test_peer_model_inputs_are_byte_identical(monkeypatch):
    monkeypatch.setenv("PPAP_MODEL_VISION_MODEL", "controlled")
    monkeypatch.setenv("PPAP_MODEL_VISION_API_KEY", "controlled")
    monkeypatch.setenv("PPAP_MODEL_VISION_BASE_URL", "https://controlled.invalid")
    frames = [OcrImage(b"synthetic", {"scope": "pdf:2", "page": 2, "context": True, "located_region": True}, 100, 100)]
    read = Mock(return_value={"seal": True})
    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    assert observe_ocr_images(frames, {"seal": "actual seal"}, Mock()) == {"seal": True}
    assert read.call_count == 2
    assert read.call_args_list[0] == read.call_args_list[1]


@pytest.mark.parametrize("second", [True, False, None])
def test_both_source_sets_follow_the_same_consensus(monkeypatch, second):
    monkeypatch.setenv("PPAP_MODEL_VISION_MODEL", "controlled")
    monkeypatch.setenv("PPAP_MODEL_VISION_API_KEY", "controlled")
    monkeypatch.setenv("PPAP_MODEL_VISION_BASE_URL", "https://controlled.invalid")
    frames = [OcrImage(b"synthetic", {"scope": "pdf:2", "page": 2, "context": True}, 100, 100)]
    bounds = [[0.05, 0.1, 0.45, 0.9], [0.55, 0.1, 0.95, 0.9]]
    calls = []

    def read(**kwargs):
        box = bounds[len(calls)]
        value = True if not calls else second
        calls.append(kwargs)
        return {
            "seal": [
                {
                    "scope": "pdf:2",
                    "value": value,
                    "sources": [{"image_index": 0, "bbox": box}] if value is not None else [],
                    "covered_image_indices": [0] if value is not None else [],
                    "coverage": "complete" if value is not None else "unreadable",
                    "excerpt": "synthetic seal",
                }
            ]
        }

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    witness = Mock()
    cache = {}
    expected = {"seal": True if second is True else None}
    assert observe_ocr_images(frames, {"seal": "actual seal"}, witness, request_cache=cache) == expected
    assert observe_ocr_images(frames, {"seal": "actual seal"}, Mock(), request_cache=cache) == expected
    assert len(calls) == 2
    if second is True:
        assert witness.call_count == 2
        for call, box in zip(witness.call_args_list, [[5, 10, 45, 90], [55, 10, 95, 90]], strict=True):
            assert call.args[0]["bbox"] == pytest.approx(box)
    else:
        witness.assert_not_called()


@pytest.mark.asyncio
async def test_observation_identity_reaches_the_threaded_async_transport(monkeypatch):
    monkeypatch.setenv("PPAP_MODEL_VISION_MODEL", "controlled")
    monkeypatch.setenv("PPAP_MODEL_VISION_API_KEY", "controlled")
    monkeypatch.setenv("PPAP_MODEL_VISION_BASE_URL", "https://controlled.invalid")
    numbers = []

    async def read(*args, **kwargs):
        numbers.append(current_ocr_observation())
        return {"seal": True}

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.read_visual_facts", read)
    frames = [OcrImage(b"synthetic", {"scope": "pdf:2", "page": 2, "context": True, "located_region": True}, 100, 100)]
    assert observe_ocr_images(frames, {"seal": "actual seal"}, Mock()) == {"seal": True}
    assert numbers == [1, 2]
    assert current_ocr_observation() is None
