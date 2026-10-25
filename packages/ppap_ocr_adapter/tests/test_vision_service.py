"""Multimodal page extraction keeps order, bounded concurrency and refusal safety."""

from threading import Barrier
from unittest.mock import patch

import fitz
import pytest
from ppap_ocr_adapter.doc_cache import vision_service


def _pdf(tmp_path, pages=2):
    path = tmp_path / "pages.pdf"
    with fitz.open() as document:
        for _ in range(pages):
            document.new_page()
        document.save(path)
    return str(path)


def test_page_requests_overlap_but_results_preserve_pdf_page_order(tmp_path):
    gate = Barrier(2, timeout=5)
    seen = []

    def read_page(image, page_number, page_count):
        assert image.startswith(b"\x89PNG")
        assert page_count == 2
        seen.append(page_number)
        gate.wait()
        return {"tables": [{"rows": [[str(page_number)]], "merges": []}], "paragraphs": [], "seals": []}

    with patch.object(vision_service, "_read_page", side_effect=read_page):
        pages = vision_service.VisionLayoutService().analyze_full_pdf(_pdf(tmp_path))
    assert sorted(seen) == [1, 2]
    assert [page["tables"][0]["rows"][0][0] for page in pages] == ["1", "2"]


@pytest.mark.parametrize(
    "payload",
    [
        {"tables": [], "paragraphs": [], "seals": ["ok"], "extra": 1},
        {"tables": [{"rows": [["ok", 12]], "merges": []}], "paragraphs": [], "seals": []},
        {"tables": "broken", "paragraphs": [], "seals": []},
    ],
)
def test_schema_deviations_fail_closed(payload):
    with pytest.raises(ValueError, match="Invalid Responses"):
        vision_service._validate_page(payload)


def test_reads_png_image_using_responses_schema(tmp_path):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return {"tables": [], "paragraphs": ["readable text"], "seals": []}

    with patch.object(vision_service, "call_llm", side_effect=create):
        results = vision_service.VisionLayoutService().analyze_full_pdf(_pdf(tmp_path, 1))
    assert results[0]["paragraphs"] == ["readable text"]
    assert calls[0]["json_mode"] is True
    assert calls[0]["retry_count"] == 1
    assert calls[0]["response_schema"]["name"] == "ppap_multimodal_pdf_page"


def test_multimodal_merge_rejects_fake_or_overlapping_evidence():
    valid = {
        "tables": [{"rows": [["外观"], [""], [""]], "merges": [{"start_row": 0, "start_col": 0, "end_row": 2, "end_col": 0}]}],
        "paragraphs": [],
        "seals": [],
    }
    assert vision_service._validate_page(valid) == valid
    overlapping = {
        **valid,
        "tables": [
            {
                "rows": [["外观"], [""], [""]],
                "merges": [
                    {"start_row": 0, "start_col": 0, "end_row": 2, "end_col": 0},
                    {"start_row": 1, "start_col": 0, "end_row": 2, "end_col": 0},
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="overlapping"):
        vision_service._validate_page(overlapping)
    invalid_cover = {
        **valid,
        "tables": [
            {
                "rows": [["外观"], ["另一个真实单元格"], [""]],
                "merges": [{"start_row": 0, "start_col": 0, "end_row": 2, "end_col": 0}],
            }
        ],
    }
    with pytest.raises(ValueError, match=r"nonempty evidence .*table=1, anchor=\(0,0\), cell=\(1,0\)"):
        vision_service._validate_page(invalid_cover)


def test_invalid_merge_diagnostic_identifies_pdf_page_without_exposing_cell_text():
    invalid = {
        "tables": [{"rows": [["private value"], ["other evidence"]], "merges": [{"start_row": 0, "start_col": 0, "end_row": 1, "end_col": 0}]}],
        "paragraphs": [],
        "seals": [],
    }
    with patch.object(vision_service, "call_llm", return_value=invalid):
        with pytest.raises(vision_service.ResponsesEvidenceError, match=r"Responses page 2/3: Invalid Responses merge covers nonempty evidence") as error:
            vision_service._read_page(b"image", 2, 3)
    assert error.value.code == "responses_invalid_schema"
    assert "private value" not in str(error.value)
    assert "other evidence" not in str(error.value)


def test_page_error_preserves_invalid_json_type_without_contents():
    failure = vision_service.ResponsesEvidenceError("responses_invalid_json", "Responses invalid JSON (output_chars=34)")
    with patch.object(vision_service, "call_llm", side_effect=failure):
        with pytest.raises(vision_service.ResponsesEvidenceError, match="Responses page 4/4") as error:
            vision_service._read_page(b"image", 4, 4)
    assert error.value.code == "responses_invalid_json"
