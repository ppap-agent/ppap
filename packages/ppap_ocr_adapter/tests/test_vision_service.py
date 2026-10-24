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
        return {"tables": [{"rows": [[str(page_number)]]}], "paragraphs": [], "seals": []}

    with patch.object(vision_service, "_read_page", side_effect=read_page):
        pages = vision_service.VisionLayoutService().analyze_full_pdf(_pdf(tmp_path))
    assert sorted(seen) == [1, 2]
    assert [page["tables"][0]["rows"][0][0] for page in pages] == ["1", "2"]


@pytest.mark.parametrize(
    "payload",
    [{"tables": [], "paragraphs": [], "seals": ["ok"], "extra": 1},
     {"tables": [{"rows": [["ok", 12]]}], "paragraphs": [], "seals": []},
     {"tables": "broken", "paragraphs": [], "seals": []}],
)
def test_schema_deviations_fail_closed(payload):
    with pytest.raises(ValueError, match='Invalid Responses'):
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
