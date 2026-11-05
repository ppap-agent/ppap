"""Shared measurement decisions and their Standard workbook integration."""

import pytest
from audit.checkers.v2.dimension_measurement_excel_agent import DimensionMeasurementExcelAgent
from audit.contracts import AuditInput, RuleInput
from audit.engine.checkers.dimension_measurement_policy import (
    check_duplicates,
    check_qualitative_method,
    check_tolerance,
    is_strict_ok_text,
    parse_quality_standard_range,
)
from openpyxl import Workbook


@pytest.mark.parametrize(
    ("standard", "expected"),
    [
        ("2×Φ10±0.5", (9.5, 10.5)),
        ("10（+0.5，-0.2）", (9.8, 10.5)),
        ("Φ14（0.2，0.1）", (14.1, 14.2)),
        ("10+0.5/-0.2", (9.8, 10.5)),
        ("10+0.5", (10.0, 10.5)),
        ("10/-0.2", (9.8, 10.0)),
        ("R2max", (0.0, 2.0)),
        ("Ra3.2", (0.0, 3.2)),
        ("≤0.5", (0.0, 0.5)),
        ("10min", (10.0, float("inf"))),
    ],
)
def test_existing_tolerance_notations(standard, expected):
    assert parse_quality_standard_range(standard) == expected


@pytest.mark.parametrize(("value", "expected"), [("OK / 合格", True), ("ok", True), ("PASS", False), ("正确", False), ("OK/NG", False), (None, False)])
def test_qualitative_result_tokens_are_strict(value, expected):
    assert is_strict_ok_text(value) is expected


@pytest.mark.parametrize(("measurement", "expected"), [(9.49, "NG"), (9.5, "PASS"), (10.5, "PASS"), (10.51, "NG")])
def test_tolerance_checks_both_inclusive_bounds(measurement, expected):
    decision = check_tolerance("10±0.5", (measurement,), "OK", "卡尺")
    assert decision.status == expected
    assert decision.numeric_checked


def test_qualitative_tolerance_needs_a_method_and_strict_result_but_no_numeric_range():
    assert check_tolerance(None, ("OK", "合格"), "OK", "检具").status == "PASS"
    assert check_tolerance(None, ("OK",), "OK", None).reason == "检查数据为OK/合格但缺少检查方法，无法完成校验"
    assert check_tolerance(None, ("OK",), "PASS", "检具").reason == "判定结果不规范: PASS"
    assert check_tolerance("10±0.5", ("符合检具",), "OK", "检具").reason == "检查数据非数值或占位符: 符合检具"


def test_tolerance_preserves_source_cell_compaction_and_known_non_range_behavior():
    assert check_tolerance("10±0.5", ("10 mm",), "OK", "卡尺").status == "PASS"
    assert check_tolerance("位置度0.3", (0.2,), "OK", "三坐标").status == "PASS"
    assert check_tolerance("平面度0.05", (0.0987,), "OK", "三坐标").status == "NG"
    assert check_tolerance("Φ14（0.2，0.1）", (14.05,), "OK", "卡尺").status == "NG"
    assert check_tolerance("12.5±0.1", ("12.47/12.48\n12.49/12.50",), "OK", "三坐标").status == "PASS"
    assert check_tolerance("10", (10,), None, None).status == "NA"
    assert check_tolerance("未给定", (10,), "OK", "卡尺").reason == "判定标准无法解析: 未给定"
    assert check_tolerance(None, (), None, None).status == "NA"


def test_duplicate_check_compares_numeric_values_across_groups_only():
    assert check_duplicates(("10/11", "10.0")).reason == "同行检查数据存在重复值"
    assert check_duplicates(("10/11", "12.0")).status == "PASS"
    assert check_duplicates(("OK", "OK")).status == "NA"
    assert check_duplicates((10, "符合检具")).status == "NA"


def test_qualitative_method_only_triggers_on_ok_data_and_rejects_mixed_data():
    assert check_qualitative_method((10, 11), None).status == "NA"
    assert check_qualitative_method(("符合检具",), "检具").status == "NA"
    assert check_qualitative_method(("OK", "合格"), "使用 通 止 规").status == "PASS"
    assert check_qualitative_method(("OK",), "卡尺").reason == "检查数据为OK/合格但方法不匹配: 卡尺"
    assert check_qualitative_method(("OK", 10), "卡板").reason == "定性方法数据不合规: 10"


@pytest.mark.parametrize(
    ("rule_number", "standard", "measurements", "result", "method", "status", "message"),
    [
        (3, "10±0.5", (9.5, 10.5), "OK", "卡尺", "PASS", "所有检查数据均在公差范围内"),
        (3, "10±0.5", (9.49, 10), "OK", "卡尺", "NG", "检查数据9.49超出公差范围(9.5, 10.5)"),
        (3, "10±0.5", (10, 10.51), "OK", "卡尺", "NG", "检查数据10.51超出公差范围(9.5, 10.5)"),
        (3, "未给定", ("OK", "合格"), "OK", "检具", "PASS", "规则3按通过处理"),
        (3, "未给定", ("OK", "合格"), "OK", None, "NG", "检查数据为OK/合格但缺少检查方法，无法完成校验"),
        (3, "10±0.5", ("10 mm",), "OK", "卡尺", "PASS", "所有检查数据均在公差范围内"),
        (3, "10±0.5", ("符合检具",), "OK", "检具", "NG", "检查数据非数值或占位符: 符合检具"),
        (3, None, (), None, None, "PASS", "规则3按通过处理"),
        (5, "10±0.5", (10, "10.0"), "OK", "卡尺", "NG", "同行检查数据存在重复值"),
        (5, "未给定", ("OK", "OK"), "OK", "通止规", "PASS", "尺寸区同行检查数据无重复值"),
        (5, None, (), None, None, "PASS", "尺寸区同行检查数据无重复值"),
        (7, "10±0.5", (10, 10.1), "OK", "卡尺", "PASS", "规则7按通过处理"),
        (7, "未给定", ("符合检具",), "OK", "检具", "PASS", "规则7按通过处理"),
        (7, "未给定", ("OK", "合格"), "OK", "通 止 规", "PASS", "所有定性方法数据均为OK/合格"),
        (7, "未给定", ("OK", "合格"), "OK", "卡尺", "NG", "检查数据为OK/合格但方法不匹配: 卡尺"),
        (7, None, (), None, None, "PASS", "规则7按通过处理"),
    ],
)
def test_standard_workbook_keeps_rule_decisions_and_source_messages(tmp_path, rule_number, standard, measurements, result, method, status, message):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "尺寸检验报告"
    sheet.append(["序号", "检查项目", "判定标准", "供方检查方法/检查工具", "检查数据", None, "判定结果"])
    sheet.append(["尺寸"])
    sheet.append([1, "直径", standard, method, *measurements, *([None] * (2 - len(measurements))), result])
    path = tmp_path / "measurement-contract.xlsx"
    workbook.save(path)
    agent = DimensionMeasurementExcelAgent(
        AuditInput(audit_id="measurement-contract", file_url=str(path)),
        RuleInput(
            rule_id="measurement-rules",
            rule_profile="v2",
            type=DimensionMeasurementExcelAgent.DOC_TYPE.get_type_name(),
            rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, 8)],
        ),
    )
    try:
        expected_message = f"工作表尺寸检验报告第3行{message}" if status == "NG" else message
        assert getattr(agent, f"rule_{rule_number}")(agent.rule_ctx(workbook)) == (status, expected_message)
        public_result = agent.check(rule_number - 1)
        assert public_result is not None
        assert public_result.status == status
        assert public_result.suggestions == expected_message
    finally:
        agent.close()
        workbook.close()
