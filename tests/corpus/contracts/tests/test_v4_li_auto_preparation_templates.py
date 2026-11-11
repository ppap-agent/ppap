"""Preparation checks exercised against original Li Auto offline templates."""

from types import SimpleNamespace

import pytest
from audit.checkers.v4.li_auto import EXCEL_AGENTS
from audit.checkers.v4.li_auto.precheck_inspection_validation import operations
from audit.checkers.v4.li_auto.precheck_l1 import L1Facts
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body


def result(audit, entry, criterion, *, group=None, agent=None):
    facts = L1Facts(audit)
    agent = agent or SimpleNamespace(audit_file=audit, group_context=group)
    return facts.observe(entry + ":" + criterion, operations(agent, entry, facts)[criterion])


@pytest.mark.parametrize(
    ("entry", "name"),
    [("L09", "检具认可"), ("L10", "模具验收"), ("L11", "过程流程图"), ("L12", "产品过程CCSC"), ("L13", "设备开发"), ("L14", "产能评估"), ("L15", "项目问题"), ("L16", "年度试验"), ("L17", "零件提交")],
)
def test_each_l2_source_subcriterion_runs_on_actual_customer_template(entry, name):
    from pathlib import Path

    from audit.precheck_sources import required_structure

    root = Path(__file__).resolve().parents[4] / "assets/理想/模板"
    path = next(path for path in root.iterdir() if name in path.name)
    agent = EXCEL_AGENTS[entry](AuditInput(audit_id="template-version", file_url=str(path)), RuleInput(rule_id="r", **preset_rule_body("理想", entry)))
    try:
        facts = agent.precheck()
        assert {row["id"] for row in facts} == {row["id"] for row in required_structure(entry)}
        assert not [row for row in facts if row["status"] == "error"]
        assert all(row["certified"] is False for row in facts)
        assert all(row["evidence"] and row["evidence"]["sources"] for row in facts if row["status"] == "satisfied")
    finally:
        agent.close()


def test_blank_fixture_template_labels_and_unchecked_options_are_not_content():
    from pathlib import Path

    path = Path(__file__).resolve().parents[4] / "assets/理想/模板/APQP-检具认可报告.xlsx"
    agent = EXCEL_AGENTS["L09"](AuditInput(audit_id="blank-fixture", file_url=str(path)), RuleInput(rule_id="r", **preset_rule_body("理想", "L09")))
    try:
        facts = {row["id"]: row for row in agent.precheck()}
        assert facts["L09:S3"]["status"] == "defect"
    finally:
        agent.close()


def test_source_dvp_two_tier_timeline_and_blank_actuals(tmp_path):
    from pathlib import Path

    from openpyxl import load_workbook

    source = Path(__file__).resolve().parents[4] / "assets/理想/模板/APQP-零部件设计验证计划.xlsx"
    workbook = load_workbook(source)
    sheet = workbook["DVP&R"]
    sheet["M10"] = "2024-05-01"
    sheet["N10"] = "2024-10-10"
    path = tmp_path / "dvp.xlsx"
    workbook.save(path)
    audit = AuditInput(audit_id="native", file_url=str(path), customer="理想")
    observed = result(audit, "L02", "CON-10")
    assert (observed["status"], observed["code"]) == ("defect", "W06"), observed
    sheet["M10"] = None
    sheet["N10"] = None
    workbook.save(path)
    assert result(audit, "L02", "CON-10")["status"] == "satisfied"
