from pathlib import Path

import pytest
from openpyxl import Workbook
from ppap_core.contracts import AuditInput, AuditResultItem, RuleInput
from ppap_excel_audit_engine.checkers.agents_v3_overlay.annual_test_requirements_agent import AnnualTestRequirementsAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.dimension_measurement_excel_agent import DimensionMeasurementExcelAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.dvp_r_excel_agent import DvpRExcelAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.equipment_list_agent import EquipmentListAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.initial_process_capacity_agent import InitialProcessCapacityAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.sub_supplier_list_excel_agent import SubSupplierListExcelAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.xir_assembly_issue_agent import XirAssemblyIssueAgent


def check_workbook(tmp_path: Path, agent_class, workbook: Workbook, rule_number: int) -> AuditResultItem:
    path = tmp_path / "target.xlsx"
    workbook.save(path)
    workbook.close()
    agent = agent_class(
        AuditInput(audit_id="target", file_url=str(path)),
        RuleInput(
            rule_id="confirmed-document-rules",
            rule_profile="v3",
            type=agent_class.DOC_TYPE.get_type_name(),
            rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, rule_number + 1)],
        ),
    )
    try:
        result = agent.check(rule_number - 1)
        assert result is not None
        return result
    finally:
        agent.close()


def test_sub_supplier_requires_a_part_number_anchor(tmp_path):
    workbook = Workbook()
    workbook.active.title = "清单"
    workbook.active.append(["单件重量"])
    workbook.active.append(["0.1234"])

    result = check_workbook(tmp_path, SubSupplierListExcelAgent, workbook, 1)

    assert result.status == "NG"
    assert "主数据区" in result.suggestions or "零件号" in result.suggestions


@pytest.mark.parametrize(
    ("agent_class", "sheet_name", "expected_fields"),
    [
        (AnnualTestRequirementsAgent, "Sheet", ("主表", "质量等级")),
        (InitialProcessCapacityAgent, "产品过程CCSC特性管理清单", ("P阶段Cpk结果", "CCSC数据区")),
    ],
    ids=["annual-quality-level", "initial-capacity-target"],
)
def test_missing_required_document_fields_are_ng_not_execution_errors(tmp_path, agent_class, sheet_name, expected_fields):
    workbook = Workbook()
    workbook.active.title = sheet_name

    result = check_workbook(tmp_path, agent_class, workbook, 1)

    assert result.status == "NG"
    assert any(field in result.suggestions for field in expected_fields)


def test_equipment_requires_the_supplier_column_for_process_rows(tmp_path):
    workbook = Workbook()
    workbook.active.title = "设备开发计划"
    workbook.active.append(["产线/工序", "设备开发", "设备", "设备型号", "利旧设备数量", "新采购设备数量", "设备通用情况", "计划到位时间"])
    workbook.active.append(["工序A", "开发", "设备A", "型号A", "1", "1", "通用", "2026-01-01"])

    result = check_workbook(tmp_path, EquipmentListAgent, workbook, 1)

    assert result.status == "NG"
    assert "供应商" in result.suggestions


@pytest.mark.parametrize(("measurement_type", "expected"), [(None, "PASS"), ("UNKNOWN", "NG")], ids=["empty-type", "unknown-type"])
def test_dimension_measurement_type_can_be_empty_but_not_unknown(tmp_path, measurement_type, expected):
    workbook = Workbook()
    workbook.active.title = "测量报告"
    workbook.active.append(["测点号", "类型"])
    workbook.active.append(["P1", measurement_type])

    result = check_workbook(tmp_path, DimensionMeasurementExcelAgent, workbook, 5)

    assert result.status == expected
    if expected == "NG":
        assert "类型值非法" in result.suggestions


def test_zero_nominal_is_a_valid_numeric_nominal(tmp_path):
    workbook = Workbook()
    workbook.active.title = "测量报告"
    workbook.active.append(["测点号", "主要控制方向", "名义值", "测量偏差值", "Part1", "Centering"])
    workbook.active.append(["P1", "Z", 0, None, 0.2, 0.2])

    result = check_workbook(tmp_path, DimensionMeasurementExcelAgent, workbook, 4)

    assert result.status == "PASS"


@pytest.mark.parametrize(("centering", "expected"), [(None, "NG"), (0.1, "PASS")], ids=["missing", "calculated"])
def test_calculable_centering_must_be_present(tmp_path, centering, expected):
    workbook = Workbook()
    workbook.active.title = "测量报告"
    workbook.active.append(["测点号", "主要控制方向", "名义值", "上公差", "下公差", "测量偏差值", "Part1", "Centering"])
    workbook.active.append(["P1", "X", None, 1, -1, None, 0.2, centering])

    result = check_workbook(tmp_path, DimensionMeasurementExcelAgent, workbook, 8)

    assert result.status == expected
    if expected == "NG":
        assert "Centering为空" in result.suggestions


@pytest.mark.parametrize(("end", "expected"), [("2026-01-01", "NG"), ("2025-12-31", "NG"), ("2026-01-02", "PASS")], ids=["same-day", "before-start", "after-start"])
def test_planned_test_end_must_be_after_start(tmp_path, end, expected):
    workbook = Workbook()
    workbook.active.title = "DVP&R"
    workbook.active.append(["序号", "试验项目", "试验要求", "试验类型", "计划开始", "计划结束"])
    workbook.active.append(["1", None, None, None, "2026-01-01", end])

    result = check_workbook(tmp_path, DvpRExcelAgent, workbook, 4)

    assert result.status == expected
    if expected == "NG":
        assert "晚于计划开始日期" in result.suggestions


@pytest.mark.parametrize(("process", "expected"), [(None, "PASS"), ("装配", "NG")], ids=["irrelevant-row", "process-row"])
def test_equipment_requirements_only_apply_to_process_rows(tmp_path, process, expected):
    workbook = Workbook()
    workbook.active.title = "设备开发计划"
    workbook.active.append(["产线/工序", "设备开发", "设备", "供应商", "设备型号", "利旧设备数量", "新采购设备数量", "设备通用情况", "计划到位时间"])
    workbook.active.append([process, None, "设备A"])

    result = check_workbook(tmp_path, EquipmentListAgent, workbook, 1)

    assert result.status == expected
    if expected == "PASS":
        assert result.suggestions == ""


@pytest.mark.parametrize(("issue", "expected"), [(None, "PASS"), ("装配间隙异常", "NG")], ids=["no-issue", "reported-issue"])
def test_corrective_action_requirements_only_apply_to_described_issues(tmp_path, issue, expected):
    workbook = Workbook()
    workbook.active.title = "项目问题清单"
    workbook.active.append(["问题描述", "对应措施", "时间节点", "负责人"])
    workbook.active.append([issue, "调整工装"])

    result = check_workbook(tmp_path, XirAssemblyIssueAgent, workbook, 3)

    assert result.status == expected
    if expected == "PASS":
        assert result.suggestions == ""
