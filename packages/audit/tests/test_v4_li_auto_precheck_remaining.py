"""L1 preparation decisions are bounded by actual native source cells."""

import pytest
from audit.checkers.v4.li_auto import EXCEL_AGENTS
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook


def run(tmp_path, entry, rows):
    book = Workbook()
    sheet = book.active
    sheet.title = "实填报告"
    for row in rows:
        sheet.append(row)
    path = tmp_path / "原件.xlsx"
    book.save(path)
    book.close()
    agent = EXCEL_AGENTS[entry](AuditInput(audit_id="frozen-original", file_url=str(path)), RuleInput(rule_id="frozen-rule", **preset_rule_body("理想", entry)))
    try:
        return {fact["id"].split(":", 1)[1]: fact for fact in agent.precheck()}
    finally:
        agent.close()


def assert_fact(fact, expected, code=None):
    assert fact["status"] == expected
    assert fact["code"] == code
    assert fact["certified"] is False
    if expected == "defect":
        assert fact["evidence"]["sources"]
        assert any("frozen-original" in source["locator"] for source in fact["evidence"]["sources"])


@pytest.mark.parametrize(("part", "name", "expected"), [("螺栓", "P001", "defect"), ("P001", "螺栓", "satisfied"), ("=1+1", "螺栓", "unknown")])
def test_material_field_swap_uses_real_values(tmp_path, part, name, expected):
    result = run(tmp_path, "L04", [["零件号", "零件名称"], [part, name]])
    assert_fact(result["PC-C1"], expected, "B03" if expected == "defect" else None)


def test_unknown_driver_is_not_dropped_after_one_known_part(tmp_path):
    result = run(tmp_path, "L04", [["零件号", "单件重量"], ["P001", 10], ["=1+1", "bad"]])
    assert_fact(result["PC-C3"], "unknown")


@pytest.mark.parametrize(
    ("criterion", "headers", "values", "code"),
    [
        ("PC-C5", ["材料规格/尺寸"], ["电泳20μm"], "W03"),
        ("PC-C6", ["材料执行标准"], ["某某有限公司"], "W03"),
        ("PC-C7", ["成型工艺", "表面处理"], ["电泳", "电泳"], "W06"),
        ("PC-C9", ["通过何种质量管理体系认证"], ["ISO/TS16949:2009"], "W03"),
    ],
)
def test_source_material_counterexamples(tmp_path, criterion, headers, values, code):
    result = run(tmp_path, "L04", [["零件号", *headers], ["P001", *values]])
    assert_fact(result[criterion], "defect", code)


@pytest.mark.parametrize("blank", [4, 5])
def test_fill_rate_includes_supplier_and_certification_columns(tmp_path, blank):
    headers = ["零件号", "材料执行标准", "材料类型", "材料牌号", "材料规格/尺寸", "材料供应商名称", "通过何种质量管理体系认证"]
    values = ["P001", "GB/T 699", "金属", "45", "10mm", "材料厂", "IATF16949:2016"]
    values[blank + 1] = None
    assert_fact(run(tmp_path, "L04", [headers, values])["PC-D3"], "defect", "W01")


def test_explicit_unit_mix_is_reported_without_inventing_default_units(tmp_path):
    result = run(tmp_path, "L04", [["零件号", "单件重量", "材料规格/尺寸"], ["P1", 1, "10mm"], ["P2", 2, "1cm"]])
    assert_fact(result["PC-E6"], "defect", "W03")


@pytest.mark.parametrize(("resolution", "band", "expected"), [("0.02mm", "0.1mm", "defect"), ("10μm", "0.1mm", "satisfied"), ("0.01", "0.1mm", "unknown"), ("=1+1", "0.1mm", "unknown")])
def test_msa_resolution_compares_same_dimensions_at_exact_boundary(tmp_path, resolution, band, expected):
    fact = run(tmp_path, "L05", [["分辨率", "公差带"], [resolution, band]])["MSA-PRE-10"]
    assert_fact(fact, expected, "W06" if expected == "defect" else None)


@pytest.mark.parametrize(("r", "k", "n", "expected", "code"), [(1, 3, 10, "defect", "B05"), (2, 1, 10, "defect", "W06"), (2, 2, 4, "defect", "W06"), (3, 3, 10, "unknown", None)])
def test_msa_design_uses_declared_counts_not_column_count(tmp_path, r, k, n, expected, code):
    fact = run(tmp_path, "L05", [["重复次数", r], ["评价人数", k], ["零件数", n]])["MSA-PRE-11"]
    assert_fact(fact, expected, code)


@pytest.mark.parametrize(("k1", "expected"), [(0.5908, "unknown"), (0.59, "defect"), ("=1+1", "unknown")])
def test_msa_constants_require_exact_three_by_three_by_ten_design(tmp_path, k1, expected):
    rows = [["重复次数", 3], ["评价人数", 3], ["零件数", 10], ["K1", k1], ["K2", 0.5231], ["K3", 0.3146], ["D4", 2.575]]
    fact = run(tmp_path, "L05", rows)["MSA-PRE-12"]
    assert_fact(fact, expected, "B06" if expected == "defect" else None)


@pytest.mark.parametrize(
    ("label", "value", "expected", "code"),
    [("%GRR", 0.2, "defect", "W03"), ("%GRR", "20%", "satisfied", None), ("%GRR", 101, "defect", "W03"), ("ndc", -1, "defect", "W06"), ("ndc", 51, "defect", "W06"), ("ndc", 5, "satisfied", None)],
)
def test_msa_percentage_and_ndc_ranges(tmp_path, label, value, expected, code):
    assert_fact(run(tmp_path, "L05", [[label, value]])["MSA-PRE-20"], expected, code)


@pytest.mark.parametrize(("lower", "upper", "expected"), [(10, 9, "defect"), (10, 10, "defect"), (9, 11, "unknown"), ("=1+1", 11, "unknown")])
def test_capability_limits_check_only_printed_algebra(tmp_path, lower, upper, expected):
    fact = run(tmp_path, "L06", [["LSL", lower], ["USL", upper]])["PRE-11"]
    assert_fact(fact, expected, "B06" if expected == "defect" else None)


@pytest.mark.parametrize(("cp", "cpk", "expected"), [(1, 1.1, "defect"), (1.1, 1.1, "unknown"), (0.7, 0.5, "unknown"), (1, "=1+1", "unknown")])
def test_capability_internal_math_never_applies_formal_acceptance_threshold(tmp_path, cp, cpk, expected):
    assert_fact(run(tmp_path, "L06", [["Cp", cp], ["Cpk", cpk]])["PRE-12"], expected, "B06" if expected == "defect" else None)


def test_percentage_native_number_format_is_not_misread_as_decimal(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.title = "实填报告"
    sheet.append(["%GRR", 0.2])
    sheet["B1"].number_format = "0.00%"
    path = tmp_path / "原件.xlsx"
    book.save(path)
    book.close()
    agent = EXCEL_AGENTS["L05"](AuditInput(audit_id="frozen-original", file_url=str(path)), RuleInput(rule_id="frozen-rule", **preset_rule_body("理想", "L05")))
    try:
        fact = next(fact for fact in agent.precheck() if fact["id"] == "L05:MSA-PRE-20")
        assert_fact(fact, "satisfied")
    finally:
        agent.close()


def test_horizontal_metadata_header_is_not_treated_as_bad_numeric_data(tmp_path):
    fact = run(tmp_path, "L06", [["LSL", "USL"], [9, 11]])["PRE-11"]
    assert_fact(fact, "unknown")


@pytest.mark.parametrize(("second", "expected"), [([12, 11], "defect"), ([10, 12], "unknown"), (["=1+1", 12], "unknown")])
def test_horizontal_specification_rows_remain_bound_per_row(tmp_path, second, expected):
    fact = run(tmp_path, "L06", [["LSL", "USL"], [9, 11], second])["PRE-11"]
    assert_fact(fact, expected, "B06" if expected == "defect" else None)


def test_horizontal_printed_math_does_not_pair_across_rows(tmp_path):
    fact = run(tmp_path, "L06", [["Cp", "Cpk"], [1, 0.8], [0.7, 0.9]])["PRE-12"]
    assert_fact(fact, "defect", "B06")


def test_multiple_report_metadata_values_need_a_proven_object_key(tmp_path):
    result = run(tmp_path, "L03", [["报告编号", "R001"], ["报告编号", "R002"]])
    assert_fact(result["CON-02"], "unknown")
