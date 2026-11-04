"""L16 source conditions apply to all physical business rows, without an anchor exemption."""

import pytest
from audit.checkers.v4.li_auto.annual_tests import LiAutoAnnualTestsAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook


def execute(tmp_path, number, rows, *, merge=None):
    book = Workbook()
    book.active.title = "表格"
    for row in rows:
        book.active.append(row)
    if merge:
        book.active.merge_cells(merge)
    path = tmp_path / "年度试验要求.xlsx"
    book.save(path)
    book.close()
    agent = LiAutoAnnualTestsAgent(AuditInput(audit_id="annual-version", file_url=str(path)), RuleInput(rule_id="rule", **{**preset_rule_body("理想", "L16"), "rules": []}))
    try:
        return agent._execute_rule_check(number)
    finally:
        agent.close()


@pytest.mark.parametrize(("item", "frequency", "status"), [("ELV试验", "每两年", "PASS"), ("EMC试验", "每年", "NG"), ("耐久", "每年", "PASS"), ("耐久", None, "NG"), ("耐久", "=A1", "UNABLE")])
def test_frequency_conditions_are_checked_for_each_trial(tmp_path, item, frequency, status):
    result = execute(tmp_path, 2, [["试验项目", "测试频率"], [item, frequency]])
    assert result.status == status, result


def test_a_blank_frequency_cannot_end_the_scan_before_a_later_bad_trial(tmp_path):
    result = execute(tmp_path, 2, [["试验项目", "测试频率"], ["耐久", "每年"], ["EMC试验", None], ["ELV试验", "每年"]])
    assert result.status == "NG", result


def test_trial_identity_unknown_does_not_select_the_other_trial_frequency_rule(tmp_path):
    result = execute(tmp_path, 2, [["试验项目", "测试频率"], ["耐久", "每年"], [None, "每年"]])
    assert result.status == "UNABLE", result


@pytest.mark.parametrize("quantity", [0, "0pcs", "3件", "３件", "٣pcs"])
def test_source_quantity_requires_digits_not_a_new_positive_minimum(tmp_path, quantity):
    result = execute(tmp_path, 4, [["试验项目", "试验要求", "试验标准/方法", "样本数量"], ["耐久", "按规范", "GB1", quantity]])
    assert result.status == "PASS", result


def test_sample_clause_does_not_add_a_test_project_column_requirement(tmp_path):
    result = execute(tmp_path, 4, [["样本数量"], ["3pcs"]])
    assert result.status == "PASS", result


def test_sample_quantity_blank_with_other_trial_facts_cannot_be_skipped(tmp_path):
    result = execute(tmp_path, 4, [["试验项目", "试验要求", "试验标准/方法", "样本数量"], ["耐久", "按规范", "GB1", "3pcs"], [None, "按规范", "GB2", None]])
    assert result.status == "NG", result


def test_requirement_row_needs_all_three_fields_even_when_project_is_blank(tmp_path):
    result = execute(tmp_path, 5, [["试验项目", "试验要求", "试验标准/方法"], ["耐久", "按规范", "GB1"], [None, "按规范", "GB2"]])
    assert result.status == "NG", result


def test_merged_project_still_checks_each_requirement_row(tmp_path):
    result = execute(tmp_path, 5, [["试验项目", "试验要求", "试验标准/方法"], ["耐久", "按规范", "GB1"], [None, None, "GB2"]], merge="A2:A3")
    assert result.status == "NG", result


def test_requirement_scan_does_not_end_after_a_blank_gap(tmp_path):
    result = execute(tmp_path, 5, [["试验项目", "试验要求", "试验标准/方法"], ["耐久", "按规范", "GB1"], *[[None, None, None] for _ in range(8)], ["疲劳", None, "GB2"]])
    assert result.status == "NG", result


def test_known_placeholder_sample_value_is_checked_instead_of_skipped(tmp_path):
    assert execute(tmp_path, 4, [["样本数量"], ["NA"]]).status == "NG"


@pytest.mark.parametrize(("number", "label", "value"), [(1, "质量等级", "一般件"), (3, "供应商名称", "S01-供应商全称"), (6, "车型", "M1")])
def test_known_metadata_values_still_follow_the_source(number, label, value, tmp_path):
    assert execute(tmp_path, number, [[label], [value]]).status == "PASS"


@pytest.mark.parametrize(("number", "label"), [(1, "质量等级"), (3, "供应商名称"), (6, "车型")])
def test_unknown_metadata_values_are_not_reported_as_unfilled(number, label, tmp_path):
    result = execute(tmp_path, number, [[label], ['="value"']])
    assert result.status == "UNABLE", result
