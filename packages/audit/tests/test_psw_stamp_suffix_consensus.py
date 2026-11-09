"""Shared OCR consensus uses the visible rule-relevant seal suffix, not a vote.

Controlled observations reproduce the failure class. CI's failed raw provider
answers were not retained, so these cases do not replay that unavailable text.
"""

from unittest.mock import Mock

import pytest
from audit.engine.checkers.ocr_visual_facts import OcrImage, observe_ocr_images
from audit.engine.checkers.psw_visual_evidence import evaluate_psw_observation
from documents.ocr.client import current_ocr_observation


@pytest.fixture(autouse=True)
def no_live_provider(monkeypatch):
    monkeypatch.setenv("OCR_MODEL_NAME", "controlled-ocr")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled-noncredential")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://ocr.invalid/v1")
    monkeypatch.setattr("documents.ocr.client.AsyncOpenAI", lambda **kwargs: pytest.fail("This regression must not call a live provider"))


def read_pair(monkeypatch, first, second, *, source=True):
    readings = (first, second)

    async def read(_images, _prompt, *, response_schema, **_options):
        values = readings[current_ocr_observation() - 1]
        return {
            field: [
                {
                    "scope": "pdf:2",
                    "value": values[field],
                    "sources": [{"image_index": 0, "bbox": [0, 0, 1, 1]}] if source else [],
                    "covered_image_indices": [0],
                    "coverage": "complete",
                    "excerpt": "原件末端",
                }
            ]
            for field in response_schema["schema"]["properties"]
        }

    monkeypatch.setattr("documents.ocr.client.call_llm_async", read)
    image = OcrImage(b"controlled-image", {"scope": "pdf:2", "page": 2, "bbox": [10, 20, 110, 220], "context": True}, 100, 200)
    witness = Mock()
    facts = observe_ocr_images([image], {"psw_stamp_present": "实际印章", "psw_stamp_ending_text": "末端2至6个汉字"}, witness)
    return facts, witness


@pytest.mark.parametrize(
    ("first", "second", "expected", "suffix"),
    [
        ("部门专用章", "专用章", "NG", "用章"),
        ("有限公司", "公司", "PASS", "公司"),
        ("公司。", "公司", "PASS", "公司"),
        ("公司", "专用章", "UNABLE", None),
        ("专用章", "部门章", "UNABLE", None),
        (None, "专用章", "UNABLE", None),
    ],
)
def test_rule3_uses_agreeing_literal_suffix_and_keeps_genuine_conflicts_unknown(monkeypatch, first, second, expected, suffix):
    facts, witness = read_pair(monkeypatch, {"psw_stamp_present": True, "psw_stamp_ending_text": first}, {"psw_stamp_present": True, "psw_stamp_ending_text": second})
    facts.update(authorization_table_present=True, authorization_company_stamp_present=False)
    assert evaluate_psw_observation(3, facts).status == expected
    assert facts["psw_stamp_ending_text"] == suffix
    if suffix is not None:
        values = [call.args[0]["observed_value"] for call in witness.call_args_list if call.args[0]["fact"] == "psw_stamp_ending_text"]
        assert values == [first, second]
        assert all(call.args[0]["page"] == 2 for call in witness.call_args_list)


@pytest.mark.parametrize("present", [None, False])
def test_conflicting_or_unknown_stamp_presence_cannot_establish_a_company_suffix(monkeypatch, present):
    facts, _witness = read_pair(monkeypatch, {"psw_stamp_present": True, "psw_stamp_ending_text": "有限公司"}, {"psw_stamp_present": present, "psw_stamp_ending_text": "公司"})
    facts.update(authorization_table_present=True, authorization_company_stamp_present=False)
    assert evaluate_psw_observation(3, facts).status == "UNABLE"


@pytest.mark.parametrize("value", ["公司?", "专用章ABC", "司", "示例有限公司部门专用章"])
def test_malformed_endings_cannot_be_sliced_into_confirmed_suffixes(monkeypatch, value):
    with pytest.raises(ValueError):
        read_pair(monkeypatch, {"psw_stamp_present": True, "psw_stamp_ending_text": value}, {"psw_stamp_present": True, "psw_stamp_ending_text": value})


def test_missing_original_sources_cannot_establish_the_suffix(monkeypatch):
    with pytest.raises(ValueError):
        read_pair(monkeypatch, {"psw_stamp_present": True, "psw_stamp_ending_text": "有限公司"}, {"psw_stamp_present": True, "psw_stamp_ending_text": "公司"}, source=False)
