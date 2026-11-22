"""A controlled stamp on a PDF original is decided by the image model's fact, mapped in code.

A stamp that cannot be made out is never reported as absent. Only a stamp the model looked at and could not
make out is the original's "无法辨识": it fails and asks for a person where the clause says so (U16:3), and is
otherwise left to a person (U08:3, U10:1, U14:1). An unknown the program left (nothing asked, no model) is
left to a person on every clause. A failed model request stays a technical error.
"""

import fitz
import pytest
from audit.checker_lib.uaes.engineering import UaesProcessFlowAgent
from audit.checker_lib.uaes.native import UaesSourceAgent
from audit.checker_lib.uaes.records import UaesControlledDocumentAgent
from audit.contracts import AuditInput, RuleInput
from audit.engine.rule_engine import GuardStop
from audit.presets import preset_rule_body


@pytest.mark.parametrize(
    ("fact", "seen", "expected", "message"),
    [
        (True, False, "PASS", "已从原件核实受控章"),
        (False, False, "NG", "原件没有受控章"),
        (None, True, "NG", "原件上的受控章无法辨识，判不通过，请人工复核"),
        (None, False, "UNABLE", "检查程序未能核实受控章"),
    ],
    ids=["stamped", "absent", "unreadable", "not-looked-at"],
)
def test_the_image_model_fact_decides_a_pdf_stamp(tmp_path, monkeypatch, fact, seen, expected, message):
    from audit.engine.checkers.ocr_visual_facts import note_unreadable

    path = tmp_path / "flow.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((40, 40), "Process Flow")
        document.save(path)
    asked = []

    def visual_pdf(self, fields, **options):
        asked.append((tuple(fields), options))
        if seen:
            note_unreadable(fields)
        return {"controlled_stamp": fact}

    monkeypatch.setattr(UaesSourceAgent, "visual_pdf", visual_pdf)
    body = preset_rule_body("UAES", "U16")
    agent = UaesProcessFlowAgent(AuditInput(audit_id="flow", file_url=str(path)), RuleInput.of(body, rule_id="u16"))
    names = [name for name, _ in agent.declared_rules()]
    result = agent.check(names.index("rule_3"))
    assert result.status == expected
    assert message in result.suggestions
    assert asked == [(("controlled_stamp",), {"existential_fields": ("controlled_stamp",)})]


def test_an_unreadable_stamp_is_left_to_a_person_where_the_clause_does_not_fail_it(tmp_path, monkeypatch):
    path = tmp_path / "spec.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((40, 40), "Material Specification")
        document.save(path)
    from audit.engine.checkers.ocr_visual_facts import note_unreadable

    monkeypatch.setattr(UaesSourceAgent, "visual_pdf", lambda self, fields, **options: note_unreadable(fields) or {"controlled_stamp": None})
    body = preset_rule_body("UAES", "U10")
    agent = UaesControlledDocumentAgent(AuditInput(audit_id="spec", file_url=str(path)), RuleInput.of(body, rule_id="u10"))
    result = agent.check(0)
    assert result.status == "UNABLE"
    assert "原件上的受控章无法辨识，需人工复核" in result.suggestions


def test_a_failed_model_request_is_not_an_unreadable_stamp(tmp_path, monkeypatch):
    from documents.model_access import ModelUnavailable

    path = tmp_path / "flow.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((40, 40), "Process Flow")
        document.save(path)

    def visual_pdf(self, fields, **options):
        raise ModelUnavailable("model_request_failed")

    monkeypatch.setattr(UaesSourceAgent, "visual_pdf", visual_pdf)
    body = preset_rule_body("UAES", "U16")
    agent = UaesProcessFlowAgent(AuditInput(audit_id="flow", file_url=str(path)), RuleInput.of(body, rule_id="u16"))
    with pytest.raises(GuardStop) as stop:
        agent.stamp()
    assert stop.value.status == "ERR"


@pytest.mark.parametrize(("seen", "expected"), [(True, "NG"), (False, "UNABLE")], ids=["model-could-not-make-out", "not-looked-at"])
def test_an_image_fact_fails_only_when_the_original_itself_cannot_be_made_out(tmp_path, monkeypatch, seen, expected):
    # U16:1 says "无法辨识则不通过": that is the original being illegible, never the program not asking.
    from audit.engine.checkers.ocr_visual_facts import note_unreadable

    path = tmp_path / "flow.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((40, 40), "Process Flow")
        document.save(path)

    def visual_pdf(self, fields, **options):
        if seen:
            note_unreadable(fields)
        return dict.fromkeys(fields)

    monkeypatch.setattr(UaesSourceAgent, "visual_pdf", visual_pdf)
    body = preset_rule_body("UAES", "U16")
    agent = UaesProcessFlowAgent(AuditInput(audit_id="flow", file_url=str(path)), RuleInput.of(body, rule_id="u16"))
    names = [name for name, _ in agent.declared_rules()]
    result = agent.check(names.index("rule_1"))
    assert result.status == expected, result.suggestions
    if seen:
        assert "无法辨识" in result.suggestions and "人工复核" in result.suggestions
