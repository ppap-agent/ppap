"""Shared numeric regressions through real workbooks and public V2/V3 checks."""

import pytest
from audit.checkers.v2.plant_capacity_evaluation_agent import PlantCapacityEvaluationAgent as V2CapacityAgent
from audit.checkers.v2.sub_supplier_list_excel_agent import SubSupplierListExcelAgent as V2SupplierAgent
from audit.checkers.v3.plant_capacity_evaluation_agent import PlantCapacityEvaluationAgent as V3CapacityAgent
from audit.checkers.v3.sub_supplier_list_excel_agent import SubSupplierListExcelAgent as V3SupplierAgent
from audit.contracts import AuditInput, RuleInput
from openpyxl import Workbook


def check_workbook(tmp_path, workbook, agent_type, profile, rule_number, metadata=None):
    path = tmp_path / "numeric-contract.xlsx"
    workbook.save(path)
    workbook.close()
    agent = agent_type(
        AuditInput(audit_id="numeric-contract", file_url=str(path), file_metadata=metadata or {}),
        RuleInput(
            rule_id="confirmed-numeric-rules",
            rule_profile=profile,
            type=agent_type.DOC_TYPE.get_type_name(),
            rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, rule_number + 1)],
        ),
    )
    try:
        result = agent.check(rule_number - 1)
        assert result is not None
        return result
    finally:
        agent.close()


@pytest.mark.parametrize(("profile", "agent_type"), [("v2", V2CapacityAgent), ("v3", V3CapacityAgent)], ids=["v2", "v3"])
@pytest.mark.parametrize(("reported_capacity", "expected"), [(93506, "PASS"), (93507, "PASS"), (93508, "NG")], ids=["floor", "ceiling", "above-ceiling"])
def test_monthly_capacity_accepts_floor_or_ceiling_and_reports_the_source_row(tmp_path, profile, agent_type, reported_capacity, expected):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "产能测试及评估"
    headers = ["线体/工序类别", "参与测试线体数量", "单线体/工序总产出JPH", "现有线体数", "班次/天", "每班净利用时间（h）", "每月标准工作天数", "累积良率", "线体/工序预估月度产能"]
    for column, header in enumerate(headers, start=1):
        sheet.cell(8, column, header)
    # Frozen D-003 example: calculated monthly capacity is 93506.4.
    for column, value in enumerate(["工序A", 2, 250, 2, 3, 2.4, 26, 0.999, reported_capacity], start=1):
        sheet.cell(9, column, value)

    result = check_workbook(tmp_path, workbook, agent_type, profile, 6)

    assert result.status == expected
    if expected == "PASS":
        assert result.suggestions == ""
    else:
        assert "第9行" in result.suggestions
        assert "预估月度产能与计算结果不一致" in result.suggestions


@pytest.mark.parametrize(("jph", "monthly", "expected"), [("118.4", "26507", "PASS"), ("118.0", "26507", "NG"), ("118.4", "27000", "NG")])
def test_pdf_display_precision_does_not_hide_inconsistent_capacity_inputs(tmp_path, jph, monthly, expected):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "table_产能测试及评估"
    sheet.append(
        [
            "线体/工序类别",
            "参与测试线体数量",
            "单线体/工序总产出JPH",
            "现有线体数",
            "班次/天",
            "每班净利用时间（h）",
            "每月标准工作天数",
            "累积良率",
            "线体/工序预估月度产能",
            "产出数量",
            "净利用时间（h）",
        ]
    )
    sheet.append(["SMT", "1", jph, "1", "1", "9.5", "26", "90.6%", monthly, "450", "3.8"])
    result = check_workbook(tmp_path, workbook, V2CapacityAgent, "v2", 6, {"source_pdf_url": "original.pdf"})
    assert result.status == expected


@pytest.mark.parametrize(("profile", "agent_type"), [("v2", V2SupplierAgent), ("v3", V3SupplierAgent)], ids=["v2", "v3"])
@pytest.mark.parametrize(
    ("weight", "number_format", "expected"),
    [(3.1897, "General", "PASS"), (3.189, "General", "NG"), (3.1, "0.0000", "PASS")],
    ids=["four-decimal-value", "three-decimal-value", "four-decimal-format"],
)
def test_supplier_weight_accepts_four_decimal_values_or_explicit_four_decimal_format(tmp_path, profile, agent_type, weight, number_format, expected):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "材料产品及分供方清单"
    sheet.append(["零件号", "单件重量"])
    sheet.append(["PART-001", weight])
    sheet["B2"].number_format = number_format

    result = check_workbook(tmp_path, workbook, agent_type, profile, 1)

    assert result.status == expected
    if expected == "PASS":
        assert result.suggestions == ""
    else:
        assert "单件重量必须是四位小数格式" in result.suggestions
