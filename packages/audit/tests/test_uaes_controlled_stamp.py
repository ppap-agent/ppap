"""A controlled stamp on a PDF original is decided by the image model's fact, mapped in code.

A stamp that cannot be made out is never reported as absent: it fails and asks for a person only where
the clause says "无法辨识则不通过" (U16:3), and is otherwise left to a person (U08:3, U10:1, U14:1).
A failed model request stays a technical error.
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
    ("fact", "expected", "message"),
    [(True, "PASS", "已从原件核实受控章"), (False, "NG", "原件没有受控章"), (None, "NG", "无法辨识受控章，判不通过，请人工复核")],
    ids=["stamped", "absent", "unreadable"],
)
def test_the_image_model_fact_decides_a_pdf_stamp(tmp_path, monkeypatch, fact, expected, message):
    path = tmp_path / "flow.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((40, 40), "Process Flow")
        document.save(path)
    asked = []

    def visual_pdf(self, fields, **options):
        asked.append((tuple(fields), options))
        return {"controlled_stamp": fact}

    monkeypatch.setattr(UaesSourceAgent, "visual_pdf", visual_pdf)
    body = preset_rule_body("UAES", "U16")
    agent = UaesProcessFlowAgent(AuditInput(audit_id="flow", file_url=str(path)), RuleInput(rule_id="u16", **{key: value for key, value in body.items() if key != "kind"}))
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
    monkeypatch.setattr(UaesSourceAgent, "visual_pdf", lambda self, fields, **options: {"controlled_stamp": None})
    body = preset_rule_body("UAES", "U10")
    agent = UaesControlledDocumentAgent(AuditInput(audit_id="spec", file_url=str(path)), RuleInput(rule_id="u10", **{key: value for key, value in body.items() if key != "kind"}))
    result = agent.check(0)
    assert result.status == "UNABLE"
    assert "无法辨识受控章" in result.suggestions


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
    agent = UaesProcessFlowAgent(AuditInput(audit_id="flow", file_url=str(path)), RuleInput(rule_id="u16", **{key: value for key, value in body.items() if key != "kind"}))
    with pytest.raises(GuardStop) as stop:
        agent.stamp()
    assert stop.value.status == "ERR"
