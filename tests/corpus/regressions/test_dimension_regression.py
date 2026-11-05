import pytest
from corpus_contracts.oracle import original


def test_giant_measurement_failure_names_an_actual_out_of_tolerance_record(request):
    if request.config.getoption("--ftype", default=None) not in (None, "pdf"):
        pytest.skip("Original PDF regression")
    from audit.checkers.v2.dimension_measurement_pdf_agent import DimensionMeasurementPDFAgent
    from audit.contracts import AuditInput, RuleInput

    source = original("1-全功能检验报告_巨一.pdf")
    rules = RuleInput(
        rule_id="reviewed-v2-rules",
        rule_profile="v2",
        type=DimensionMeasurementPDFAgent.DOC_TYPE.get_type_name(),
        rules=[{"id": str(number), "category": "原件检查", "rule_to_check": f"规则{number}"} for number in range(1, 9)],
    )
    agent = DimensionMeasurementPDFAgent(AuditInput(audit_id="giant-measurement", file_url=str(source)), rules)
    try:
        result = agent.check(2)
        assert result.status == "NG"
        assert "原PDF第4页序号103" in result.suggestions
        assert "14.05超出公差范围(14.1, 14.2)" in result.suggestions
        assert "非数值" not in result.suggestions
    finally:
        agent.close()
