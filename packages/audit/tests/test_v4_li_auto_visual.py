"""Native/Paddle fact contracts; offline records do not certify OCR accuracy."""

import hashlib
import json
from types import SimpleNamespace

import fitz
import pytest
from audit.checkers.v4.li_auto import PDF_AGENTS
from audit.checkers.v4.visual_facts import read_original_pdf_visual_facts
from audit.contracts import AuditInput, RuleInput
from audit.engine.checkers.client import AIClient
from audit.engine.runtime import bind_runtime
from audit.presets import preset_rule_body
from documents.conversion import convert_pdf_to_excel_auto


def original_pdf(path, count=1):
    with fitz.open() as document:
        for _ in range(count):
            page = document.new_page(width=500, height=600)
            for index, (label, value) in enumerate([("报告编号", "R-001"), ("试验结论", "通过"), ("试验类型", "PV"), ("样品数量", "3")]):
                page.insert_text((60, 90 + 40 * index), label, fontname="china-s", fontsize=12)
                page.insert_text((260, 90 + 40 * index), value, fontname="china-s", fontsize=12)
            for y in range(60, 221, 40):
                page.draw_line((50, y), (450, y))
            for x in (50, 250, 450):
                page.draw_line((x, 60), (x, 220))
        document.save(path)


def evidence(path, *, seals=(), page=1):
    return {
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "engine": "paddleocr_ppstructure_v3",
        "pages": [{"page": page, "frame": {"pdf_width": 500, "pdf_height": 600}, "seals": list(seals)}],
        "diagnostics": [],
    }


@pytest.fixture(autouse=True)
def forbid_llm(monkeypatch):
    attempts = []

    def reject(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Paddle facts must not fall back to LLM vision")

    monkeypatch.setattr(AIClient, "image_analysis_completion", reject)
    yield
    assert attempts == []


@pytest.mark.parametrize("detected", [False, True])
def test_l03_reuses_one_page_and_never_infers_photos_or_seal_absence(tmp_path, monkeypatch, detected):
    path = tmp_path / "原件.pdf"
    original_pdf(path)
    calls = []

    def paddle(source, page):
        calls.append((source, page))
        return evidence(path, seals=[{"detected": True, "pdf_bbox": [10, 20, 60, 80]}] if detected else [])

    monkeypatch.setattr("documents.ocr.paddle.reader.read_pdf_page_evidence", paddle)
    runtime = SimpleNamespace(convert_pdf_to_excel=lambda ref, **kwargs: convert_pdf_to_excel_auto(ref, allow_ocr=False, **kwargs))
    with bind_runtime(runtime):
        agent = PDF_AGENTS["L03"](AuditInput(audit_id="original-version", file_url=str(path)), RuleInput(rule_id="r", **preset_rule_body("理想", "L03")))
        try:
            results = [agent._execute_rule_check(4), agent._execute_rule_check(5)]
            assert [item.status for item in results] == (["PASS", "UNABLE"] if detected else ["UNABLE", "UNABLE"])
            if detected:
                visual = [source for source in results[0].evidence.sources if source.kind == "visual"]
                assert {json.loads(source.locator)["file_version_id"] for source in visual} == {"original-version"}
                assert {json.loads(source.locator)["page"] for source in visual} == {1}
        finally:
            agent.close()
    assert len(calls) == 1


@pytest.mark.parametrize("corrupt", ["page", "source", "transport"])
def test_page_identity_and_technical_failures_are_cached_without_retry(tmp_path, monkeypatch, corrupt):
    path = tmp_path / "原件.pdf"
    original_pdf(path)
    calls = []

    def paddle(source, page):
        calls.append(page)
        if corrupt == "transport":
            raise TimeoutError("private endpoint must not be logged")
        value = evidence(path)
        if corrupt == "page":
            value["pages"][0]["page"] = 2
        else:
            value["source_sha256"] = "0" * 64
        return value

    monkeypatch.setattr("documents.ocr.paddle.reader.read_pdf_page_evidence", paddle)
    cache = {}
    audit = AuditInput(audit_id="v", file_url=str(path))
    for _ in range(2):
        with pytest.raises((ValueError, TimeoutError)):
            read_original_pdf_visual_facts(audit, {"seal": "实际印章"}, lambda *a, **k: None, cache=cache)
    assert calls == [1]


def test_unselected_long_source_and_unsupported_photos_do_not_trigger_ocr(tmp_path, monkeypatch):
    path = tmp_path / "长原件.pdf"
    original_pdf(path, count=29)
    monkeypatch.setattr("documents.ocr.paddle.reader.read_pdf_page_evidence", lambda *a: pytest.fail("No selected page or supported fact"))
    audit = AuditInput(audit_id="v", file_url=str(path))
    assert read_original_pdf_visual_facts(audit, {"seal": "实际印章"}, lambda *a, **k: None) == {"seal": None}
    assert read_original_pdf_visual_facts(audit, {"before": "试验前照片"}, lambda *a, **k: None) == {"before": None}


@pytest.mark.parametrize(("text", "expected"), [("受控", True), ("受控章", True), ("不受控", None), ("非受控", None), ("部门章", None)])
def test_controlled_stamp_requires_a_detected_seals_own_affirmative_text(tmp_path, monkeypatch, text, expected):
    path = tmp_path / "原件.pdf"
    original_pdf(path)
    monkeypatch.setattr("documents.ocr.paddle.reader.read_pdf_page_evidence", lambda *a: evidence(path, seals=[{"detected": True, "pdf_bbox": [10, 20, 60, 80], "recognized_text": [text]}]))
    value = read_original_pdf_visual_facts(AuditInput(audit_id="v", file_url=str(path)), {"controlled_stamp": "真实受控章"}, lambda *a, **k: None)
    assert value["controlled_stamp"] is expected


def test_a_frozen_version_cannot_accept_changed_bytes_without_metadata_sha(tmp_path, monkeypatch):
    path = tmp_path / "原件.pdf"
    original_pdf(path)
    calls = []

    def paddle(*args):
        calls.append(True)
        return evidence(path, seals=[{"detected": True, "pdf_bbox": [10, 20, 60, 80]}])

    monkeypatch.setattr("documents.ocr.paddle.reader.read_pdf_page_evidence", paddle)
    cache = {}
    audit = AuditInput(audit_id="frozen-v", file_url=str(path))
    assert read_original_pdf_visual_facts(audit, {"seal": "实际印章"}, lambda *a, **k: None, cache=cache)["seal"] is True
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Frozen PDF identity changed"):
        read_original_pdf_visual_facts(audit, {"seal": "实际印章"}, lambda *a, **k: None, cache=cache)
    assert calls == [True]


@pytest.mark.parametrize("overlay", ["vector", "image", "annotation"])
@pytest.mark.parametrize("paddle_checked", [False, True], ids=["unavailable-paddle", "explicit-paddle-mark"])
def test_native_checkbox_glyph_with_overlapping_graphics_does_not_prove_an_empty_box(tmp_path, monkeypatch, overlay, paddle_checked):
    from io import BytesIO

    from PIL import Image, ImageDraw

    path = tmp_path / "marked-original.pdf"
    labels = ("认可情况：", "□批准", "□条件批准", "□拒绝")
    with fitz.open() as document:
        page = document.new_page(width=500, height=600)
        for x, text in zip((20, 120, 260, 400), labels, strict=True):
            page.insert_text((x, 80), text, fontname="china-s", fontsize=11)
        boxes = [list(page.search_for(label)[0]) for label in labels]
        glyph = fitz.Rect(boxes[1][0], boxes[1][1], boxes[1][0] + 11, boxes[1][3])
        strokes = [[(glyph.x0 + 1, glyph.y0 + 3), (glyph.x1 - 1, glyph.y1 - 2)], [(glyph.x1 - 1, glyph.y0 + 3), (glyph.x0 + 1, glyph.y1 - 2)]]
        if overlay == "vector":
            for start, end in strokes:
                page.draw_line(start, end, color=(0, 0, 0), width=1)
        elif overlay == "image":
            image = Image.new("RGB", (20, 20), "white")
            draw = ImageDraw.Draw(image)
            draw.line((2, 2, 18, 18), fill="black", width=2)
            draw.line((18, 2, 2, 18), fill="black", width=2)
            encoded = BytesIO()
            image.save(encoded, format="PNG")
            page.insert_image(glyph, stream=encoded.getvalue())
        else:
            annotation = page.add_ink_annot(strokes)
            annotation.set_colors(stroke=(0, 0, 0))
            annotation.update()
        # The text layer still says empty, while the original has an actual X.
        assert "□批准" in page.get_text()
        document.save(path)
    calls = []

    def paddle(source, page):
        calls.append((str(source), page))
        if not paddle_checked:
            from documents.ocr.paddle.reader import PaddleEvidenceUnavailable

            raise PaddleEvidenceUnavailable("No replayable evidence")
        value = evidence(path)
        value["pages"][0]["texts"] = [
            {"index": index, "text": text, "pdf_bbox": box, "source": "paddle_overall_ocr", "type": "ocr_word", "score": 0.99}
            for index, (text, box) in enumerate(zip(("认可情况：", "☑批准", "□条件批准", "□拒绝"), boxes, strict=True))
        ]
        return value

    monkeypatch.setattr("documents.ocr.paddle.reader.read_pdf_page_evidence", paddle)
    fields = {name: name for name in ("disposition_visible", "approved_checked", "conditional_checked", "rejected_checked")}
    audit = AuditInput(audit_id="frozen-marked-version", file_url=str(path))
    cache = {}
    for _ in range(2):
        facts = read_original_pdf_visual_facts(audit, fields, lambda *a, **k: None, cache=cache)
        assert facts["approved_checked"] is (True if paddle_checked else None)
        assert facts["conditional_checked"] is False
        assert facts["rejected_checked"] is False
    assert calls == [(str(path), 1)]
