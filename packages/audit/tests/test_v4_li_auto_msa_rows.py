"""Original L05 clause 2 checks physical business rows, including empty IDs."""

import pytest
from audit.checkers.v4.li_auto.msa import LiAutoMsaAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook


def check(tmp_path, rows):
    book = Workbook()
    book.active.title = "测量系统分析计划表"
    for row in [["序号", "新增业务列", "备注"], *rows]:
        book.active.append(row)
    path = tmp_path / "原始计划.xlsx"
    book.save(path)
    book.close()
    agent = LiAutoMsaAgent(AuditInput(audit_id="frozen", file_url=str(path)), RuleInput(rule_id="rule", **preset_rule_body("理想", "L05")))
    try:
        return agent._execute_rule_check(2)
    finally:
        agent.close()


@pytest.mark.parametrize(("serial", "expected"), [(None, "NG"), ("", "NG"), ("=1+1", "UNABLE"), (0, "PASS"), (2, "PASS")])
def test_populated_row_cannot_disappear_when_its_serial_is_empty_or_unreadable(tmp_path, serial, expected):
    result = check(tmp_path, [[1, "第一真实业务项", None], [serial, "另一真实业务项", None]])
    assert result.status == expected, result.suggestions


def test_a_real_row_after_a_long_blank_gap_is_still_checked(tmp_path):
    assert check(tmp_path, [[1, "已填", None], *[[None, None, None]] * 30, [None, "漏序号业务项", None]]).status == "NG"


def test_repeated_header_without_business_data_is_not_a_passing_plan(tmp_path):
    assert check(tmp_path, [["序号", "新增业务列", "备注"]]).status == "UNABLE"


def test_signoff_and_remark_only_rows_do_not_create_required_business_data(tmp_path):
    assert check(tmp_path, [[1, "已填", None], [None, None, "备注内容"], ["编制：", "人员", None]]).status == "PASS"
