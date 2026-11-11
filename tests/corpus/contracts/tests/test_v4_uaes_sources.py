"""Original UAES assets are references, never golden correct reports."""

from pathlib import Path

from audit.checker_lib.uaes.inspection import UaesInspectionAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body


def test_original_uaes_inspection_reference_has_ten_not_three_measurement_columns():
    root = Path(__file__).resolve().parents[4]
    path = root / "assets/uaes/templates/F-PU-207_样品检验报告_Sample Inspection Report_V01-.xls (1).xls"
    body = preset_rule_body("UAES", "U01")
    agent = UaesInspectionAgent(AuditInput(audit_id="original-reference", file_url=str(path)), RuleInput(rule_id="source", **{key: value for key, value in body.items() if key != "kind"}))
    result = agent.check(0)
    # The actual offline reference has F:O =10. The rule document's remark
    # hands more than five columns to a reviewer; it must not become a PASS.
    assert result.status == "UNABLE", result.suggestions
    assert "10" in result.suggestions
    assert "多产线/多模穴" in result.suggestions
    assert result.evidence.sources


def test_original_uaes_measurement_example_is_not_real_supplier_data():
    from audit.checker_lib.uaes.precheck_l1 import Facts, _mcr_records

    root = Path(__file__).resolve().parents[4]
    path = root / "assets/uaes/templates/F-PU-166_测量比较报告_Measure Comparison Report_V02.xls (1).xls"
    facts = Facts(AuditInput(audit_id="offline-reference", file_url=str(path)))
    observed = facts.observe("U02:M-06", lambda: _mcr_records(facts))
    assert observed["status"] == "defect"
    assert observed["code"] == "B04"
