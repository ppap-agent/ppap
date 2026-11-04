"""Image-only PDF table topology is grounded in pixels and fixed-cell OCR."""

from __future__ import annotations

import asyncio
import re
from unittest.mock import patch

import cv2
import fitz
import httpx
import numpy as np
import pytest
from documents.conversion import pipeline
from documents.ocr import cell_ocr, page, raster
from documents.ocr.local import convert_local_pdf_to_excel


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Scan-owner tests must not request a real model")

    async def reject_async(*args, **kwargs):
        raise AssertionError("Scan-owner tests must not request a real model")

    monkeypatch.setattr(httpx.Client, "send", reject)
    monkeypatch.setattr(httpx.AsyncClient, "send", reject_async)


def _scan(tmp_path, *, merge_header: bool = False, ruled: bool = True):
    image = np.full((900, 1100, 3), 255, dtype=np.uint8)
    xs = [75, 320, 590, 930]
    ys = [120, 215, 320, 425]
    if ruled:
        for y in ys:
            cv2.line(image, (xs[0], y), (xs[-1], y), (0, 0, 0), 3)
        for x in xs:
            cv2.line(image, (x, ys[0]), (x, ys[-1]), (0, 0, 0), 3)
        if merge_header:
            cv2.line(image, (xs[1], ys[0] + 3), (xs[1], ys[1] - 3), (255, 255, 255), 9)
    source = [
        ("ALPHA", (90, 172)),
        ("GAMMA", (620, 172)),
        ("A2", (90, 277)),
        ("B2", (350, 277)),
        ("C2", (620, 277)),
        ("A3", (90, 382)),
        ("B3", (350, 382)),
        ("C3", (620, 382)),
    ]
    if not merge_header:
        source.append(("BETA", (350, 172)))
    else:
        source.append(("BETA", (225, 172)))
    for value, position in source:
        cv2.putText(image, value, position, cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)
    encoded, png = cv2.imencode(".png", image)
    assert encoded
    path = tmp_path / ("merged-scan.pdf" if merge_header else "scan.pdf")
    with fitz.open() as document:
        pdf_page = document.new_page(width=1100, height=900)
        pdf_page.insert_image(pdf_page.rect, stream=png.tobytes())
        document.save(path)
    with fitz.open(path) as document:
        assert not document[0].get_text("words")
        assert not document[0].get_drawings()
    return path, png.tobytes()


def _model_page():
    return {
        "tables": [
            {
                "rows": [["ALPHA", "BETA", "GAMMA"], ["A2", "B2", "C2"], ["A3", "B3", "C3"]],
                "merges": [{"start_row": 0, "start_col": 0, "end_row": 0, "end_col": 1}],
            }
        ],
        "paragraphs": [],
        "seals": [],
    }


def _fake_vision(*, merged: bool, corrupt: bool | str = False):
    values = ["ALPHA BETA" if merged else "ALPHA", "GAMMA"] if merged else ["ALPHA", "BETA", "GAMMA"]
    values += ["A2", "B2", "C2", "A3", "B3", "C3"]
    calls = []

    async def respond(**kwargs):
        schema = kwargs["response_schema"]["name"]
        calls.append(schema)
        if schema == "ppap_scanned_page_remainder":
            return {"paragraphs": ["outside paragraph"], "seals": ["visible signature"], "has_other_tables": False}
        if schema == "ppap_multimodal_pdf_page":
            return _model_page()
        assert schema == "ppap_raster_cell_evidence"
        ids = [int(value) for value in re.findall(r"\d+", kwargs["prompt"].split("Requested CELL IDs: ", 1)[1])]
        entries = {str(index): values[index] for index in reversed(ids)}
        if corrupt:
            if corrupt == "missing":
                entries.pop(str(ids[0]))
            else:
                entries[str(ids[0] + 123)] = entries.pop(str(ids[0]))
        # Responses can reorder entries. Only their source-bound ID is authority.
        return {"cells": entries}

    return respond, calls


def test_scanned_pdf_has_no_vector_anchors_but_detects_physical_grid(tmp_path):
    path, _source = _scan(tmp_path)
    with fitz.open(path) as pdf:
        image = pdf[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png")
        grids = raster.detect_raster_grids(image)
    assert len(grids) == 1
    assert (grids[0].row_count, grids[0].col_count) == (3, 3)
    assert len(grids[0].cells) == 9
    assert grids[0].merges == ()


def test_dense_skewed_scan_keeps_independent_cells():
    image = np.full((1000, 1300, 3), 255, dtype=np.uint8)
    for y in range(100, 901, 40):
        cv2.line(image, (100, y), (1180, y), (0, 0, 0), 3)
    for x in range(100, 1181, 45):
        cv2.line(image, (x, 100), (x, 900), (0, 0, 0), 3)
    transform = cv2.getPerspectiveTransform(
        np.float32([[100, 100], [1180, 100], [1180, 900], [100, 900]]),
        np.float32([[108, 95], [1185, 105], [1174, 910], [95, 895]]),
    )
    image = cv2.warpPerspective(image, transform, (1300, 1000), borderValue=(255, 255, 255))
    encoded, png = cv2.imencode(".png", image)
    assert encoded
    _normalized, grids = raster.read_raster_page(png.tobytes())
    assert len(grids) == 1
    assert (grids[0].row_count, grids[0].col_count, len(grids[0].cells)) == (20, 24, 480)
    assert not grids[0].merges


def test_open_outer_cell_border_does_not_manufacture_a_complete_grid(tmp_path):
    _path, png = _scan(tmp_path)
    image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    cv2.line(image, (75, 140), (75, 195), (255, 255, 255), 12)
    encoded, damaged = cv2.imencode(".png", image)
    assert encoded
    _normalized, grids = raster.read_raster_page(damaged.tobytes())
    assert grids == []


def test_scanned_pdf_reads_physical_cells_without_first_generating_a_model_grid(tmp_path):
    path, _source = _scan(tmp_path)
    fake, calls = _fake_vision(merged=False)

    async def respond(**kwargs):
        assert kwargs["response_schema"]["name"] != "ppap_multimodal_pdf_page", "A physical scan must not depend on generating an entire model grid"
        return await fake(**kwargs)

    with patch.object(page, "call_llm_async", side_effect=respond), patch("documents.ocr.cell_ocr.call_llm_async", side_effect=respond):
        pages = page.VisionLayoutService().analyze_full_pdf(str(path))
    assert pages[0]["tables"][0]["rows"] == _model_page()["tables"][0]["rows"]
    assert pages[0]["tables"][0]["merges"] == []
    assert calls == ["ppap_raster_cell_evidence", "ppap_scanned_page_remainder"]
    assert pages[0]["paragraphs"] == ["outside paragraph"]
    assert pages[0]["seals"] == ["visible signature"]


def test_true_scanned_merge_uses_source_cell_identity_not_model_merge(tmp_path):
    path, _source = _scan(tmp_path, merge_header=True)
    fake, _calls = _fake_vision(merged=True)
    with fitz.open(path) as pdf:
        image = pdf[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png")
        grids = raster.detect_raster_grids(image)
    assert len(grids) == 1
    assert grids[0].merges == ((0, 0, 0, 1),)

    with patch.object(page, "call_llm_async", side_effect=fake), patch("documents.ocr.cell_ocr.call_llm_async", side_effect=fake):
        pages = page.VisionLayoutService().analyze_full_pdf(str(path))
    assert pages[0]["tables"][0]["rows"][0] == ["ALPHA BETA", "", "GAMMA"]
    assert pages[0]["tables"][0]["merges"] == [{"start_row": 0, "start_col": 0, "end_row": 0, "end_col": 1}]


def test_scanned_unruled_document_fails_closed_without_source_grid(tmp_path):
    path, _source = _scan(tmp_path, ruled=False)
    fake, calls = _fake_vision(merged=False)
    with patch.object(page, "call_llm_async", side_effect=fake):
        with pytest.raises(page.ResponsesEvidenceError, match="merge covers nonempty evidence"):
            page.VisionLayoutService().analyze_full_pdf(str(path))
    assert calls == ["ppap_multimodal_pdf_page"]


@pytest.mark.parametrize("corrupt", ["foreign", "missing"])
def test_raster_transcription_requires_exactly_the_requested_cell_ids(tmp_path, corrupt):
    path, _source = _scan(tmp_path)
    fake, _calls = _fake_vision(merged=False, corrupt=corrupt)
    with patch.object(page, "call_llm_async", side_effect=fake), patch("documents.ocr.cell_ocr.call_llm_async", side_effect=fake):
        with pytest.raises(page.ResponsesEvidenceError) as error:
            page.VisionLayoutService().analyze_full_pdf(str(path))
    assert error.value.code == "raster_cells_invalid_schema"


def test_remaining_unmapped_table_is_not_silently_discarded(tmp_path):
    path, _source = _scan(tmp_path)
    fake, _calls = _fake_vision(merged=False)

    async def respond(**kwargs):
        if kwargs["response_schema"]["name"] == "ppap_scanned_page_remainder":
            return {"paragraphs": ["private remaining evidence"], "seals": [], "has_other_tables": True}
        return await fake(**kwargs)

    with patch.object(page, "call_llm_async", side_effect=respond), patch("documents.ocr.cell_ocr.call_llm_async", side_effect=respond):
        with pytest.raises(page.ResponsesEvidenceError) as error:
            page.VisionLayoutService().analyze_full_pdf(str(path))
    assert error.value.code == "raster_source_unavailable"
    assert "private remaining evidence" not in str(error.value)


@pytest.mark.parametrize("code", ["responses_invalid_json", "responses_timeout", "raster_cell_timeout"])
def test_global_fallback_does_not_reenter_legacy_cell_ocr(tmp_path, monkeypatch, code):
    path, _source = _scan(tmp_path)
    calls = []

    async def malformed(**kwargs):
        calls.append(kwargs["response_schema"]["name"])
        raise page.ResponsesEvidenceError(code, "Controlled evidence failure")

    monkeypatch.setenv("OCR_ADAPTER_MODE", "local")
    monkeypatch.setenv("PADDLE_EVIDENCE_ENABLED", "false")
    with patch.object(page, "call_llm_async", side_effect=malformed), patch("documents.ocr.cell_ocr.call_llm_async", side_effect=malformed):
        result, error = pipeline._attempt_ocr(str(path), enable_cache=False)
    assert result is None
    assert error["error_code"] == "paddle_evidence_disabled"
    assert calls == []


def test_scanned_page_deadline_includes_remainder_and_cancels_its_request(tmp_path):
    path, _source = _scan(tmp_path)
    fake, _calls = _fake_vision(merged=False)
    cancelled = []
    original_timeout = asyncio.timeout
    deadline = None

    def controlled_deadline(seconds):
        nonlocal deadline
        assert seconds == 180
        deadline = original_timeout(None)
        return deadline

    async def respond(**kwargs):
        if kwargs["response_schema"]["name"] == "ppap_scanned_page_remainder":
            try:
                # Expire the real page timer at this cut point, independent of crop CPU time.
                deadline.reschedule(asyncio.get_running_loop().time())
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        return await fake(**kwargs)

    with (
        patch.object(page.asyncio, "timeout", side_effect=controlled_deadline),
        patch.object(page, "call_llm_async", side_effect=respond),
        patch.object(cell_ocr, "call_llm_async", side_effect=respond),
    ):
        with pytest.raises(page.ResponsesEvidenceError) as error:
            page.VisionLayoutService().analyze_full_pdf(str(path))
    assert error.value.code == "raster_cell_timeout"
    assert cancelled == [True]


def test_scan_batches_complete_out_of_order_with_two_requests_and_keep_page_identity(tmp_path, monkeypatch):
    _path, png = _scan(tmp_path)
    grids = raster.detect_raster_grids(png)
    monkeypatch.setattr(cell_ocr, "CELLS_PER_REQUEST", 3)

    async def scenario():
        active = peak = page_index = 0
        releases = [asyncio.Event(), asyncio.Event()]
        started, completed = [], []

        async def respond(**kwargs):
            nonlocal active, peak, page_index
            active += 1
            peak = max(peak, active)
            try:
                if kwargs["response_schema"]["name"] == "ppap_scanned_page_remainder":
                    assert active == 1
                    page_index += 1
                    return {"paragraphs": [], "seals": [], "has_other_tables": False}
                current_page = page_index
                ids = kwargs["response_schema"]["schema"]["properties"]["cells"]["required"]
                key = (current_page, ids[0])
                assert key not in started
                started.append(key)
                if ids[0] == "0":
                    await releases[current_page].wait()
                else:
                    releases[current_page].set()
                completed.append(key)
                return {"cells": {index: f"{current_page}:{index}" for index in reversed(ids)}}
            finally:
                active -= 1

        with patch.object(page, "call_llm_async", side_effect=respond), patch.object(cell_ocr, "call_llm_async", side_effect=respond):
            results = await asyncio.wait_for(page._read_batch([(index, png, [{"raster_grid": grids[0]}]) for index in (1, 2)], 2), timeout=2)
        assert peak == 2 and active == 0
        assert len(started) == len(set(started)) == 6
        assert completed[:2] == [(0, "3"), (0, "6")]
        for index, result in enumerate(results):
            assert result["tables"][0]["rows"] == [[f"{index}:{row * 3 + col}" for col in range(3)] for row in range(3)]

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["provider", "deadline", "cancel"])
def test_scan_batch_failure_drains_both_requests_without_starting_another(tmp_path, monkeypatch, failure):
    _path, png = _scan(tmp_path)
    grids = raster.detect_raster_grids(png)
    monkeypatch.setattr(cell_ocr, "CELLS_PER_REQUEST", 3)
    if failure == "deadline":
        monkeypatch.setattr(page, "MAX_RASTER_EVIDENCE_SECONDS", 0.5)

    async def scenario():
        both_started = asyncio.Event()
        started, closed = [], []

        async def respond(**kwargs):
            assert kwargs["response_schema"]["name"] == "ppap_raster_cell_evidence"
            ids = kwargs["response_schema"]["schema"]["properties"]["cells"]["required"]
            started.append(ids[0])
            if len(started) == 2:
                both_started.set()
            try:
                await both_started.wait()
                if failure == "provider" and ids[0] == "0":
                    raise page.ResponsesEvidenceError("responses_invalid_json", "Controlled provider failure")
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)  # Async client cleanup must finish before return.
                closed.append(ids[0])

        with patch.object(cell_ocr, "call_llm_async", side_effect=respond):
            operation = asyncio.create_task(page._read_scan_page(png, grids))
            await asyncio.wait_for(both_started.wait(), timeout=2)
            if failure == "cancel":
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
            else:
                with pytest.raises(page.ResponsesEvidenceError) as error:
                    await operation
                assert error.value.code == ("responses_invalid_json" if failure == "provider" else "raster_cell_timeout")
        assert started == ["0", "3"]
        assert sorted(closed) == ["0", "3"]
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]

    asyncio.run(scenario())


def test_table_mask_preserves_all_pixels_outside_the_verified_table(tmp_path):
    _path, png = _scan(tmp_path)
    grids = raster.detect_raster_grids(png)
    original = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    masked = cv2.imdecode(np.frombuffer(raster.mask_raster_tables(png, grids), dtype=np.uint8), cv2.IMREAD_COLOR)
    x, y, width, height = grids[0].bbox
    expected = original.copy()
    expected[y : y + height, x : x + width] = 255
    assert np.array_equal(masked, expected)


def test_paddle_conversion_does_not_read_or_write_legacy_raster_cache(tmp_path, monkeypatch):
    path, _ = _scan(tmp_path)
    monkeypatch.setenv("OCR_LOCAL_OSS_DIR", str(tmp_path / "raster-cache"))
    monkeypatch.setenv("OCR_LOCAL_SQLITE_DB", str(tmp_path / "raster-cache" / "cache.db"))
    monkeypatch.setenv("PADDLE_EVIDENCE_ENABLED", "false")
    fake, calls = _fake_vision(merged=False, corrupt=True)
    with patch.object(page, "call_llm_async", side_effect=fake), patch("documents.ocr.cell_ocr.call_llm_async", side_effect=fake):
        artifact = convert_local_pdf_to_excel(str(path))
    assert artifact.excel_path is None
    assert artifact.raw_meta["error_code"] == "paddle_evidence_disabled"
    assert calls == []
    assert not list((tmp_path / "raster-cache" / "results").glob("*.xlsx"))
