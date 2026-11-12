"""The scanned UAES warrant is transcribed once; every clause decides from the transcription in code."""

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

    def build(facts, seal=None):
        def request(**kwargs):
            calls.append(kwargs["response_schema"]["name"])
            return facts

        monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", request)
        monkeypatch.setattr("audit.engine.checkers.visual_facts._read_paddle_visual_facts", lambda *args, **kwargs: {"seal": seal})
        rule = RuleInput(rule_id="u17", **preset_rule_body("UAES", "U17"))
        return UaesWarrantAgent(AuditInput(audit_id="psw", file_url=str(path)), rule)

    return build, calls


def test_one_transcription_decides_every_clause(scanned):
    build, calls = scanned
    checker = build(_transcription())
    results = [checker.check(index) for index in range(5)]
    assert [result.status for result in results] == ["PASS", "NG", "UNABLE", "PASS", "PASS"]
    assert "应用（Application）为空" in results[1].suggestions
    assert "印章" in results[2].suggestions
    assert calls == ["psw"]
    assert all(result.evidence and result.evidence.sources for result in (results[0], results[1], results[3], results[4]))
    # The one transcription serves every clause; each pass still counts the facts it read, or the runtime refuses it.
    assert all(result.checks_performed for result in results if result.status == "PASS")


def test_an_unreadable_value_is_unable_but_a_known_violation_still_fails(scanned):
    build, _calls = scanned
    checker = build(_transcription(part_name=_field(None), level=None, requested_submission_level=_choice(("Level 3", True), ("Level 4", True))))
    assert checker.check(0).status == "UNABLE"
    assert checker.check(4).status == "NG"


def test_a_located_seal_with_a_handwritten_signature_passes(scanned):
    build, _calls = scanned
    assert build(_transcription(), seal=True).check(2).status == "PASS"
    assert build(_transcription(supplier_authorized_signature={"present": True, "handwritten": False, "value": "pki, BOSCH"}), seal=True).check(2).status == "NG"


def test_other_reason_needs_its_explanation(scanned):
    build, _calls = scanned
    other = _choice(("Initial submission", False), ("Other - please specify", True))
    assert build(_transcription(reason_for_submission=other)).check(3).status == "NG"
    assert build(_transcription(reason_for_submission=other, reason_other_text=_field("cavity 3"))).check(3).status == "PASS"
