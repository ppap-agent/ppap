"""A controlled stamp on a PDF original is decided by the image model's fact, mapped in code.

UAES rules are fail-closed: a stamp that cannot be made out fails and asks for a person, but is not
reported as absent; a failed model request stays a technical error.
"""

import fitz
import pytest
from audit.checker_lib.uaes.engineering import UaesProcessFlowAgent
from audit.checker_lib.uaes.native import UaesSourceAgent
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
    try:
        status, suggestion = agent.stamp()
    except GuardStop as stop:
        status, suggestion = stop.status, str(stop)
    assert status == expected
    assert message in suggestion
    assert asked == [(("controlled_stamp",), {"existential_fields": ("controlled_stamp",)})]


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
