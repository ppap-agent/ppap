"""U06 summary covers populated rows even when both printed results are blank."""

import pytest
from audit.checkers.v4.uaes.engineering import UaesCapabilityAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook

HEADERS = ["特性名称", "CPK结果值", "PPK结果值", "CPK目标值", "PPK目标值", "反应计划"]


def checked(tmp_path, rows, *, headers=HEADERS):
    book = Workbook()
    sheet = book.active
    sheet.title = "能力值汇总"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    path = tmp_path / "original.xlsx"
    book.save(path)
    book.close()
    body = preset_rule_body("UAES", "U06")
    agent = UaesCapabilityAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="rule", **{name: value for name, value in body.items() if name != "kind"}))
    return agent.check(2)


@pytest.mark.parametrize("targets", [(1.33, 1.67), (None, None)])
def test_named_row_with_unread_results_cannot_hide_after_a_valid_row(tmp_path, targets):
    result = checked(tmp_path, [["孔径", 1.5, 2, 1.33, 1.67, None], ["高度", None, None, *targets, None]])
    assert result.status == "UNABLE", result.suggestions


def test_late_incomplete_row_below_blank_gap_is_not_outside_scope(tmp_path):
    result = checked(tmp_path, [["孔径", 1.5, 2, 1.33, 1.67, None], *([[None] * 6] * 45), ["高度", None, None, 1.33, 1.67, None]])
    assert result.status == "UNABLE", result.suggestions


def test_uncached_result_formula_is_unknown(tmp_path):
    result = checked(tmp_path, [["孔径", 1.5, 2, 1.33, 1.67, None], ["高度", "=1+1", 2, 1.33, 1.67, None]])
    assert result.status == "UNABLE", result.suggestions


@pytest.mark.parametrize(("reaction", "expected"), [(None, "NG"), ("调整工装并复测", "PASS")])
def test_low_result_uses_its_actual_target_and_reaction(tmp_path, reaction, expected):
    result = checked(tmp_path, [["孔径", 2, 2, 3, 1.67, reaction]])
    assert result.status == expected, result.suggestions


def test_repeated_known_header_and_signature_footer_remain_boundaries(tmp_path):
    result = checked(tmp_path, [["孔径", 1.5, 2, 1.33, 1.67, None], HEADERS, ["高度", 1.5, 2, 1.33, 1.67, None], ["编制/日期", "张三", "2026-11-03", None, None, None]])
    assert result.status == "PASS", result.suggestions


def test_results_only_layout_does_not_gain_a_characteristic_name_requirement(tmp_path):
    result = checked(tmp_path, [[1.5, 2, 1.33, 1.67, None]], headers=HEADERS[1:])
    assert result.status == "PASS", result.suggestions


def test_footer_without_business_rows_cannot_pass(tmp_path):
    result = checked(tmp_path, [["编制/日期", "张三", "2026-11-03", None, None, None]])
    assert result.status == "UNABLE", result.suggestions
