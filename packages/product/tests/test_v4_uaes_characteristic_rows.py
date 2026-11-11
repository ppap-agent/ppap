"""U12 numbered business rows cannot hide behind an empty sequence cell."""

import pytest
from audit.checker_lib.uaes.special_characteristics import UaesSpecialCharacteristicsAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook

HEADERS = ["编号", "产品特性", "特性及公差", "特性类型"]


def checked(tmp_path, rows, *, merge_number=False):
    book = Workbook()
    sheet = book.active
    sheet.title = "特殊特性清单"
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    if merge_number:
        sheet.merge_cells("A2:A3")
    path = tmp_path / "original.xlsx"
    book.save(path)
    book.close()
    body = preset_rule_body("UAES", "U12")
    agent = UaesSpecialCharacteristicsAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="rule", **{name: value for name, value in body.items() if name != "kind"}))
    return agent.check(1)


@pytest.mark.parametrize("number", [None, "", "   "])
def test_populated_characteristic_without_number_cannot_pass(tmp_path, number):
    result = checked(tmp_path, [[1, "孔径", "10±1", "CC"], [number, "高度", "20±1", "SC"]])
    assert result.status == "NG", result.suggestions
    assert "编号" in result.suggestions


def test_late_business_row_after_blank_gap_is_still_checked(tmp_path):
    result = checked(tmp_path, [[1, "孔径", "10±1", "CC"], *([[None] * 4] * 45), [None, "高度", "20±1", "SC"]])
    assert result.status == "NG", result.suggestions


def test_uncached_sequence_formula_stays_unknown(tmp_path):
    result = checked(tmp_path, [[1, "孔径", "10±1", "CC"], ["=1+1", "高度", "20±1", "SC"]])
    assert result.status == "UNABLE", result.suggestions


def test_repeated_known_header_and_footer_do_not_become_business_rows(tmp_path):
    result = checked(tmp_path, [[1, "孔径", "10±1", "CC"], HEADERS, [2, "高度", "20±1", "SC"], ["编制/日期", "编制人", "2026-11-03", None]])
    assert result.status == "PASS", result.suggestions


@pytest.mark.parametrize(("name", "expected"), [("高度", "PASS"), (None, "NG")])
def test_vertical_number_merge_keeps_each_physical_characteristic(tmp_path, name, expected):
    result = checked(tmp_path, [[1, "孔径", "10±1", "CC"], [None, name, "20±1", "SC"]], merge_number=True)
    assert result.status == expected, result.suggestions
