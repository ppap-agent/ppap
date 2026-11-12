"""A controlled stamp on a PDF original fails only when neither the image model nor Paddle on every page finds one."""

import fitz
import pytest
from audit.checker_lib.uaes.engineering import UaesProcessFlowAgent
from audit.checker_lib.uaes.native import UaesSourceAgent
from audit.contracts import AuditInput, RuleInput
from audit.engine.rule_engine import GuardStop
from audit.presets import preset_rule_body


@pytest.mark.parametrize(
    ("image", "seals", "expected"),
    [
        (("UNABLE", "受控章尚未核实"), [], "NG"),
        (("NG", "原件没有受控章。"), [], "NG"),
        (("UNABLE", "受控章尚未核实"), [["受控"]], "PASS"),
        (("UNABLE", "受控章尚未核实"), [["质量专用章"]], "UNABLE"),
        (("UNABLE", "受控章尚未核实"), None, "UNABLE"),
        (("PASS", "已从原件核实受控章。"), [], "PASS"),
    ],
)
def test_neither_reader_finding_a_stamp_fails(tmp_path, monkeypatch, image, seals, expected):
    path = tmp_path / "flow.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((40, 40), "Process Flow")
        document.save(path)
    monkeypatch.setattr(UaesSourceAgent, "controlled_stamp", lambda self: image)
    monkeypatch.setattr(UaesProcessFlowAgent, "paddle_seals", lambda self: seals)
    body = preset_rule_body("UAES", "U16")
    agent = UaesProcessFlowAgent(AuditInput(audit_id="flow", file_url=str(path)), RuleInput(rule_id="u16", **{key: value for key, value in body.items() if key != "kind"}))
    try:
        status = agent.stamp()[0]
    except GuardStop as stop:
        status = stop.status
    assert status == expected
