"""Page requests and source-bound partial evidence, without external services."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
import requests
from documents.ocr.paddle import PaddleOCRFormatError, PaddleStructureService, build_evidence, client


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "original.pdf"
    with fitz.open() as pdf:
        for text in ("PAGE ONE", "PAGE TWO", "PAGE THREE"):
            page = pdf.new_page(width=400, height=200)
            page.insert_text((20, 40), text)
        pdf.save(path)
    return path


def page_result() -> dict:
    return {
        "dataInfo": {"numPages": 1, "pages": [{"width": 800, "height": 400}]},
        "layoutParsingResults": [
            {
                "prunedResult": {
                    "layout_det_res": {"boxes": [{"label": "seal", "coordinate": [20, 40, 100, 120]}]},
                    "parsing_res_list": [{"block_label": "text", "block_content": "ARCHIVE", "block_bbox": [120, 40, 280, 80]}],
                    "seal_res_list": [{"rec_texts": ["ARCHIVE"], "bbox": [20, 40, 100, 120]}],
                    "table_res_list": [],
                }
            }
        ],
    }


def test_selected_page_sends_exactly_one_original_page_without_touching_source(source, monkeypatch):
    before = source.read_bytes()
    calls = []

    def post(endpoint, *, json, headers, timeout, allow_redirects):
        assert allow_redirects is False
        with fitz.open(stream=base64.b64decode(json["file"]), filetype="pdf") as selected:
            assert len(selected) == 1
            assert "PAGE THREE" in selected[0].get_text()
            assert "PAGE ONE" not in selected[0].get_text()
        calls.append((endpoint, timeout))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"result": page_result()})

    monkeypatch.setattr(client.requests, "post", post)
    actual = PaddleStructureService("https://ocr.example/layout-parsing", timeout=7).analyze_page(source, 3)
    assert actual == page_result()
    assert calls == [("https://ocr.example/layout-parsing", 7)]
    assert source.read_bytes() == before


@pytest.mark.parametrize("page", [0, -1, 4, True, 1.5])
def test_invalid_original_page_never_calls_service(source, monkeypatch, page):
    monkeypatch.setattr(client.requests, "post", lambda *_a, **_kw: pytest.fail("invalid page sent"))
    with pytest.raises(ValueError):
        PaddleStructureService("https://ocr.example/layout-parsing").analyze_page(source, page)


def test_page_timeout_is_one_attempt_and_does_not_claim_remote_cancellation(source, monkeypatch):
    calls = []

    def unavailable(*_a, **_kw):
        calls.append(True)
        raise requests.ReadTimeout("do not expose provider URL or body")

    monkeypatch.setattr(client.requests, "post", unavailable)
    with pytest.raises(PaddleOCRFormatError, match="ReadTimeout"):
        PaddleStructureService("https://ocr.example/layout-parsing").analyze_page(source, 2)
    assert calls == [True]


def test_page_request_uses_the_smaller_remaining_timeout(source, monkeypatch):
    timeouts = []

    def post(*_a, **kwargs):
        timeouts.append(kwargs["timeout"])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"result": page_result()})

    monkeypatch.setattr(client.requests, "post", post)
    service = PaddleStructureService("https://ocr.example/layout-parsing", timeout=7)
    service.analyze_page(source, 1, timeout=0.5)
    service.analyze_page(source, 1, timeout=10)
    assert timeouts == [0.5, 7]


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_invalid_remaining_timeout_never_sends_a_page(source, monkeypatch, timeout):
    monkeypatch.setattr(client.requests, "post", lambda *_a, **_kw: pytest.fail("invalid timeout sent"))
    with pytest.raises(ValueError, match="timeout"):
        PaddleStructureService("https://ocr.example/layout-parsing").analyze_page(source, 1, timeout=timeout)


def test_source_change_during_request_rejects_late_evidence(source, monkeypatch):
    def replace_source(*_a, **_kw):
        source.write_bytes(b"changed while provider was running")
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"result": page_result()})

    monkeypatch.setattr(client.requests, "post", replace_source)
    with pytest.raises(PaddleOCRFormatError, match="source changed"):
        PaddleStructureService("https://ocr.example/layout-parsing").analyze_page(source, 2)


def test_evidence_binds_original_page_hash_and_pixel_to_pdf_location(source):
    result = build_evidence(page_result(), page_count=1, pdf_path=source, source_pages=[3])
    assert result["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    page = result["pages"][0]
    assert page["page"] == 3
    assert page["frame"]["width"] == 800
    assert page["frame"]["rotation"] == 0
    assert page["seals"][0]["bbox"] == [20, 40, 100, 120]
    assert page["seals"][0]["pdf_bbox"] == [10, 20, 50, 60]
    assert page["seals"][0]["recognized_text"] == ["ARCHIVE"]
    assert page["texts"][0]["pdf_bbox"] == [60, 20, 140, 40]
    assert result["status"] == "complete"


def test_rotated_page_locations_return_to_unrotated_original_points(tmp_path):
    source = tmp_path / "rotated.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=400, height=200)
        page.set_rotation(90)
        pdf.save(source)
    response = page_result()
    response["dataInfo"]["pages"] = [{"width": 400, "height": 800}]
    pruned = response["layoutParsingResults"][0]["prunedResult"]
    pruned["layout_det_res"]["boxes"][0]["coordinate"] = [40, 80, 120, 160]
    pruned["seal_res_list"][0]["bbox"] = [40, 80, 120, 160]
    page = build_evidence(response, page_count=1, pdf_path=source, source_pages=[1])["pages"][0]
    assert page["frame"]["rotation"] == 90
    assert page["seals"][0]["pdf_bbox"] == [40, 140, 80, 180]


def test_missing_page_dimensions_cannot_supply_authoritative_original_location(source):
    response = page_result()
    response["dataInfo"]["pages"] = [{}]
    with pytest.raises(PaddleOCRFormatError, match="dimensions"):
        build_evidence(response, page_count=1, pdf_path=source, source_pages=[1])


def test_bad_table_retains_good_seal_and_text_and_reports_local_error(source):
    response = page_result()
    response["layoutParsingResults"][0]["prunedResult"]["table_res_list"] = [
        {"pred_html": "<table><tr><td colspan='2'"},
    ]
    actual = build_evidence(response, page_count=1, pdf_path=source, source_pages=[2])
    page = actual["pages"][0]
    assert actual["status"] == "partial"
    assert page["seals"][0]["recognized_text"] == ["ARCHIVE"]
    assert page["texts"][0]["text"] == "ARCHIVE"
    assert page["tables"] == []
    assert any(d["element"] == "table" and d["page"] == 2 for d in page["diagnostics"])


def test_bad_text_and_bad_seal_do_not_remove_good_elements(source):
    response = page_result()
    pruned = response["layoutParsingResults"][0]["prunedResult"]
    pruned["parsing_res_list"].insert(0, {"block_label": "text", "block_content": 42})
    pruned["seal_res_list"].append({"rec_texts": "not an array"})
    actual = build_evidence(response, page_count=1, pdf_path=source, source_pages=[1])
    page = actual["pages"][0]
    assert actual["status"] == "partial"
    assert len(page["texts"]) == 1
    assert page["texts"][0]["index"] == 1
    assert page["seals"][0]["recognized_text"] == ["ARCHIVE"]
    assert {d["element"] for d in page["diagnostics"]} >= {"text", "seal"}


def test_ambiguous_two_seals_cannot_copy_one_recognition_into_either(source):
    response = page_result()
    pruned = response["layoutParsingResults"][0]["prunedResult"]
    pruned["layout_det_res"]["boxes"] = [
        {"label": "seal", "coordinate": [20, 40, 100, 120]},
        {"label": "seal", "coordinate": [30, 40, 110, 120]},
    ]
    actual = build_evidence(response, page_count=1, pdf_path=source, source_pages=[1])
    seals = actual["pages"][0]["seals"]
    bound = [s for s in seals if s["region_index"] is not None]
    assert len(bound) == 2
    assert all(s["text_status"] == "unreadable" and s["recognized_text"] == [] for s in bound)
    assert any(s["recognized_text"] == ["ARCHIVE"] and s["region_index"] is None for s in seals)


def test_invalid_seal_geometry_does_not_become_a_bboxless_unique_match(source):
    response = page_result()
    response["layoutParsingResults"][0]["prunedResult"]["seal_res_list"][0]["bbox"] = [40, 40, 20, 20]
    actual = build_evidence(response, page_count=1, pdf_path=source, source_pages=[1])
    bound = [s for s in actual["pages"][0]["seals"] if s["region_index"] is not None]
    assert len(bound) == 1
    assert bound[0]["text_status"] == "unreadable"
    assert bound[0]["recognized_text"] == []


def test_out_of_bounds_location_is_unknown_not_clipped_to_original(source):
    response = page_result()
    response["layoutParsingResults"][0]["prunedResult"]["parsing_res_list"][0]["block_bbox"] = [700, 10, 900, 30]
    actual = build_evidence(response, page_count=1, pdf_path=source, source_pages=[1])
    assert actual["pages"][0]["texts"][0]["pdf_bbox"] is None
    assert actual["status"] == "partial"


def test_wrong_response_page_identity_is_not_guessed(source):
    response = page_result()
    response["layoutParsingResults"][0]["prunedResult"]["page_index"] = 4
    with pytest.raises(PaddleOCRFormatError, match="page identity"):
        build_evidence(response, page_count=1, pdf_path=source, source_pages=[3])
