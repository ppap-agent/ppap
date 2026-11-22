"""A UAES warrant is read once into one schema (a scan by the image model, an Excel form from its cells); every clause decides in code."""

import json
from io import BytesIO
from pathlib import Path

import fitz
import pytest
from audit.checker_lib.uaes import warrant
from audit.checker_lib.uaes.warrant import UaesWarrantAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body


def _field(value):
    return {"present": True, "value": value}


def _choice(*marks):
    return {"present": True, "options": [{"label": label, "checked": checked} for label, checked in marks]}


def _transcription(**changes):
    facts = {name: _field("X") for name in warrant.resources.IDENTITY}
    facts.update(
        safety_government_regulation=_choice(("Yes", False), ("No", True)),
        organization_area={"present": True},
        supplier_name=_field("Swoboda Technologies CZ"),
        customer_area={"present": True},
        customer_name_division=_field("Robert Bosch GmbH 6580"),
        buyer_code=_field("Frentz, Andreas"),
        application=_field(""),
        substances_of_concern_reported=_choice(("Yes", False), ("No", False), ("n/a", True)),
        imds_submission=_field("522944908"),
        supplier_authorized_signature={"present": True, "handwritten": True, "value": "Mokry, Martin"},
        reason_for_submission=_choice(("Initial submission", False), ("Change in part processing", True), ("Other - please specify", False)),
        reason_other_text=_field(""),
        requested_submission_level=_choice(("Level 3", False), ("Level 4", True)),
        ink_seal=False,
    )
    facts.update(changes)
    return facts


@pytest.fixture
def scanned(tmp_path, monkeypatch):
    path = tmp_path / "psw.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_image(page.rect, pixmap=fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 80), 1))
        document.save(path)
    calls = []

    def build(facts):
        def request(**kwargs):
            calls.append(kwargs["response_schema"]["name"])
            return facts

        monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", request)
        rule = RuleInput.of(preset_rule_body("UAES", "U17"), rule_id="u17")
        return UaesWarrantAgent(AuditInput(audit_id="psw", file_url=str(path)), rule)

    return build, calls


def test_one_transcription_decides_every_clause(scanned):
    build, calls = scanned
    checker = build(_transcription())
    results = [checker.check(index) for index in range(5)]
    assert [result.status for result in results] == ["PASS", "NG", "NG", "PASS", "PASS"]
    assert "应用（Application）为空" in results[1].suggestions
    assert "未找到印章" in results[2].suggestions
    assert calls == ["transcription"]
    assert all(result.evidence and result.evidence.sources for result in (results[0], results[1], results[3], results[4]))
    # The one transcription serves every clause; each pass still counts the facts it read, or the runtime refuses it.
    assert all(result.checks_performed for result in results if result.status == "PASS")


def test_an_unreadable_value_is_unable_but_a_known_violation_still_fails(scanned):
    build, _calls = scanned
    checker = build(_transcription(part_name=_field(None), level=None, requested_submission_level=_choice(("Level 3", True), ("Level 4", True))))
    assert checker.check(0).status == "UNABLE"
    assert checker.check(4).status == "NG"


def test_a_seen_seal_with_a_handwritten_signature_passes(scanned):
    build, _calls = scanned
    result = build(_transcription(ink_seal=True)).check(2)
    assert result.status == "PASS" and result.evidence and result.evidence.sources
    assert build(_transcription(ink_seal=True, supplier_authorized_signature={"present": True, "handwritten": False, "value": "pki, BOSCH"})).check(2).status == "NG"


def test_an_unreadable_seal_is_left_for_review(scanned):
    build, _calls = scanned
    assert build(_transcription(ink_seal=None)).check(2).status == "UNABLE"


def test_other_reason_needs_its_explanation(scanned):
    build, _calls = scanned
    other = _choice(("Initial submission", False), ("Other - please specify", True))
    assert build(_transcription(reason_for_submission=other)).check(3).status == "NG"
    assert build(_transcription(reason_for_submission=other, reason_other_text=_field("cavity 3"))).check(3).status == "PASS"


_SAMPLES = Path(__file__).resolve().parents[3] / "assets/uaes/samples/F01RD0YA40_机加工壳体"
_WARRANT = _SAMPLES / "测试文件/16 零件提交保证书/F-PU-206_零件提交保证书_PartSubmissionWarrant_V03.xls"
_DEFECT = _SAMPLES / "缺陷样本(-test-)/16 零件提交保证书/F-PU-206_零件提交保证书_PartSubmissionWarrant_V03 -test-零件名称无、零件号错误、无签字盖章.xls"


@pytest.fixture
def workbook(monkeypatch):
    """The F-PU-206 Excel warrants; the image model is asked only what each embedded picture shows."""
    from PIL import Image

    asked = []
    # Stand-in answers by picture size: the red seal, the handwritten signature, the header logo.
    answers = {(348, 324): (False, True), (100, 106): (True, False), (109, 40): (False, False)}

    def request(**kwargs):
        size = Image.open(BytesIO(kwargs["images"][0])).size
        asked.append(size)
        signed, seal = answers.get(size, (None, None))
        return {"handwritten_signature": signed, "ink_seal": seal}

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", request)

    def build(path):
        rule = RuleInput.of(preset_rule_body("UAES", "U17"), rule_id="u17")
        return UaesWarrantAgent(AuditInput(audit_id="psw", file_url=str(path)), rule)

    return build, asked, answers


def _cells(result):
    return [json.loads(source.locator).get("cell") or json.loads(source.locator).get("start") for source in result.evidence.sources]


def test_an_excel_warrant_is_read_from_its_cells_checkboxes_and_pictures(workbook):
    build, asked, _answers = workbook
    checker = build(_WARRANT)
    first, second = checker.check(0), checker.check(1)
    # "N/A" is written content: the clause fails only an empty field.
    assert first.status == "PASS" and {"E4", "P4", "G6", "P6", "H8", "R8", "H10", "R10", "B12"} <= set(_cells(first))
    assert second.status == "PASS" and {"B18", "M18", "M21", "M24", "L31"} <= set(_cells(second))
    assert asked == []
    third = checker.check(2)
    assert third.status == "PASS" and {"I62", "K57"} <= set(_cells(third))
    assert [result.status for result in (checker.check(3), checker.check(4))] == ["PASS", "PASS"]
    assert sorted(asked) == sorted({(348, 324), (100, 106), (109, 40)})


def test_the_defect_warrant_fails_its_blank_part_name_and_missing_signature(workbook):
    build, asked, _answers = workbook
    results = [build(_DEFECT).check(index) for index in range(5)]
    assert [result.status for result in results] == ["NG", "PASS", "NG", "PASS", "PASS"]
    assert "零件名称（Part Name）为空" in results[0].suggestions
    assert "没有手写签名" in results[2].suggestions and _cells(results[2]) == ["B63"]
    # Nothing is placed on its signature line, so no picture is asked about.
    assert asked == []


def test_a_picture_the_model_cannot_make_out_is_left_for_review(workbook):
    build, _asked, answers = workbook
    answers[(100, 106)] = (None, False)
    assert build(_WARRANT).check(2).status == "UNABLE"
