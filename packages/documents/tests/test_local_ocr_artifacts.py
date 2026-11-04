"""Global native fallback consumes located Paddle tables without model/cache reuse."""

from __future__ import annotations

import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import fitz
import httpx
import pytest
import requests
from documents.conversion import pipeline
from documents.ocr import local
from documents.ocr.paddle import reader
from documents.ocr.paddle.client import PaddleOCRFormatError
from openpyxl import Workbook, load_workbook


@pytest.fixture(autouse=True)
def no_external_requests(monkeypatch):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(1)
        raise AssertionError("Paddle conversion tests must stub the external service")

    async def forbidden_async(*args, **kwargs):
        return forbidden(*args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_async)
    monkeypatch.setattr(requests.Session, "send", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    yield
    assert attempts == []


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
    monkeypatch.setenv("PADDLE_EVIDENCE_ENABLED", "true")
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


@pytest.mark.parametrize("enabled", [None, "false"])
def test_reader_disabled_does_not_dispatch(tmp_path, selected_paddle, monkeypatch, enabled):
    source = make_pdf(tmp_path / "source.pdf")
    if enabled is None:
        monkeypatch.delenv("PADDLE_EVIDENCE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("PADDLE_EVIDENCE_ENABLED", enabled)
    with pytest.raises(reader.PaddleEvidenceUnavailable) as error:
        reader.read_pdf_page_evidence(source, 1)
    assert error.value.code == "paddle_evidence_disabled"
    assert selected_paddle[0] == []
    artifact = local.convert_local_pdf_to_excel(str(source))
    assert artifact.excel_path is None and artifact.raw_meta["error_code"] == "paddle_evidence_disabled"
    assert selected_paddle[0] == []


def test_reader_missing_endpoint_does_not_dispatch(tmp_path, selected_paddle, monkeypatch):
    source = make_pdf(tmp_path / "source.pdf")
    monkeypatch.delenv("PADDLE_OCR_URL", raising=False)
    with pytest.raises(reader.PaddleEvidenceUnavailable) as error:
        reader.read_pdf_page_evidence(source, 1)
    assert error.value.code == "paddle_evidence_unconfigured"
    assert selected_paddle[0] == []


def test_multipage_global_fallback_requires_explicit_page(tmp_path, selected_paddle):
    source = make_pdf(tmp_path / "29-pages.pdf", pages=29)
    artifact = local.convert_local_pdf_to_excel(str(source))
    assert artifact.excel_path is None
    assert artifact.raw_meta["error_code"] == "paddle_page_selection_required"
    assert selected_paddle[0] == []


def test_singlepage_paddle_workbooks_are_independent_without_old_llm_cache(tmp_path, selected_paddle, monkeypatch):
    source = make_pdf(tmp_path / "source.pdf")
    before = source.read_bytes()
    cache = tmp_path / "existing-cache.sqlite"
    cache.write_bytes(b"existing production cache")
    monkeypatch.setenv("OCR_LOCAL_SQLITE_DB", str(cache))
    monkeypatch.setenv("OCR_LOCAL_OSS_DIR", str(tmp_path / "old-cache"))
    artifacts = []
    try:
        for use_cache in (True, False):
            artifact = local.convert_local_pdf_to_excel(str(source), enable_cache=use_cache)
            artifacts.append(artifact)
            assert artifact.provider == "paddleocr_ppstructure_v3"
            assert artifact.excel_path is not None and artifact.html_path is None
            assert artifact.raw_meta["source_pages"] == [1]
            book = load_workbook(artifact.excel_path)
            try:
                assert "A1:B1" in {str(merge) for merge in book.active.merged_cells.ranges}
                assert book.active["A2"].value == "=2+2" and book.active["A2"].data_type == "s"
                assert book.active["B2"].value == "A&B"
                book.active["A2"] = "caller edit"
                book.save(artifact.excel_path)
            finally:
                book.close()
        assert len({item.excel_path for item in artifacts}) == 2
        assert len(selected_paddle[0]) == 2
        assert cache.read_bytes() == b"existing production cache"
        assert not (tmp_path / "old-cache").exists()
        assert source.read_bytes() == before
    finally:
        for artifact in artifacts:
            if artifact.excel_path:
                Path(artifact.excel_path).unlink(missing_ok=True)


@pytest.mark.parametrize("content", ["empty", "unlocated", "malformed"])
def test_incomplete_table_never_becomes_complete_workbook(tmp_path, selected_paddle, content):
    source = make_pdf(tmp_path / "source.pdf")
    response = selected_paddle[1]
    pruned = response["layoutParsingResults"][0]["prunedResult"]
    if content == "empty":
        pruned["layout_det_res"]["boxes"] = []
        pruned["table_res_list"] = []
    elif content == "unlocated":
        pruned["layout_det_res"]["boxes"] = []
    else:
        pruned["table_res_list"][0]["pred_html"] = "<table><tr><td>truncated"
    artifact = local.convert_local_pdf_to_excel(str(source))
    assert artifact.excel_path is None
    assert artifact.raw_meta["error_code"] in {"paddle_table_unconfirmed", "paddle_table_incomplete"}
    assert len(selected_paddle[0]) == 1


def test_transport_failure_stays_technical_without_retry_or_model_fallback(tmp_path, selected_paddle, monkeypatch):
    source = make_pdf(tmp_path / "source.pdf")
    calls = []

    def fail(self, *args, **kwargs):
        calls.append(1)
        raise PaddleOCRFormatError("controlled transport failure private-source")

    monkeypatch.setattr(reader.PaddleStructureService, "analyze_page", fail)
    result, error = pipeline._attempt_ocr(str(source), enable_cache=False)
    assert result is None
    assert error["error_code"] == "paddle_extraction_failed"
    assert "private-source" not in error["error"]
    assert calls == [1]


def test_good_table_beside_bad_table_is_not_exported_as_complete_workbook(tmp_path, selected_paddle):
    source = make_pdf(tmp_path / "source.pdf")
    pruned = selected_paddle[1]["layoutParsingResults"][0]["prunedResult"]
    pruned["layout_det_res"]["boxes"].append({"label": "table", "coordinate": [20, 500, 570, 750]})
    pruned["table_res_list"][0]["cell_box_list"] = [[20, 20, 570, 350]]
    pruned["table_res_list"].append({"pred_html": "<table><tr><td>truncated", "cell_box_list": [[20, 500, 570, 750]]})
    artifact = local.convert_local_pdf_to_excel(str(source))
    assert artifact.excel_path is None
    assert artifact.raw_meta["error_code"] == "paddle_table_incomplete"
    assert selected_paddle[0] == [(source, 1)]


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


def test_concurrent_conversions_publish_separate_workbooks(tmp_path, selected_paddle):
    source = make_pdf(tmp_path / "source.pdf")
    artifacts = []
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            artifacts = list(executor.map(lambda _: local.convert_local_pdf_to_excel(str(source)), range(2)))
        assert all(item.excel_path for item in artifacts)
        assert len({item.excel_path for item in artifacts}) == 2
        for artifact in artifacts:
            book = load_workbook(artifact.excel_path)
            book.close()
    finally:
        for artifact in artifacts:
            if artifact.excel_path:
                Path(artifact.excel_path).unlink(missing_ok=True)


def test_native_success_never_dispatches_paddle(tmp_path, selected_paddle, monkeypatch):
    source = make_pdf(tmp_path / "source.pdf", pages=29)

    def native(path, output_path, *, spec):
        book = Workbook()
        book.active.append(["native", "data"])
        book.save(output_path)
        book.close()
        return None, {"native": True}

    monkeypatch.setattr(pipeline, "_attempt_manual_linegrid", native)
    artifact = pipeline.convert_pdf_to_excel_auto(str(source))
    try:
        assert artifact.success and artifact.strategy_used == "manual_linegrid"
        assert selected_paddle[0] == []
    finally:
        for path in artifact.temp_files:
            Path(path).unlink(missing_ok=True)


def test_task_scope_reuses_converter_page_for_rule_and_protects_cached_evidence(tmp_path, selected_paddle):
    source = make_pdf(tmp_path / "source.pdf")
    artifact = None
    try:
        with reader.paddle_page_scope():
            artifact = local.convert_local_pdf_to_excel(str(source))
            assert artifact.excel_path is not None
            first = reader.read_pdf_page_evidence(source, 1)
            first["diagnostics"].append({"code": "caller-only"})
            first["pages"][0]["tables"].clear()
            second = reader.read_pdf_page_evidence(source, 1)
            assert second["pages"][0]["tables"]
            assert second["diagnostics"] == []
        assert selected_paddle[0] == [(source, 1)]
    finally:
        if artifact and artifact.excel_path:
            Path(artifact.excel_path).unlink(missing_ok=True)


def test_task_scope_reuses_failed_converter_request_without_resending(tmp_path, selected_paddle, monkeypatch):
    source = make_pdf(tmp_path / "source.pdf")
    calls = []

    def fail(self, *args, **kwargs):
        calls.append(1)
        raise PaddleOCRFormatError("controlled transport failure")

    monkeypatch.setattr(reader.PaddleStructureService, "analyze_page", fail)
    with reader.paddle_page_scope():
        artifact = local.convert_local_pdf_to_excel(str(source))
        assert artifact.excel_path is None
        with pytest.raises(PaddleOCRFormatError, match="controlled transport failure"):
            reader.read_pdf_page_evidence(source, 1)
    assert calls == [1]


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
