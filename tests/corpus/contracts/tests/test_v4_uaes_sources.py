"""Original UAES assets are references, never golden correct reports."""

from pathlib import Path

from audit.checker_lib.uaes.inspection import UaesInspectionAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body


def test_original_uaes_inspection_reference_without_supplier_readings_is_not_a_pass():
    root = Path(__file__).resolve().parents[4]
    path = root / "assets/uaes/templates/F-PU-207_样品检验报告_Sample Inspection Report_V01-.xls (1).xls"
    body = preset_rule_body("UAES", "U01")
    agent = UaesInspectionAgent(AuditInput(audit_id="original-reference", file_url=str(path)), RuleInput(rule_id="source", **{key: value for key, value in body.items() if key != "kind"}))
    result = agent.check(0)
    # The blank reference heads ten columns F:O but holds no reading: a measured
    # column is one with readings, so the empty template is not a PASS.
    assert result.status == "NG", result.suggestions
    assert "没有任何实测值" in result.suggestions
