"""Image-only PDF table topology is grounded in pixels and fixed-cell OCR."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import cv2
import fitz
import httpx
import numpy as np
import pytest
from ppap_core.settings import get_settings
from ppap_pdf_to_excel.vision import page, raster
from ppap_pdf_to_excel.vision.local import convert_local_pdf_to_excel


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


def _fake_vision(*, merged: bool, corrupt: bool = False):
    values = ["ALPHA BETA" if merged else "ALPHA", "GAMMA"] if merged else ["ALPHA", "BETA", "GAMMA"]
    values += ["A2", "B2", "C2", "A3", "B3", "C3"]
    calls = []

    async def respond(**kwargs):
        schema = kwargs["response_schema"]["name"]
        calls.append(schema)
        if schema == "ppap_multimodal_pdf_page":
            return _model_page()
        assert schema == "ppap_raster_cell_evidence"
        ids = [int(value) for value in re.findall(r"\d+", kwargs["prompt"].split("Requested CELL IDs: ", 1)[1])]
        if corrupt:
            return {"cells": [{"id": ids[0] + 123, "text": "untrusted"}]}
        return {"cells": [{"id": index, "text": values[index]} for index in ids]}

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


def test_real_dense_control_plan_pages_have_complete_physical_cell_partitions():
    source = Path(__file__).resolve().parents[3] / "assets/poc测试/POC测试样本/Blanc Spoiler-有特殊特性清单/19-BLANC尾-生产控制计划.pdf"
    original = source.read_bytes()
    with fitz.open(stream=original, filetype="pdf") as pdf:
        for pdf_page in pdf:
            assert not pdf_page.get_text("words")
            image = pdf_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png")
            normalized, grids = raster.read_raster_page(image)
            assert len(grids) == 1
            grid = grids[0]
            occupied = set()
            rows = [["" for _ in range(grid.col_count)] for _ in range(grid.row_count)]
            height, width = cv2.imdecode(np.frombuffer(normalized, dtype=np.uint8), 0).shape
            for cell in grid.cells:
                x0, y0, x1, y1 = cell.bbox
                assert 0 <= x0 < x1 < width and 0 <= y0 < y1 < height
                slots = {(r, c) for r in range(cell.row, cell.end_row + 1) for c in range(cell.col, cell.end_col + 1)}
                assert slots and not occupied.intersection(slots)
                occupied.update(slots)
                rows[cell.row][cell.col] = str(cell.index)
            assert len(occupied) == grid.row_count * grid.col_count
            # This is the actual density that the former 400-slot cap rejected.
            assert len(occupied) > 400
            page._validate_page(
                {
                    "tables": [{"rows": rows, "merges": [dict(zip(("start_row", "start_col", "end_row", "end_col"), bounds, strict=True)) for bounds in grid.merges]}],
                    "paragraphs": [],
                    "seals": [],
                }
            )
    assert source.read_bytes() == original


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


def test_scanned_pdf_replaces_false_model_merges_without_erasing_text(tmp_path):
    path, _source = _scan(tmp_path)
    fake, calls = _fake_vision(merged=False)
    with patch.object(page, "call_llm_async", side_effect=fake), patch("ppap_pdf_to_excel.vision.cell_ocr.call_llm_async", side_effect=fake):
        pages = page.VisionLayoutService().analyze_full_pdf(str(path))
    assert pages[0]["tables"][0]["rows"] == _model_page()["tables"][0]["rows"]
    assert pages[0]["tables"][0]["merges"] == []
    assert "ppap_raster_cell_evidence" in calls


def test_true_scanned_merge_uses_source_cell_identity_not_model_merge(tmp_path):
    path, _source = _scan(tmp_path, merge_header=True)
    fake, _calls = _fake_vision(merged=True)
    with fitz.open(path) as pdf:
        image = pdf[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png")
        grids = raster.detect_raster_grids(image)
    assert len(grids) == 1
    assert grids[0].merges == ((0, 0, 0, 1),)

    with patch.object(page, "call_llm_async", side_effect=fake), patch("ppap_pdf_to_excel.vision.cell_ocr.call_llm_async", side_effect=fake):
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


def test_raster_transcription_does_not_accept_spoofed_cell_ids(tmp_path):
    path, _source = _scan(tmp_path)
    fake, _calls = _fake_vision(merged=False, corrupt=True)
    with patch.object(page, "call_llm_async", side_effect=fake), patch("ppap_pdf_to_excel.vision.cell_ocr.call_llm_async", side_effect=fake):
        with pytest.raises(page.ResponsesEvidenceError) as error:
            page.VisionLayoutService().analyze_full_pdf(str(path))
    assert error.value.code == "raster_cells_invalid_schema"


def test_raster_model_text_must_have_cropped_source_witness(tmp_path):
    path, _source = _scan(tmp_path)

    async def respond(**kwargs):
        if kwargs["response_schema"]["name"] == "ppap_multimodal_pdf_page":
            model = _model_page()
            model["tables"][0]["rows"][0][1] = "private hallucinated field"
            return model
        ids = [int(value) for value in re.findall(r"\d+", kwargs["prompt"].split("Requested CELL IDs: ", 1)[1])]
        correct = ["ALPHA", "BETA", "GAMMA", "A2", "B2", "C2", "A3", "B3", "C3"]
        return {"cells": [{"id": index, "text": correct[index]} for index in ids]}

    with patch.object(page, "call_llm_async", side_effect=respond), patch("ppap_pdf_to_excel.vision.cell_ocr.call_llm_async", side_effect=respond):
        with pytest.raises(page.ResponsesEvidenceError) as error:
            page.VisionLayoutService().analyze_full_pdf(str(path))
    assert error.value.code == "raster_text_unverified"
    assert "private hallucinated field" not in str(error.value)


def test_unverifiable_raster_cell_ocr_does_not_cache_partial_results(tmp_path, monkeypatch):
    path, _ = _scan(tmp_path)
    monkeypatch.setenv("OCR_LOCAL_OSS_DIR", str(tmp_path / "raster-cache"))
    monkeypatch.setenv("OCR_LOCAL_SQLITE_DB", str(tmp_path / "raster-cache" / "cache.db"))
    get_settings.cache_clear()
    fake, _calls = _fake_vision(merged=False, corrupt=True)
    try:
        with patch.object(page, "call_llm_async", side_effect=fake), patch("ppap_pdf_to_excel.vision.cell_ocr.call_llm_async", side_effect=fake):
            artifact = convert_local_pdf_to_excel(str(path))
        assert artifact.excel_path is None
        assert artifact.raw_meta["error_code"] == "raster_cells_invalid_schema"
        assert not list((tmp_path / "raster-cache" / "results").glob("*.xlsx"))
    finally:
        get_settings.cache_clear()
