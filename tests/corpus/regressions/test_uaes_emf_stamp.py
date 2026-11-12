"""A UAES flow chart original whose controlled stamp is an EMF picture on the sheet."""

from pathlib import Path

import pytest
from audit.presets import preset_rule_body


def test_an_emf_stamp_placed_on_the_sheet_is_the_controlled_stamp(monkeypatch, request):
    if request.config.getoption("--ftype", default=None) not in (None, "excel"):
        pytest.skip("Original workbook regression")
    from audit.checker_lib.uaes.engineering import UaesProcessFlowAgent
    from audit.checker_lib.uaes.native import UaesSourceAgent
    from audit.contracts import AuditInput, RuleInput

    path = next((Path(__file__).parents[3] / "assets/uaes/samples").glob("F01R10Y776*/**/流程图776.xlsx"))
    monkeypatch.setattr(UaesSourceAgent, "visual_pdf", lambda *args, **kwargs: pytest.fail("no image is needed"))
    body = preset_rule_body("UAES", "U16")
    agent = UaesProcessFlowAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="source-rules", **{key: value for key, value in body.items() if key != "kind"}))
    assert agent.check(2).status == "PASS"
