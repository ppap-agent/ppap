"""Existence scans every source page until a confirmed witness decides it."""

import io
import re

import fitz
import pytest
from audit.checker_lib.li.dpvr_excel_agent import DpvrExcelAgent
from audit.contracts import AuditInput, RuleInput
from audit.engine.runtime import bind_runtime
from documents.ocr.observation import current_ocr_observation
from openpyxl import Workbook
from PIL import Image, ImageChops


def pdf(path, count, marked=()):
    with fitz.open() as document:
        for number in range(1, count + 1):
            page = document.new_page(width=600, height=850)
            page.insert_text((40, 70), f"Content {number}", fontsize=12)
            if number in marked:
                page.draw_circle(fitz.Point(450, 650), 45, color=(1, 0, 0), width=3)
        document.save(path)
    return path


def agent(path, tmp_path):
    converted = tmp_path / "converted.xlsx"
    workbook = Workbook()
    workbook.active.title = "DPVR报告"
    workbook.active.append(["报告编号", "CONTROLLED"])
    workbook.save(converted)
    workbook.close()
    rules = RuleInput(
        rule_id="fixed-report", type=DpvrExcelAgent.DOC_TYPE.get_type_name(), rules=[{"id": str(number), "category": "原件检查", "rule_to_check": "已确认规则"} for number in range(1, 7)]
    )
    return DpvrExcelAgent(AuditInput(audit_id="frozen-report", file_url=str(converted), file_metadata={"source_pdf_url": str(path)}), rules)


@pytest.fixture
def observations(monkeypatch):
    monkeypatch.setenv("OCR_MODEL_NAME", "controlled")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://controlled.invalid")
    monkeypatch.setattr("audit.engine.checkers.visual_facts._read_paddle_visual_facts", lambda *args, **kwargs: {"seal": None})
    calls = []

    def read(*, images, prompt, response_schema):
        page = int(re.search(r"'page': (\d+)", prompt).group(1))
        calls.append((page, current_ocr_observation()))
        present = False
        for data in images:
            with Image.open(io.BytesIO(data)) as image:
                rgb = image.convert("RGB")
                present |= ImageChops.subtract(rgb.getchannel("R"), rgb.getchannel("G")).getbbox() is not None
        return {"seal": present}

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    return calls


def check(path, tmp_path, observations, expected):
    with bind_runtime(None):
        checker = agent(path, tmp_path)
        try:
            assert checker.check(3).status == expected
            count = len(observations)
            assert checker.check(3).status == expected
            assert len(observations) == count
        finally:
            checker.close()


@pytest.mark.parametrize("position,leading", [(2, 0), (6, 0), (11, 3)])
def test_middle_evidence_is_found_without_labels_after_page_moves(tmp_path, observations, position, leading):
    target = position + leading
    source = pdf(tmp_path / "unrelated-name.pdf", 17 + leading, marked={target})
    check(source, tmp_path, observations, "PASS")
    assert observations == [(page, ordinal) for page in range(1, target + 1) for ordinal in (1, 2)]


@pytest.mark.parametrize("count", [1, 2, 14, 27])
def test_no_seal_requires_every_page_to_be_confirmed_negative(tmp_path, observations, count):
    check(pdf(tmp_path / "no-seal.pdf", count), tmp_path, observations, "NG")
    assert observations == [(page, ordinal) for page in range(1, count + 1) for ordinal in (1, 2)]


def override(monkeypatch, transform):
    from audit.engine.checkers import ocr_visual_facts

    original = ocr_visual_facts.request_ocr_visual_facts

    def read(**request):
        facts = original(**request)
        page = int(re.search(r"'page': (\d+)", request["prompt"]).group(1))
        return {"seal": transform(page, current_ocr_observation(), facts["seal"])}

    monkeypatch.setattr(ocr_visual_facts, "request_ocr_visual_facts", read)


def test_an_unknown_page_prevents_absence_but_does_not_stop_the_scan(tmp_path, observations, monkeypatch):
    override(monkeypatch, lambda page, ordinal, value: None if page == 2 else value)
    check(pdf(tmp_path / "unknown.pdf", 5), tmp_path, observations, "UNABLE")
    assert observations == [(page, ordinal) for page in range(1, 6) for ordinal in (1, 2)]


def test_later_confirmed_witness_decides_exists_after_unknown(tmp_path, observations, monkeypatch):
    override(monkeypatch, lambda page, ordinal, value: None if page == 2 else value)
    check(pdf(tmp_path / "later-witness.pdf", 16, marked={12}), tmp_path, observations, "PASS")
    assert observations[-2:] == [(12, 1), (12, 2)]
    assert len(observations) == 24


def test_disagreement_stays_unknown_after_all_other_pages_are_negative(tmp_path, observations, monkeypatch):
    override(monkeypatch, lambda page, ordinal, value: False if page == 2 and ordinal == 2 else value)
    check(pdf(tmp_path / "disagreed.pdf", 4, marked={2}), tmp_path, observations, "UNABLE")
    assert len(observations) == 8


def test_an_independent_page_witness_can_resolve_a_previous_disagreement(tmp_path, observations, monkeypatch):
    override(monkeypatch, lambda page, ordinal, value: False if page == 2 and ordinal == 2 else value)
    check(pdf(tmp_path / "other-witness.pdf", 16, marked={2, 5}), tmp_path, observations, "PASS")
    assert observations[-2:] == [(5, 1), (5, 2)]
    assert len(observations) == 10


@pytest.mark.parametrize("failed_ordinal", [1, 2])
def test_technical_failure_stops_scanning_without_retry(tmp_path, observations, monkeypatch, failed_ordinal):
    def fail(page, ordinal, value):
        if page == 2 and ordinal == failed_ordinal:
            raise RuntimeError("controlled transport failure")
        return value

    override(monkeypatch, fail)
    check(pdf(tmp_path / "failed.pdf", 16, marked={12}), tmp_path, observations, "ERR")
    assert observations == [(1, 1), (1, 2), *[(2, ordinal) for ordinal in range(1, failed_ordinal + 1)]]
