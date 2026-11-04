"""U05 plan completeness uses physical business rows, including blank serials."""

import pytest
from audit.checkers.v4.uaes.msa import UaesMsaAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook

HEADERS = ["序号", "量具名称", "新增业务列", "备注"]


def checked(tmp_path, rows):
    book = Workbook()
    sheet = book.active
    sheet.title = "测量系统分析计划"
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    path = tmp_path / "original.xlsx"
    book.save(path)
    book.close()
    body = preset_rule_body("UAES", "U05")
    agent = UaesMsaAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="rule", **{name: value for name, value in body.items() if name != "kind"}))
    return agent.check(0)


@pytest.mark.parametrize("serial", [None, "", "   "])
def test_business_row_without_serial_is_not_skipped(tmp_path, serial):
    result = checked(tmp_path, [[1, "卡尺", "已填", None], [serial, "千分尺", "已填", None]])
    assert result.status == "NG", result.suggestions


def test_late_business_row_after_blank_gap_is_checked(tmp_path):
    result = checked(tmp_path, [[1, "卡尺", "已填", None], *([[None] * 4] * 45), [None, "千分尺", "已填", None]])
    assert result.status == "NG", result.suggestions


def test_unknown_formula_serial_cannot_pass(tmp_path):
    result = checked(tmp_path, [[1, "卡尺", "已填", None], ["=1+1", "千分尺", "已填", None]])
    assert result.status == "UNABLE", result.suggestions


def test_new_declared_business_column_is_required(tmp_path):
    result = checked(tmp_path, [[1, "卡尺", None, None]])
    assert result.status == "NG", result.suggestions


def test_zero_serial_and_empty_remarks_are_valid(tmp_path):
    result = checked(tmp_path, [[0, "卡尺", "已填", None]])
    assert result.status == "PASS", result.suggestions


def test_repeated_header_and_known_footer_are_not_business_rows(tmp_path):
    result = checked(tmp_path, [[1, "卡尺", "已填", None], HEADERS, [2, "千分尺", "已填", None], ["编制/日期", "张三", "2026-11-03", None]])
    assert result.status == "PASS", result.suggestions


def test_only_remarks_or_footer_cannot_prove_a_business_scope(tmp_path):
    result = checked(tmp_path, [[None, None, None, "补充说明"], ["编制/日期", "张三", "2026-11-03", None]])
    assert result.status == "UNABLE", result.suggestions
