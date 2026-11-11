"""Rule-engine row and footer resolution that li/nio clauses depend on."""

from pathlib import Path

import pytest
from audit.checker_lib.li.annual_test_requirements_agent import AnnualTestRequirementsAgent as LiAnnual
from audit.checker_lib.li.control_plan_agent import ControlPlanAgent as LiControlPlan
from audit.checker_lib.li.equipment_list_agent import EquipmentListAgent as LiEquipment
from audit.checker_lib.nio.annual_test_requirements_agent import AnnualTestRequirementsAgent as NioAnnual
from audit.checker_lib.nio.equipment_list_agent import EquipmentListAgent as NioEquipment
from audit.contracts import AuditInput, RuleInput
from openpyxl import Workbook


def _status(tmp_path: Path, agent_cls, workbook: Workbook, rule_number: int, *, profile: str) -> str:
    path = tmp_path / "input.xlsx"
    workbook.save(path)
    workbook.close()
    agent = agent_cls(
        AuditInput(audit_id="rule-engine", file_url=str(path)),
        RuleInput(
            rule_id="rule-engine",
            rule_profile=profile,
            type=agent_cls.DOC_TYPE.get_type_name(),
            rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, 9)],
        ),
    )
    try:
        result = agent.check(rule_number - 1)
        assert result is not None
        return result.status
    finally:
        agent.close()


def _control_plan(special_row_3: str, frequency_row_3: str | None) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "控制计划"
    sheet.append(["工序号", "工序名称", "产品", "过程", "特殊特性分类", "控制方法", "频率"])
    sheet.append(["OP10", "装配", "外观", None, "NA", "目视", "每班"])
    sheet.append([None, None, "尺寸", None, special_row_3, "卡尺", frequency_row_3])
    sheet.append([None, None, "扭矩", None, "NA", "扭矩枪", "每班"])
    sheet.merge_cells("A2:A4")
    sheet.merge_cells("B2:B4")
    return workbook


@pytest.mark.parametrize(
    ("rule_number", "special", "frequency", "expected"),
    [
        (2, "NA", "每班", "PASS"),
        (2, "关键", "每班", "NG"),
        (5, "NA", "每班", "PASS"),
        (5, "NA", None, "NG"),
    ],
)
def test_li_control_plan_checks_every_row_of_a_merged_process_block(tmp_path, rule_number, special, frequency, expected):
    assert _status(tmp_path, LiControlPlan, _control_plan(special, frequency), rule_number, profile="v2") == expected


def _equipment_list(row_7: dict[str, object]) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "设备开发验收清单"
    sheet["A3"] = "设备开发"
    sheet["J3"] = "设备验收"
    headers = ["序号", "产线/工序", "设备", "供应商", "设备型号", "利旧设备数量", "新采购设备数量", "设备通用情况", "计划到位时间", "实际到位时间", "验收状态", "验收结果", "整改措施", "整改计划"]
    sheet.append([])
    sheet.append(headers)
    complete = ["1", "焊接", "焊机", "甲公司", "W1", 1, 0, "专用", "2025-10-01", "2025-10-01", "已就位", "通过", None, None]
    sheet.append(complete)
    sheet.append([row_7.get(name, value) for name, value in zip(headers, ["2", *complete[1:]])])
    sheet.append(["编制/日期"])
    return workbook


@pytest.mark.parametrize("agent_cls,profile", [(LiEquipment, "v2"), (NioEquipment, "v3")])
@pytest.mark.parametrize(
    ("rule_number", "row_7", "expected"),
    [
        (1, {}, "PASS"),
        (1, {"设备": None}, "NG"),
        (2, {"设备": None}, "PASS"),
        (2, {"设备": None, "验收结果": None}, "NG"),
    ],
)
def test_equipment_rows_are_found_by_the_line_process_trigger(tmp_path, agent_cls, profile, rule_number, row_7, expected):
    assert _status(tmp_path, agent_cls, _equipment_list(row_7), rule_number, profile=profile) == expected


def test_equipment_rows_under_a_merged_line_process_are_each_checked(tmp_path):
    workbook = _equipment_list({"产线/工序": None, "供应商": None})
    workbook.active.merge_cells("B6:B7")

    assert _status(tmp_path, LiEquipment, workbook, 1, profile="v2") == "NG"


def _annual_tests(row_11: dict[str, object]) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "表格"
    sheet.append(["试验要求栏"])
    headers = ["序号", "试验项目", "试验要求", "试验标准/方法", "样本数量", "测试频率"]
    sheet.append(headers)
    sheet.append(["1", "ELV试验", "满足要求", "GB 1", "1", "每两年"])
    sheet.append([row_11.get(name, value) for name, value in zip(headers, ["2", "VOC", "满足要求", "GB 2", "1", "每年"])])
    sheet.append(["3", "气味", "满足要求", "GB 3", "1", "每年"])
    return workbook


@pytest.mark.parametrize("agent_cls,profile", [(LiAnnual, "v2"), (NioAnnual, "v3")])
@pytest.mark.parametrize(
    ("rule_number", "row_11", "expected"),
    [
        (2, {}, "PASS"),
        (2, {"测试频率": None}, "NG"),
        (5, {}, "PASS"),
        (5, {"试验项目": None}, "NG"),
    ],
)
def test_annual_test_rows_are_not_cut_off_at_a_blank_required_cell(tmp_path, agent_cls, profile, rule_number, row_11, expected):
    assert _status(tmp_path, agent_cls, _annual_tests(row_11), rule_number, profile=profile) == expected
