"""NIO L1 fields are separate from its formal engineering verdicts."""

import json

from audit.checkers.v4.nio.measurement import NioMeasurementAgent
from audit.contracts import AuditInput, RuleInput
from audit.precheck_sources import l1_criteria
from audit.presets import preset_rule_body
from openpyxl import Workbook


def execute(tmp_path, *, cover=None, part="P12345", date="2026-01-01", samples=None, point_type="CC", centering=0.075, range_value=0.05, extra=None, duplicate=False):
    book = Workbook()
    front = book.active
    front.title = "CoverSheet"
    for row in cover or [["零件号", part], ["零件名", "支架"], ["项目", "项目A"], ["项目阶段", "P"], ["日期", date], ["供应商", "真实供应商"], ["测量员", "张工"]]:
        front.append(row)
    sheet = book.create_sheet("Surface")
    headers = ["测点号", "类型", "坐标X", "坐标Y", "坐标Z", "名义值", "上公差", "下公差", "主要控制方向", "测量偏差值", None, None, None, None, "Centering", "Range", "测点图/截面"]
    sheet.append(headers)
    sheet.append([None] * 9 + ["Part1", "Part2", "Part3", "Part4", "Part5"])
    sheet.merge_cells("J1:N1")
    for column in [*range(1, 10), 15, 16, 17]:
        sheet.merge_cells(start_row=1, end_row=2, start_column=column, end_column=column)
    values = samples if samples is not None else [0.1, 0.2, 0.1, 0.2, 0.15]
    sheet.append(["P01", point_type, 1, 2, 3, None, 1, -1, "X", *values, centering, range_value, "P01"])
    if duplicate:
        sheet.append(["P01", point_type, 1, 2, 3, None, 1, -1, "X", *values, centering, range_value, "P01"])
    if extra:
        extra(book)
    path = tmp_path / "PN_P12345_measurement.xlsx"
    book.save(path)
    book.close()
    body = preset_rule_body("蔚来", "N01")
    checker = NioMeasurementAgent(
        AuditInput(audit_id="version", file_url=str(path), file_metadata={"original_filename": path.name}), RuleInput(rule_id="rule", **{name: value for name, value in body.items() if name != "kind"})
    )
    facts = checker.precheck()
    assert {item["id"] for item in l1_criteria("N01")} <= {item["id"] for item in facts}
    for fact in facts:
        assert fact["certified"] is False
        if fact["status"] == "satisfied":
            assert fact["evidence"]["sources"], fact
        if fact.get("evidence"):
            assert all(json.loads(source["locator"])["file_version_id"] == "version" for source in fact["evidence"]["sources"])
    return {fact["id"].split(":", 1)[1]: fact for fact in facts}


def test_nio_cover_pairs_and_columns_have_real_field_values(tmp_path):
    facts = execute(tmp_path)
    assert facts["M-03"]["status"] == "satisfied", facts["M-03"]
    assert facts["C-08"]["status"] == "satisfied"
    assert facts["M-07"]["status"] == "satisfied"
    facts = execute(tmp_path, cover=[["零件号", "零件名", "项目", "项目阶段", "日期", "供应商", "测量员"], ["P12345", "支架", "项目A", "P", "2026-01-01", "真实供应商", "张工"]])
    assert facts["M-03"]["status"] == "satisfied", facts["M-03"]


def test_nio_missing_cover_key_does_not_borrow_note_underneath(tmp_path):
    rows = [["零件号", None], ["备注：仅内部使用"], ["零件名", "支架"], ["项目", "项目A"], ["项目阶段", "P"], ["日期", "2026-01-01"]]
    facts = execute(tmp_path, cover=rows)
    assert facts["M-03"]["status"] == "defect", facts["M-03"]
    assert "为空" in facts["M-03"]["message"]


def test_nio_dates_distinguish_invalid_from_default_warning(tmp_path):
    facts = execute(tmp_path, date="not a date")
    assert facts["C-08"]["code"] == "B05"
    facts = execute(tmp_path, date="2017-01-10")
    assert facts["C-08"]["code"] == "W03"
    facts = execute(tmp_path, date="2026/01/01")
    assert facts["S-18"]["code"] == "W03"


def test_nio_metadata_type_errors_have_their_own_scope(tmp_path):
    facts = execute(tmp_path, part="支架")
    assert facts["C-12"]["status"] == "defect"
    assert facts["C-12"]["code"] == "B03"
    assert facts["S-03"]["code"] == "W04"


def test_nio_low_engineering_values_are_not_precheck_thresholds(tmp_path):
    facts = execute(tmp_path, samples=[0.9, 0.9, 0.9, 0.9, 0.9], centering=0.45, range_value=0)
    assert facts["S-06"]["status"] == "satisfied", facts["S-06"]
    assert facts["S-07"]["status"] == "handoff"
    assert facts["M-06"]["status"] == "satisfied"


def test_nio_error_literal_is_distinct_from_uncalculated_formula(tmp_path):
    facts = execute(tmp_path, samples=["#REF!", 0.2, 0.1, 0.2, 0.15])
    assert facts["C-10"]["status"] == "defect", facts["C-10"]
    assert facts["C-10"]["code"] == "B05"
    facts = execute(tmp_path, samples=["=1+1", 0.2, 0.1, 0.2, 0.15])
    assert facts["C-10"]["status"] == "unknown"
    assert facts["M-07"]["status"] == "unknown"


def test_nio_derived_error_is_warning_with_basis_still_readable(tmp_path):
    facts = execute(tmp_path, centering="#DIV/0!")
    assert facts["C-10"]["status"] == "satisfied", facts["C-10"]
    assert facts["C-17"]["status"] == "defect"
    assert facts["C-17"]["code"] == "W05"


def test_nio_optional_native_columns_do_not_modify_formal_resource(tmp_path):
    def extra(book):
        sheet = book["Surface"]
        sheet["R1"] = "公差带"
        sheet["R3"] = 3
        sheet["S1"] = "均值"
        sheet["S3"] = 0.15
        sheet["T1"] = "极差"
        sheet["T3"] = 0.1

    facts = execute(tmp_path, extra=extra)
    assert facts["C-13"]["status"] == "defect", facts["C-13"]
    assert facts["C-13"]["code"] == "B06"
    assert facts["S-05"]["status"] == "satisfied", facts["S-05"]


def test_nio_zero_measurements_are_real_samples_and_not_metadata_placeholders(tmp_path):
    facts = execute(tmp_path, samples=[0, 0, 0, 0, 0], centering=0, range_value=0)
    assert facts["M-07"]["status"] == "satisfied"
    assert facts["C-01"]["status"] == "satisfied"
    assert facts["S-06"]["status"] == "satisfied"


def test_nio_derived_sign_error_is_distinct_from_ordinary_difference(tmp_path):
    facts = execute(tmp_path, centering=-0.075)
    assert facts["S-06"]["code"] == "B06"
    facts = execute(tmp_path, centering=0.076)
    assert facts["S-06"]["code"] == "W06"
    facts = execute(tmp_path, centering=None)
    assert facts["S-06"]["code"] == "W05"


def test_nio_element_matrix_and_single_side_state_are_explicit(tmp_path):
    facts = execute(tmp_path)
    matrix = {item["id"].split(":", 1)[1]: item for item in facts["M-02"]["elements"]}
    assert {f"M-02:E{index:02d}" for index in range(1, 24)} <= set(matrix)
    assert matrix["M-02:E03"]["status"] == "satisfied"
    assert facts["M-15"]["status"] == "not_checked"
    assert facts["M-15"]["code"] == "S01"
    assert facts["M-09"]["status"] == "unknown"


def test_nio_uncomputed_metadata_reference_stays_unknown(tmp_path):
    def reference(book):
        book.create_sheet("Input")["A1"] = "P12345"
        book["CoverSheet"]["B1"] = "=Input!A1"

    facts = execute(tmp_path, extra=reference)
    assert facts["M-03"]["status"] == "unknown"
    assert facts["S-11"]["status"] == "unknown"


def test_nio_proven_empty_form_differs_from_unreadable_formula_row(tmp_path):
    def empty(book):
        book["Surface"].delete_rows(3)

    facts = execute(tmp_path, extra=empty)
    assert facts["M-06"]["status"] == "defect", facts["M-06"]
    assert facts["M-06"]["code"] == "B04"
    facts = execute(tmp_path, samples=["=1+1", 0.2, 0.1, 0.2, 0.15])
    assert facts["M-06"]["status"] == "unknown"


def test_nio_unassigned_real_samples_are_not_a_proven_empty_template(tmp_path):
    facts = execute(tmp_path, extra=lambda book: setattr(book["Surface"]["A3"], "value", None))
    assert facts["M-06"]["status"] == "unknown", facts["M-06"]
