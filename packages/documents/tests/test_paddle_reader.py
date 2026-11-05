"""Selected-page Paddle evidence preserves source identity and request ownership."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import fitz
import pytest
from documents.ocr.paddle import reader
from documents.ocr.paddle.client import PaddleOCRFormatError


def make_pdf(path: Path, *, pages=1) -> Path:
    with fitz.open() as document:
        for _ in range(pages):
            document.new_page()
        document.save(path)
    return path


def paddle_response(*, table_html=None):
    return {
        "dataInfo": {"pages": [{"width": 595, "height": 842}]},
        "layoutParsingResults": [
            {
                "prunedResult": {
                    "layout_det_res": {"boxes": [{"label": "table", "coordinate": [20, 20, 570, 350]}]},
                    "table_res_list": [{"pred_html": table_html or '<table><tr><th colspan="2">标题</th></tr><tr><td>=2+2</td><td>A&amp;B</td></tr></table>'}],
                    "parsing_res_list": [{"block_label": "text", "block_content": "原件段落", "block_bbox": [20, 400, 150, 440]}],
                    "seal_res_list": [],
                }
            }
        ],
    }


@pytest.fixture
def selected_paddle(monkeypatch):
    monkeypatch.setenv("PADDLE_OCR_URL", "https://paddle.example.test/layout-parsing")
    calls = []
    response = paddle_response()

    def analyze(self, file_path, page_number, **kwargs):
        assert self.timeout == 20
        calls.append((Path(file_path), page_number))
        return deepcopy(response)

    monkeypatch.setattr(reader.PaddleStructureService, "analyze_page", analyze)
    return calls, response


def test_reader_maps_only_selected_original_page_and_source(tmp_path, selected_paddle):
    source = make_pdf(tmp_path / "source.pdf", pages=3)
    before = source.read_bytes()
    evidence = reader.read_pdf_page_evidence(source, 2)
    assert [page["page"] for page in evidence["pages"]] == [2]
    assert evidence["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert evidence["pages"][0]["tables"][0]["pdf_bbox"] == [20, 20, 570, 350]
    assert selected_paddle[0] == [(source, 2)]
    assert source.read_bytes() == before


def test_reader_missing_endpoint_does_not_dispatch(tmp_path, selected_paddle, monkeypatch):
    source = make_pdf(tmp_path / "source.pdf")
    monkeypatch.delenv("PADDLE_OCR_URL", raising=False)
    with pytest.raises(reader.PaddleEvidenceUnavailable) as error:
        reader.read_pdf_page_evidence(source, 1)
    assert error.value.code == "paddle_evidence_unconfigured"
    assert selected_paddle[0] == []


def test_source_change_during_request_is_rejected(tmp_path, selected_paddle, monkeypatch):
    source = make_pdf(tmp_path / "source.pdf")

    def mutate(self, path, page_number, **kwargs):
        with fitz.open(path) as document:
            document[0].insert_text((20, 20), "changed")
            document.saveIncr()
        return deepcopy(selected_paddle[1])

    monkeypatch.setattr(reader.PaddleStructureService, "analyze_page", mutate)
    with pytest.raises(PaddleOCRFormatError, match="source changed"):
        reader.read_pdf_page_evidence(source, 1)


def test_task_scope_bounds_request_count_and_shares_nested_scope(tmp_path, selected_paddle):
    source = make_pdf(tmp_path / "source.pdf", pages=3)
    with reader.paddle_page_scope(max_requests=2):
        reader.read_pdf_page_evidence(source, 1)
        with reader.paddle_page_scope(max_requests=10):
            reader.read_pdf_page_evidence(source, 2)
            with pytest.raises(reader.PaddleEvidenceUnavailable) as error:
                reader.read_pdf_page_evidence(source, 3)
        assert error.value.code == "paddle_request_budget_exhausted"
        reader.read_pdf_page_evidence(source, 1)  # Reuse remains allowed at the limit.
    assert selected_paddle[0] == [(source, 1), (source, 2)]


def test_separate_audit_scopes_never_share_page_results(tmp_path, selected_paddle):
    source = make_pdf(tmp_path / "source.pdf")
    for _ in range(2):
        with reader.paddle_page_scope():
            reader.read_pdf_page_evidence(source, 1)
    assert selected_paddle[0] == [(source, 1), (source, 1)]


@pytest.mark.parametrize("change", ["source", "endpoint", "token"])
def test_task_cache_does_not_cross_source_or_service_identity(tmp_path, selected_paddle, monkeypatch, change):
    source = make_pdf(tmp_path / "source.pdf")
    with reader.paddle_page_scope():
        first = reader.read_pdf_page_evidence(source, 1)
        if change == "source":
            with fitz.open(source) as document:
                document[0].insert_text((20, 20), "new version")
                document.saveIncr()
        elif change == "endpoint":
            monkeypatch.setenv("PADDLE_OCR_URL", "https://other-paddle.example.test/layout-parsing")
        else:
            monkeypatch.setenv("PADDLE_TOKEN", "changed-fixture-token")
        second = reader.read_pdf_page_evidence(source, 1)
    assert selected_paddle[0] == [(source, 1), (source, 1)]
    if change == "source":
        assert first["source_sha256"] != second["source_sha256"]


def test_copied_context_cannot_dispatch_after_audit_scope_exits(tmp_path, selected_paddle):
    from contextvars import copy_context

    source = make_pdf(tmp_path / "source.pdf")
    with reader.paddle_page_scope():
        copied = copy_context()
    with pytest.raises(reader.PaddleEvidenceUnavailable) as error:
        copied.run(reader.read_pdf_page_evidence, source, 1)
    assert error.value.code == "paddle_scope_closed"
    assert selected_paddle[0] == []
