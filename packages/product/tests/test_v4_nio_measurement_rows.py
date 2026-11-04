"""N01 source clauses cannot hide missing-ID measurements or outliers."""

import pytest
from audit.checkers.v4.nio.measurement import NioMeasurementAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook


def run(tmp_path, rows, number):
    book = Workbook()
    sheet = book.active
    sheet.title = "Surface"
    sheet.append(["测点号", "类型", "坐标X", "坐标Y", "坐标Z", "名义值", "上公差", "下公差", "主要控制方向", "测量偏差值", None, "Centering", "Range"])
    sheet.append([None] * 9 + ["Part1", "Part2"])
    sheet.merge_cells("J1:K1")
    for column in [*range(1, 10), 12, 13]:
        sheet.merge_cells(start_row=1, end_row=2, start_column=column, end_column=column)
    for row in rows:
        sheet.append(row)
    path = tmp_path / "实际测点.xlsx"
    book.save(path)
    book.close()
    agent = NioMeasurementAgent(AuditInput(audit_id="frozen", file_url=str(path)), RuleInput(rule_id="rule", **preset_rule_body("蔚来", "N01")))
    try:
        return agent._execute_rule_check(number)
    finally:
        agent.close()


def measurement(point="P1", centering=0.075):
    return [point, "CC", 1, 2, 3, None, 1, -1, "X", 0.1, 0.2, centering, 0.05]


@pytest.mark.parametrize(("point", "expected"), [(None, "NG"), ("", "NG"), ("=1+1", "UNABLE"), ("P2", "PASS")])
def test_mandatory_point_number_is_checked_in_every_real_measurement_row(tmp_path, point, expected):
    result = run(tmp_path, [measurement(), measurement(point)], 2)
    assert result.status == expected, result.suggestions


@pytest.mark.parametrize("point", [None, "", "=1+1"])
def test_known_centering_outlier_remains_in_scope_without_a_usable_point_number(tmp_path, point):
    result = run(tmp_path, [measurement(), measurement(point, 0.9)], 9)
    assert result.status == "NG", result.suggestions


def test_measurement_after_a_long_blank_gap_is_not_silently_ignored(tmp_path):
    assert run(tmp_path, [measurement(), *[[None] * 13] * 30, measurement(None, 0.9)], 9).status == "NG"


def test_sample_only_in_the_second_raw_column_is_not_a_repeated_header(tmp_path):
    orphan = [None] * 13
    orphan[10] = 0.2
    assert run(tmp_path, [measurement(), orphan], 2).status == "NG"
    assert run(tmp_path, [measurement(), orphan], 9).status == "UNABLE"


def test_known_nominal_defect_is_not_masked_by_later_uncomputed_statistics(tmp_path):
    defective = measurement()
    defective[5] = 0
    formula_only = [None] * 13
    formula_only[11:13] = ["=1+1", "=1+1"]
    assert run(tmp_path, [defective, formula_only], 4).status == "NG"


def test_uncomputed_measurement_row_cannot_be_skipped_to_claim_complete_scope(tmp_path):
    formula_only = [None] * 13
    formula_only[9] = "=1+1"
    assert run(tmp_path, [measurement(), formula_only], 4).status == "UNABLE"


def test_uncomputed_statistics_remain_unknown_for_the_statistical_clause(tmp_path):
    formula_only = [None] * 13
    formula_only[11:13] = ["=1+1", "=1+1"]
    assert run(tmp_path, [measurement(), formula_only], 9).status == "UNABLE"
