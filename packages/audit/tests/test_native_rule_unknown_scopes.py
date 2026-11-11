"""Fixed native predicates read their own source cells, not unrelated errors."""

import pytest
from audit.checker_lib.li.msa_report_agent import MsaReportAgent as V2Msa
from audit.checker_lib.nio.msa_report_agent import MsaReportAgent as V3Msa
from audit.checker_lib.nio.process_flow_chart_excel_agent import ProcessFlowChartExcelAgent
from audit.contracts import AuditInput, RuleInput
from openpyxl import Workbook


@pytest.mark.parametrize("cls", [V2Msa, V3Msa], ids=["v2", "v3"])
@pytest.mark.parametrize("marker,metric,expected", [(None, "#DIV/0!", "NG"), ("√", "#DIV/0!", "PASS"), ("#N/A", 0.12, "UNABLE"), (None, "√", "NG")])
def test_msa_choice_does_not_read_an_unrelated_metric(tmp_path, cls, marker, metric, expected):
    path = tmp_path / "msa.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "量具重复性和再现性R&R分析报告"
    sheet["G56"] = "%R&R<10%:该量具可接受。"
    sheet["F56"] = marker
    sheet["D56"] = metric
    book.save(path)
    book.close()
    agent = cls(AuditInput(audit_id="frozen", file_url=str(path)), RuleInput(rule_id="confirmed", type="测量系统分析报告", rules=[]))
    try:
        result = agent.check(3)
    finally:
        agent.close()
    assert result.status == expected


@pytest.mark.parametrize("value,span,expected", [(None, "M3:P3", "NG"), ("#N/A", "M3:P3", "UNABLE"), ("2025.01.05", "M3:P3", "UNABLE"), (None, "M3:Q3", "UNABLE")])
def test_pfd_known_empty_revision_can_decide_without_unknown_other_fields(tmp_path, value, span, expected):
    path = tmp_path / "pfd.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "过程流程图"
    sheet.merge_cells("M2:P2")
    sheet["M2"] = "修订日期Date(Rev.)"
    sheet.merge_cells(span)
    sheet["M3"] = value
    sheet["B20"] = "#N/A"
    book.save(path)
    book.close()
    agent = ProcessFlowChartExcelAgent(AuditInput(audit_id="frozen", file_url=str(path)), RuleInput(rule_id="confirmed", type="过程流程图", rules=[]))
    try:
        result = agent.check(0)
    finally:
        agent.close()
    assert result.status == expected
    if expected == "NG":
        assert "修订日期" in result.suggestions
