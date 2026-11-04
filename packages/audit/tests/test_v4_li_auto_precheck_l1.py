"""Preparation facts use the L1 source contract, not formal engineering NG."""

import pytest
from audit.checkers.v4.li_auto import EXCEL_AGENTS
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook


def run(tmp_path, entry, rows, title="报告"):
    book = Workbook()
    sheet = book.active
    sheet.title = title
    for row in rows:
        sheet.append(row)
    path = tmp_path / "资料.xlsx"
    book.save(path)
    book.close()
    agent = EXCEL_AGENTS[entry](AuditInput(audit_id="original-version", file_url=str(path)), RuleInput(rule_id="r", **preset_rule_body("理想", entry)))
    try:
        return {row["id"].split(":", 1)[1]: row for row in agent.precheck()}
    finally:
        agent.close()


@pytest.mark.parametrize(("stage", "expected"), [("PV 测试", "satisfied"), ("DV 试验", "satisfied"), ("PV 阶段", "satisfied"), ("空腔验证", "defect")])
def test_report_preparation_accepts_explicit_stage_aliases_while_formal_is_literal(tmp_path, stage, expected):
    facts = run(tmp_path, "L03", [["试验类型", stage]])
    assert facts["COR-06"]["status"] == expected
    assert not facts["COR-06"]["certified"]


def test_equivalent_stage_aliases_do_not_create_a_false_conflict(tmp_path):
    facts = run(tmp_path, "L03", [["试验类型", "PV"], ["试验阶段", "PV 测试"]])
    assert facts["CON-11"]["status"] == "satisfied"
    facts = run(tmp_path, "L03", [["试验类型", "PV"], ["试验阶段", "DV"]])
    assert facts["CON-11"]["status"] == "defect"


@pytest.mark.parametrize(("last", "expected"), [("2026-02-01", "satisfied"), ("2025-02-01", "defect"), ("=1+1", "unknown")])
def test_plan_date_precheck_checks_order_but_keeps_unread_formula_unknown(tmp_path, last, expected):
    facts = run(tmp_path, "L02", [["计划开始", "计划结束"], ["2026-01-01", last]])
    assert facts["COR-01"]["status"] == expected


def test_cpk_prep_does_not_apply_the_formal_threshold_or_reject_legal_na(tmp_path):
    rows = [["工序流程编号", "特性名称", "P阶段Cpk结果", "P阶段结果判定"], ["10", "孔径", 1.1, "未达标"]]
    facts = run(tmp_path, "L06", rows, "CCSC")
    assert facts["PRE-10"]["status"] == "satisfied"
    rows[1][2] = "NA"
    facts = run(tmp_path, "L06", rows, "CCSC")
    assert facts["PRE-09"]["status"] == "satisfied"
    rows[1][2] = "N/A"
    facts = run(tmp_path, "L06", rows, "CCSC")
    assert facts["PRE-09"]["status"] == "defect"
    assert facts["PRE-09"]["code"] == "W03"


@pytest.mark.parametrize(("weight", "expected"), [("10.72", "satisfied"), ("10.72g", "defect"), ("约12", "defect"), ("=1+1", "unknown")])
def test_material_preparation_requires_a_single_numeric_weight(tmp_path, weight, expected):
    facts = run(tmp_path, "L04", [["零件号", "单件重量"], ["P123", weight]], "清单")
    assert facts["PC-C3"]["status"] == expected


def test_inspection_customer_switches_are_not_uaes_signature_or_bubble_requirements(tmp_path):
    facts = run(tmp_path, "L01", [["序号", "检查项目", "判定标准", "供方检查方法/检查工具", "检查数据", None, "判定结果"], ["尺寸"], [1, "直径", "10±0.5", "卡尺", 10, 10.1, "OK"]], "尺寸检验报告")
    assert facts["IR-13"]["code"] == "S02"
    assert facts["IR-15"]["code"] == "S02"
    assert facts["IR-09"]["status"] == "handoff"
    assert facts["IR-17"]["status"] == "defect"
    assert facts["IR-08"]["status"] == "satisfied"


def test_inspection_declared_three_samples_override_the_default_five(tmp_path):
    rows = [["应测样本量", 3], ["序号", "检查项目", "判定标准", "供方检查方法/检查工具", "检查数据", None, None, "判定结果"], ["尺寸"], [1, "直径", "10±0.5", "卡尺", 10, 10.1, 10.2, "OK"]]
    assert run(tmp_path, "L01", rows, "尺寸检验报告")["IR-17"]["status"] == "satisfied"
