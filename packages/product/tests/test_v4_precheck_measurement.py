"""NIO preparation math has its own tolerance and never applies engineering limits."""

import pytest
from audit.checkers.v4.nio.measurement import NioMeasurementAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook


def observe(tmp_path, *, values=None, upper=1, lower=-1, direction="X", point_type="CC", centering=None, range_value=None, second=None, family=True):
    values = [0.1, 0.2, 0.3, 0.4, 0.5] if values is None else values
    width = upper - lower
    mean = sum(value for value in values if isinstance(value, (float, int))) / max(1, sum(isinstance(value, (float, int)) for value in values))
    book = Workbook()
    book.active.title = "CoverSheet"
    for pair in [["零件号", "P-1"], ["零件名", "支架"], ["项目", "M1"], ["项目阶段", "P"], ["日期", "2026-01-01"]]:
        book.active.append(pair)
    sheet = book.create_sheet("Datum-001" if family else "Surface")
    sheet.append(["测点号", "类型", "坐标X", "坐标Y", "坐标Z", "名义值", "上公差", "下公差", "主要控制方向", "测量偏差值", None, None, None, None, "Centering", "Range", "测点图/截面"])
    sheet.append([None] * 9 + [f"Part{index}" for index in range(1, 6)])
    sheet.merge_cells("J1:N1")
    for column in [*range(1, 10), 15, 16, 17]:
        sheet.merge_cells(start_row=1, end_row=2, start_column=column, end_column=column)
    spread = (
        max(value for value in values if isinstance(value, (int, float))) - min(value for value in values if isinstance(value, (int, float)))
        if any(isinstance(value, (int, float)) for value in values)
        else 0
    )
    row = [
        "P01",
        point_type,
        1,
        2,
        3,
        None,
        upper,
        lower,
        direction,
        *values,
        centering if centering is not None else mean / width if width else 0,
        range_value if range_value is not None else spread / width if width else 0,
        "P01",
    ]
    sheet.append(row)
    if second is not None:
        sheet.append(second)
    path = tmp_path / "测量报告.xlsx"
    book.save(path)
    book.close()
    agent = NioMeasurementAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="source", **preset_rule_body("蔚来", "N01")))
    try:
        return {fact["id"].split(":", 1)[1]: fact for fact in agent.precheck()}
    finally:
        agent.close()


def test_precheck_does_not_reject_a_reproducible_engineering_threshold_exceedance(tmp_path):
    facts = observe(tmp_path, values=[1.4] * 5, centering=0.7)
    assert facts["S-06"]["status"] == "satisfied"
    assert facts["C-01"]["status"] == "satisfied"
    assert all(fact["certified"] is False for fact in facts.values())
    assert len(facts) == 51


@pytest.mark.parametrize(("delta", "status"), [(0.0000005, "satisfied"), (0.000002, "defect")])
def test_precheck_recalculation_uses_its_own_one_millionth_tolerance(tmp_path, delta, status):
    facts = observe(tmp_path, centering=0.15 + delta)
    assert facts["S-06"]["status"] == status
    if status == "defect":
        assert facts["S-06"]["code"] == "W06"


def test_inverted_tolerances_are_a_preparation_defect(tmp_path):
    fact = observe(tmp_path, upper=-1, lower=1)["C-02"]
    assert (fact["status"], fact["code"]) == ("defect", "B05")


def test_entire_zero_width_table_is_blocking_but_a_single_zero_width_row_is_not_counted(tmp_path):
    assert observe(tmp_path, upper=0, lower=0)["C-03"]["status"] == "defect"
    row = ["P02", "CC", 1, 2, 3, None, 1, -1, "X", 0.1, 0.2, 0.3, 0.4, 0.5, 0.15, 0.2, "P02"]
    assert observe(tmp_path, upper=0, lower=0, second=row)["C-03"]["status"] == "satisfied"


@pytest.mark.parametrize(("value", "code"), [("AB", "W03"), ("A", "B05")])
def test_only_a_complete_ab_column_proves_characteristic_semantic_substitution(tmp_path, value, code):
    fact = observe(tmp_path, point_type=value)["C-06"]
    assert (fact["status"], fact["code"]) == ("defect", code)


def test_missing_samples_warn_instead_of_becoming_zero_measurements(tmp_path):
    facts = observe(tmp_path, values=[0.1, 0.2, None, None, None])
    assert (facts["M-07"]["status"], facts["M-07"]["code"]) == ("defect", "W01")
    assert facts["C-01"]["status"] == "satisfied"
    assert facts["S-10"]["status"] != "defect"


@pytest.mark.parametrize(("value", "status"), [("TBD", "defect"), ("=1+1", "unknown")])
def test_known_non_numeric_text_and_unknown_formula_are_distinct(tmp_path, value, status):
    facts = observe(tmp_path, values=[value, 0.2, 0.3, 0.4, 0.5])
    assert facts["C-01"]["status"] == status


def test_direction_g_is_not_an_alias_for_gap(tmp_path):
    fact = observe(tmp_path, direction="G")["C-07"]
    assert (fact["status"], fact["code"]) == ("defect", "B05")


def test_a_rule_without_original_facts_never_becomes_pass(tmp_path):
    fact = observe(tmp_path)["S-08"]
    assert fact["status"] == "not_checked"
    assert fact["code"] is None


def test_each_observed_sample_has_its_own_original_cell_evidence(tmp_path):
    import json

    fact = observe(tmp_path)["C-01"]
    addresses = {json.loads(source["locator"]).get("cell") for source in fact["evidence"]["sources"]}
    assert {"J3", "K3", "L3", "M3", "N3"}.issubset(addresses)
