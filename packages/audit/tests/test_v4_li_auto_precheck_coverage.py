"""Every shipped Li Auto L1 source reaches a predicate or an explicit scope state."""

import pytest
from audit.checkers.v4.li_auto import EXCEL_AGENTS
from audit.contracts import AuditInput, RuleInput
from audit.precheck_sources import l1_criteria
from audit.presets import preset_rule_body
from openpyxl import Workbook


@pytest.mark.parametrize("entry", ["L01", "L02", "L03", "L04", "L05", "L06"])
def test_each_l1_source_reports_its_actual_missing_facts_instead_of_an_unimplemented_placeholder(tmp_path, entry):
    path = tmp_path / "incomplete-original.xlsx"
    book = Workbook()
    book.active.title = "报告"
    book.active.append(["零件号", "P1"])
    book.save(path)
    book.close()
    agent = EXCEL_AGENTS[entry](AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="rule", **preset_rule_body("理想", entry)))
    try:
        facts = agent.precheck()
        assert {row["id"] for row in facts} == {row["id"] for row in l1_criteria(entry)}
        assert not [row for row in facts if "尚未自动执行" in row["message"]], facts
        assert not [row for row in facts if row["status"] == "error"], facts
        assert all(row["certified"] is False for row in facts)
    finally:
        agent.close()
