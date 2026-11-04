"""Original field labels, values and unreadable formulas remain different facts."""

import pytest
from audit.checkers.v4.li_auto.tooling import LiAutoToolingAgent
from audit.contracts import AuditInput, RuleInput
from audit.precheck_facts import field_fact
from audit.presets import preset_rule_body
from openpyxl import Workbook


@pytest.mark.parametrize(
    ("label", "value", "status", "code", "critical"),
    [
        ("零件供应商名称", "真实供方", "satisfied", None, False),
        ("零件供应商名称", None, "defect", "W02", False),
        ("其他标签", "真实供方", "defect", "B05", True),
        ("零件供应商名称", "=1+1", "unknown", None, False),
    ],
)
def test_field_presence_empty_value_and_formula_are_not_collapsed(tmp_path, label, value, status, code, critical):
    book = Workbook()
    book.active.title = "模具验收报告"
    book.active.append([label, value])
    book.active.append(["提交日期", "2025-01-01"])
    path = tmp_path / "tooling.xlsx"
    book.save(path)
    book.close()
    agent = LiAutoToolingAgent(AuditInput(audit_id="frozen-field-version", file_url=str(path)), RuleInput(rule_id="source", **preset_rule_body("理想", "L10")))
    try:
        observed = field_fact(agent, "L10:S1", "模具验收头部字段区", ("零件供应商名称字段",))
        assert observed["status"] == status and observed["code"] == code
        assert observed["critical"] is critical and observed["certified"] is False
        assert observed["certainty"] == ("confirmed" if status == "defect" else "unknown")
        if status == "defect":
            assert observed["evidence"]["sources"]
    finally:
        agent.close()
