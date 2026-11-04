"""Page requests and source-bound partial evidence, without external services."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import fitz
import httpx
import pytest
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


@pytest.fixture
def http_boundary(monkeypatch):
    real_client = httpx.AsyncClient

    def install(handler):
        def transport(**kwargs):
            assert kwargs["follow_redirects"] is False
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(client.httpx, "AsyncClient", transport)

    return install


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


@contextmanager
def local_page_service(mode):
    """A real HTTP boundary: no OCR provider, token or model is contacted."""
    stopped, finished = Event(), Event()
    calls = []
    body = json.dumps({"result": page_result()}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(self.path)
            try:
                if mode == "headers":
                    stopped.wait(0.5)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if mode == "body":
                    stopped.wait(0.5)
                size = max(1, len(body) // 12) if mode == "drip" else len(body)
                for offset in range(0, len(body), size):
                    if stopped.is_set():
                        break
                    self.wfile.write(body[offset : offset + size])
                    self.wfile.flush()
                    if mode == "drip" and stopped.wait(0.035):
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                finished.set()

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/layout-parsing", calls
        finally:
            stopped.set()
            server.shutdown()
            thread.join(timeout=1)
            assert finished.wait(1) and not thread.is_alive()


def test_page_total_deadline_stops_a_response_that_never_hits_idle_timeout(source):
    with local_page_service("drip") as (endpoint, calls):
        began = time.monotonic()
        with pytest.raises(PaddleOCRFormatError, match="TimeoutError"):
            PaddleStructureService(endpoint, timeout=0.12).analyze_page(source, 2)
        assert time.monotonic() - began < 0.3
        assert calls == ["/layout-parsing"]


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_page_total_deadline_stops_a_stalled_http_response(source, phase):
    with local_page_service(phase) as (endpoint, calls):
        began = time.monotonic()
        with pytest.raises(PaddleOCRFormatError, match="TimeoutError"):
            PaddleStructureService(endpoint, timeout=0.12).analyze_page(source, 2)
        assert time.monotonic() - began < 0.3
        assert calls == ["/layout-parsing"]


@pytest.mark.parametrize("running_loop", [False, True])
def test_page_within_total_deadline_works_for_sync_and_async_callers(source, running_loop):
    with local_page_service("complete") as (endpoint, calls):
        service = PaddleStructureService(endpoint, timeout=1)

        async def within_loop():
            loop = asyncio.get_running_loop()
            result = service.analyze_page(source, 2)
            assert asyncio.get_running_loop() is loop
            return result

        actual = asyncio.run(within_loop()) if running_loop else service.analyze_page(source, 2)
        assert actual == page_result() and calls == ["/layout-parsing"]


def test_expired_response_closes_its_stream_before_returning_to_async_caller(source, http_boundary):
    closed, calls = [], []

    class WaitingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"result":'
            await asyncio.sleep(5)

        async def aclose(self):
            closed.append(True)

    def post(request):
        calls.append(request)
        return httpx.Response(200, stream=WaitingStream())

    http_boundary(post)

    async def within_loop():
        with pytest.raises(PaddleOCRFormatError, match="TimeoutError"):
            PaddleStructureService("https://ocr.example/layout-parsing", timeout=0.06).analyze_page(source, 1)
        assert len(calls) == 1 and closed == [True]

    asyncio.run(within_loop())


@pytest.mark.parametrize("compatibility_entry", [False, True])
def test_local_page_preparation_cannot_reset_an_expired_request_budget(source, monkeypatch, compatibility_entry):
    path = source.parent / "single.pdf"
    with fitz.open() as pdf:
        pdf.new_page()
        pdf.save(path)
    validate = client.validate_pdf

    def slow_validation(file_path):
        result = validate(file_path)
        time.sleep(0.025)
        return result

    monkeypatch.setattr(client, "validate_pdf", slow_validation)
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda *_a, **_kw: pytest.fail("expired preparation sent a page"))
    service = PaddleStructureService("https://ocr.example/layout-parsing", timeout=0.005)
    with pytest.raises(PaddleOCRFormatError, match="TimeoutError"):
        if compatibility_entry:
            service.analyze_full_pdf(path)
        else:
            service.analyze_page(path, 1)


@pytest.mark.parametrize("status", [302, 429, 503])
def test_page_http_failure_never_redirects_or_retries(source, http_boundary, status):
    calls = []

    def post(request):
        calls.append(str(request.url))
        return httpx.Response(status, headers={"Location": "https://other.example/layout-parsing"}, text="private provider response")

    http_boundary(post)
    with pytest.raises(PaddleOCRFormatError, match="HTTPStatusError") as failure:
        PaddleStructureService("https://ocr.example/layout-parsing").analyze_page(source, 1)
    assert calls == ["https://ocr.example/layout-parsing"]
    assert "private provider" not in str(failure.value)


def test_selected_page_sends_exactly_one_original_page_without_touching_source(source, http_boundary):
    before = source.read_bytes()
    calls = []

    def post(request):
        payload = json.loads(request.content)
        with fitz.open(stream=base64.b64decode(payload["file"]), filetype="pdf") as selected:
            assert len(selected) == 1
            assert "PAGE THREE" in selected[0].get_text()
            assert "PAGE ONE" not in selected[0].get_text()
        calls.append((str(request.url), request.extensions["timeout"]["read"]))
        return httpx.Response(200, json={"result": page_result()})

    http_boundary(post)
    actual = PaddleStructureService("https://ocr.example/layout-parsing", timeout=7).analyze_page(source, 3)
    assert actual == page_result()
    assert len(calls) == 1 and calls[0][0] == "https://ocr.example/layout-parsing"
    assert 6 < calls[0][1] <= 7
    assert source.read_bytes() == before


@pytest.mark.parametrize("page", [0, -1, 4, True, 1.5])
def test_invalid_original_page_never_calls_service(source, monkeypatch, page):
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda *_a, **_kw: pytest.fail("invalid page sent"))
    with pytest.raises(ValueError):
        PaddleStructureService("https://ocr.example/layout-parsing").analyze_page(source, page)


def test_page_timeout_is_one_attempt_and_does_not_claim_remote_cancellation(source, http_boundary):
    calls = []

    def unavailable(request):
        calls.append(True)
        raise httpx.ReadTimeout("do not expose provider URL or body", request=request)

    http_boundary(unavailable)
    with pytest.raises(PaddleOCRFormatError, match="ReadTimeout"):
        PaddleStructureService("https://ocr.example/layout-parsing").analyze_page(source, 2)
    assert calls == [True]


def test_page_request_uses_the_smaller_remaining_timeout(source, http_boundary):
    timeouts = []

    def post(request):
        timeouts.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"result": page_result()})

    http_boundary(post)
    service = PaddleStructureService("https://ocr.example/layout-parsing", timeout=7)
    service.analyze_page(source, 1, timeout=0.5)
    service.analyze_page(source, 1, timeout=10)
    assert 0 < timeouts[0] <= 0.5 and 6 < timeouts[1] <= 7


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_invalid_remaining_timeout_never_sends_a_page(source, monkeypatch, timeout):
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda *_a, **_kw: pytest.fail("invalid timeout sent"))
    with pytest.raises(ValueError, match="timeout"):
        PaddleStructureService("https://ocr.example/layout-parsing").analyze_page(source, 1, timeout=timeout)


def test_source_change_during_request_rejects_late_evidence(source, http_boundary):
    def replace_source(_request):
        source.write_bytes(b"changed while provider was running")
        return httpx.Response(200, json={"result": page_result()})

    http_boundary(replace_source)
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


@pytest.mark.parametrize("bounds", [None, [20, 40, 900, 500]])
def test_unlocated_seal_is_unknown_while_other_source_facts_survive(source, bounds):
    raw = page_result()
    pruned = raw["layoutParsingResults"][0]["prunedResult"]
    pruned["layout_det_res"]["boxes"][0]["coordinate"] = bounds
    pruned["seal_res_list"][0]["bbox"] = bounds
    evidence = build_evidence(raw, page_count=1, pdf_path=source, source_pages=[2])
    seal = evidence["pages"][0]["seals"][0]
    assert seal["pdf_bbox"] is None
    assert evidence["pages"][0]["texts"][0]["pdf_bbox"] is not None
    relevant = [d for d in evidence["diagnostics"] if d["element"] == "seal"]
    assert relevant and all(d["outcome"] == "unknown" for d in relevant)


def test_ambiguous_seal_text_binding_has_unknown_diagnostic(source):
    raw = page_result()
    pruned = raw["layoutParsingResults"][0]["prunedResult"]
    pruned["layout_det_res"]["boxes"] = [
        {"label": "seal", "coordinate": [20, 20, 100, 100]},
        {"label": "seal", "coordinate": [80, 20, 160, 100]},
    ]
    pruned["seal_res_list"] = [{"rec_texts": ["ARCHIVE"], "bbox": [20, 20, 160, 100]}]
    evidence = build_evidence(raw, page_count=1, pdf_path=source, source_pages=[2])
    seals = evidence["pages"][0]["seals"]
    assert all(s["recognized_text"] == [] for s in seals if s["source"] == "paddle_layout")
    binding = [d for d in evidence["diagnostics"] if d["element"] == "seal"]
    assert binding and all(d["outcome"] == "unknown" for d in binding)
