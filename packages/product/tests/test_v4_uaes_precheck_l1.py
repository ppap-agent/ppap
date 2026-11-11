"""Independent UAES L1 facts; low business values are not preparation blocks."""

import json

from audit.checker_lib.uaes import CLASS_BY_ENTRY
from audit.contracts import AuditInput, RuleInput
from audit.precheck.sources import l1_criteria
from audit.presets import preset_rule_body
from openpyxl import Workbook


def execute(tmp_path, entry, sheets, *, edit=None):
    book = Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    if edit:
        edit(book)
    path = tmp_path / "source.xlsx"
    book.save(path)
    book.close()
    body = preset_rule_body("UAES", entry)
    checker = CLASS_BY_ENTRY[entry](AuditInput(audit_id="source-version", file_url=str(path)), RuleInput(rule_id="rules", **{name: value for name, value in body.items() if name != "kind"}))
    facts = checker.precheck()
    assert {item["id"] for item in l1_criteria(entry)} <= {item["id"] for item in facts}
    assert all(item["certified"] is False for item in facts)
    for item in facts:
        if item["status"] == "satisfied":
            assert item["evidence"]["sources"], item
        if item.get("evidence"):
            assert all(json.loads(source["locator"])["file_version_id"] == "source-version" for source in item["evidence"]["sources"])
    return {item["id"].split(":", 1)[1]: item for item in facts}


def mcr(*, upper=0.5, lower=-0.5, band=1, supplier=10, uaes=10.2, second=False, formula=False):
    header = (
        ["序号", "特性描述", "特性值", "公差上限", "公差下限", "公差带", "UAES测量方法", "供应商测量方法"]
        + [f"UAES{i}" for i in range(1, 6)]
        + [f"Supplier{i}" for i in range(1, 6)]
        + [f"差异率{i}" for i in range(1, 6)]
    )
    row = [1, "孔径", 10, upper, lower, "=1+1" if formula else band, "计量型CMM", "计量型CMM"] + [uaes] * 5 + [supplier] * 5 + [0.2] * 5
    rows = [["测量比较报告 F-PU-166"], ["零件号", "P12345"], ["零件名称", "支架"], ["供应商名称", "真实供应商"], header, row]
    if second:
        rows.append([*row])
    return {"Measurement Comparison": rows}


def test_measurement_uses_absolute_dual_measurements_and_its_own_difference_formula(tmp_path):
    facts = execute(tmp_path, "U02", mcr())
    assert facts["C-01"]["status"] == "satisfied", facts["C-01"]
    assert facts["C-13"]["status"] == "satisfied"
    assert facts["diff_pct"]["status"] == "satisfied", facts["diff_pct"]
    assert facts["M-07"]["status"] == "satisfied"
    assert facts["S-06"]["status"] == "not_checked"
    assert facts["S-06"]["code"] == "S01"


def test_measurement_wrong_band_is_source_defect_without_judging_business_20_percent(tmp_path):
    facts = execute(tmp_path, "U02", mcr(band=1.5, uaes=10.9))
    assert facts["C-13"]["status"] == "defect"
    assert facts["C-13"]["code"] == "B06"
    assert facts["diff_pct"]["status"] == "defect"
    assert not any(item["code"] == "B05" and "20%" in item["message"] for item in facts.values())


def test_measurement_duplicate_ids_still_evaluate_with_unrelated_uncached_formula(tmp_path):
    facts = execute(tmp_path, "U02", mcr(second=True, formula=True))
    assert facts["C-05"]["status"] == "defect", facts["C-05"]
    assert facts["C-05"]["code"] == "B03"
    assert facts["C-13"]["status"] == "unknown"
    assert facts["C-01"]["status"] == "unknown"


def test_measurement_zero_width_is_whole_table_gate_not_one_issue_per_row(tmp_path):
    facts = execute(tmp_path, "U02", mcr(upper=0, lower=0, band=0))
    assert facts["C-03"]["status"] == "defect"
    assert facts["C-03"]["code"] == "B05"
    assert facts["C-04"]["status"] == "satisfied"


def test_msa_partial_key_warning_and_whole_core_column_defect_are_distinct(tmp_path):
    rows = [["序号", "量具名称", "量具编号", "零件号", "测量范围", "分辨率", "分析项目"], [1, "卡尺", "G1", "P12345", "0-150", None, "计量型"], [2, "卡尺", None, "P12345", "0-150", None, "计量型"]]
    facts = execute(tmp_path, "U05", {"MSA计划表": rows})
    assert facts["MSA-PRE-17"]["status"] == "defect"
    assert facts["MSA-PRE-17"]["code"] == "W02"
    assert facts["MSA-PRE-18"]["status"] == "defect"
    assert facts["MSA-PRE-18"]["code"] == "B05"


def test_msa_acceptance_expression_exists_even_when_business_conclusion_is_negative(tmp_path):
    facts = execute(tmp_path, "U05", {"GRR报告": [["结论", "测量系统不可接受"], ["%GRR", 40], ["ndc", 3]]})
    assert facts["MSA-PRE-16"]["status"] == "satisfied", facts["MSA-PRE-16"]


def test_material_missing_header_supplier_can_use_detail_supplier(tmp_path):
    rows = [["零件号", "P12345"], ["零件名称", "支架"], ["序号", "零件号", "零件名称", "部件供应商名称", "单件重量"], [1, "P12345", "支架", "真实供应商有限公司", "10.72g"]]
    facts = execute(tmp_path, "U04", {"材料及分供方清单": rows})
    assert facts["PC-B1"]["status"] == "satisfied", facts["PC-B1"]
    assert facts["PC-C3"]["status"] == "defect"
    assert facts["PC-C3"]["code"] == "B05"
    assert facts["PC-D7"]["status"] == "unknown"
    assert facts["PC-D7"]["code"] is None


def test_material_serial_and_key_defects_are_not_business_material_opinions(tmp_path):
    rows = [["序号", "零件号", "零件名称"], [1, "P12345", "支架"], [1, "000000", "螺母"]]
    facts = execute(tmp_path, "U04", {"材料及分供方清单": rows})
    assert facts["PC-B4"]["code"] == "B04"
    assert facts["PC-C2"]["code"] == "B03"


def capability(*, cp=1, cpk=0.8, pp=1.2, ppk=0.9, lower=9.5, upper=10.5):
    report = [
        ["零件号", "P12345"],
        ["零件名称", "支架"],
        ["特性名称", "孔径"],
        ["USL", upper],
        ["LSL", lower],
        ["子组容量", 5],
        ["子组数", 10],
        ["读数次数", 50],
        ["Cp", cp],
        ["Cpk", cpk],
        ["Pp", pp],
        ["Ppk", ppk],
        ["判定", "未达标"],
    ]
    summary = [["特性名称", "CPK目标值", "CPK结果", "判断", "反应计划"], ["孔径", 1.33, cpk, "未达标", "继续研究"]]
    return {"工艺能力认证报告": report, "能力值汇总": summary}


def test_capability_low_cpk_is_handoff_and_low_count_warning(tmp_path):
    facts = execute(tmp_path, "U06", capability())
    assert facts["PRE-12"]["status"] == "satisfied", facts["PRE-12"]
    assert facts["PRE-13"]["status"] == "defect"
    assert facts["PRE-13"]["code"] == "W06"
    assert facts["customer_thresholds"]["status"] == "handoff"
    assert facts["customer_thresholds"]["code"] == "S03"
    assert facts["PRE-05"]["status"] == "satisfied"


def test_capability_impossible_cpk_gt_cp_is_material_self_contradiction(tmp_path):
    facts = execute(tmp_path, "U06", capability(cp=0.5, cpk=0.8))
    assert facts["PRE-12"]["code"] == "B06"


def test_capability_uncached_field_is_unknown_not_supplier_ng(tmp_path):
    sheets = capability()
    sheets["工艺能力认证报告"][9] = ["Cpk", "=1+1"]
    facts = execute(tmp_path, "U06", sheets)
    assert facts["PRE-02"]["status"] == "unknown"
    assert facts["PRE-12"]["status"] == "unknown"


def inspection(*, result="OK", actual=10):
    return {"尺寸检验报告": [["检验报告"], ["序号", "要求", "结果", "实测值1", "实测值2", "实测值3", "实测值4", "实测值5"], [1, "10±0.1", result, actual, actual, actual, actual, actual]]}


def test_inspection_precheck_does_not_judge_tolerance_or_repeated_measurements(tmp_path):
    facts = execute(tmp_path, "U01", inspection(actual=99))
    assert facts["IR-08"]["status"] == "satisfied", facts["IR-08"]
    assert facts["IR-10"]["status"] == "satisfied", facts["IR-10"]
    assert facts["IR-17"]["status"] == "satisfied"
    assert facts["IR-09"]["status"] == "handoff"
    assert facts["IR-11"]["status"] == "handoff"


def test_inspection_pass_is_not_legal_supplier_ok(tmp_path):
    facts = execute(tmp_path, "U01", inspection(result="PASS"))
    assert facts["IR-10"]["status"] == "defect"
    assert facts["IR-10"]["code"] == "W03"


def test_uaes_inspection_does_not_apply_li_auto_title_methods_or_checkbox_requirements(tmp_path):
    sheets = inspection()
    sheets["尺寸检验报告"][0] = ["客户自定义标题"]
    facts = execute(tmp_path, "U01", sheets)
    for identifier in ("IR-02", "IR-05", "IR-06", "IR-07", "IR-12"):
        assert facts[identifier]["status"] == "not_checked"
        assert facts[identifier]["code"] == "S02"
    assert facts["IR-35"]["status"] == "satisfied"


def test_negative_inspection_word_is_parseable_but_illegal_in_its_separate_criterion(tmp_path):
    facts = execute(tmp_path, "U01", inspection(result="NOK"))
    assert facts["IR-10"]["status"] == "defect"
    assert facts["IR-35"]["status"] == "satisfied"


def test_uaes_material_three_columns_are_not_li_auto_six_columns(tmp_path):
    rows = [["UAES项目编号", "CPJ-100"], ["IMDS表单ID号", "IMDS-123"], ["序号", "零件号", "零件名称", "材料分类", "材料标准", "材料名称和牌号"], [1, "P12345", "支架", "钢材", "GB/T 699", "45钢"]]
    facts = execute(tmp_path, "U04", {"材料及分供方清单": rows})
    assert facts["PC-A5"]["status"] == "satisfied", facts["PC-A5"]
    assert facts["PC-B8"]["status"] == "satisfied"
    assert facts["PC-D3"]["status"] == "satisfied"


def test_uaes_supplier_detail_column_is_an_authorized_primary_location(tmp_path):
    rows = [["零件号", "P12345"], ["零件名称", "支架"], ["序号", "零件号", "零件名称", "供应商Supplier"], [1, "P12345", "支架", "真实供应商有限公司"]]
    facts = execute(tmp_path, "U04", {"材料及分供方清单": rows})
    assert facts["PC-B1"]["status"] == "satisfied", facts["PC-B1"]


def test_msa_native_percentage_style_is_not_mistaken_for_fractional_wrong_units(tmp_path):
    def style(book):
        book.active["B1"].number_format = "0.0%"

    facts = execute(tmp_path, "U05", {"GRR报告": [["%GRR", 0.4], ["ndc", 8]]}, edit=style)
    assert facts["MSA-PRE-20"]["status"] == "satisfied", facts["MSA-PRE-20"]
    facts = execute(tmp_path, "U05", {"GRR报告": [["%GRR", 0.4], ["ndc", 8]]})
    assert facts["MSA-PRE-20"]["code"] == "W03"


def test_msa_source_design_constants_are_not_engineering_grr_thresholds(tmp_path):
    rows = [["重复次数", 3], ["评价人数", 3], ["零件数", 10], ["K1", 0.1], ["K2", 0.5231], ["K3", 0.3146], ["D4", 2.575], ["%GRR", 40], ["ndc", 3], ["结论", "不可接受"]]
    facts = execute(tmp_path, "U05", {"GRR报告": rows})
    assert facts["MSA-PRE-12"]["code"] == "B06"
    assert facts["MSA-PRE-16"]["status"] == "satisfied"


def test_comparison_derived_excel_error_is_warning_while_basis_remains_readable(tmp_path):
    sheets = mcr()
    sheets["Measurement Comparison"][-1][-1] = "#DIV/0!"
    facts = execute(tmp_path, "U02", sheets)
    assert facts["C-10"]["status"] == "satisfied", facts["C-10"]
    assert facts["C-17"]["status"] == "defect"
    assert facts["C-17"]["code"] == "W05"


def test_comparison_criteria_emit_all_twenty_three_element_observations(tmp_path):
    facts = execute(tmp_path, "U02", mcr())
    assert all("M-02:E" + f"{index:02d}" in facts for index in range(1, 24))
    assert facts["M-02:E03"]["status"] == "satisfied"
    assert facts["M-02:E16"]["status"] == "not_checked"
    assert facts["M-02:E18"]["status"] == "not_checked"


def test_capability_explicit_excel_error_is_not_uncached_formula(tmp_path):
    sheets = capability()
    sheets["工艺能力认证报告"][9] = ["Cpk", "#REF!"]
    facts = execute(tmp_path, "U06", sheets)
    assert facts["PRE-09"]["status"] == "defect"
    assert facts["PRE-09"]["code"] == "B05"
    assert facts["PRE-02"]["status"] == "unknown"


def test_capability_requires_its_summary_and_does_not_relabel_certificate_as_single_page(tmp_path):
    sheets = capability()
    del sheets["能力值汇总"]
    facts = execute(tmp_path, "U06", sheets)
    assert facts["PRE-05"]["status"] == "unknown"
    assert facts["PRE-05"]["code"] == "S05"
    assert facts["PRE-08"]["status"] == "not_checked"


def test_comparison_example_marker_in_id_never_makes_empty_template_a_valid_report(tmp_path):
    sheets = mcr()
    sheets["Measurement Comparison"][-1][0] = "i.e."
    sheets["Measurement Comparison"].append([1, None, None, None, None, 0, None, None] + [None] * 10 + [7] * 5)
    facts = execute(tmp_path, "U02", sheets)
    assert facts["M-06"]["status"] == "defect", facts["M-06"]
    assert facts["M-06"]["code"] == "B04"


def test_msa_percent_sign_does_not_hide_out_of_range_values(tmp_path):
    for value in ("120%", "-1%"):
        facts = execute(tmp_path, "U05", {"GRR报告": [["%GRR", value], ["ndc", 8]]})
        assert facts["MSA-PRE-20"]["code"] == "W03"
    facts = execute(tmp_path, "U05", {"GRR报告": [["%GRR", "0.4%"], ["ndc", 8]]})
    assert facts["MSA-PRE-20"]["status"] == "satisfied"


def test_measurement_batch_checks_use_only_frozen_typed_originals(tmp_path):
    from audit.checker_lib.uaes.precheck_l1 import Facts, _mcr_batch
    from audit.contracts import GroupContextInput

    def original(filename, version, date_value, stage="PP"):
        book = Workbook()
        for row in [["零件号", "P12345"], ["项目号", "CPJ-100"], ["版本号", version], ["供应商名称", "真实供应商"], ["项目阶段", stage], ["批次号", filename], ["报告日期", date_value]]:
            book.active.append(row)
        path = tmp_path / filename
        book.save(path)
        book.close()
        return AuditInput(audit_id=filename, file_url=str(path), file_metadata={"original_filename": filename})

    current = original("测量当前.xlsx", "V1", "2026-11-01")
    other = original("测量前批.xlsx", "V2", "2026-10-01")
    context = GroupContextInput(by_audit_id={current.audit_id: current, other.audit_id: other}, by_doc_type={"E02": [current, other]})
    body = preset_rule_body("UAES", "U02")
    agent = CLASS_BY_ENTRY["U02"](current, RuleInput(rule_id="rules", **{name: value for name, value in body.items() if name != "kind"}), context)
    facts = Facts(current)
    result = facts.observe("U02:S-15", lambda: _mcr_batch(facts, agent, history=True))
    assert result["status"] == "defect"
    assert result["code"] == "B02"
    assert {json.loads(source["locator"])["file_version_id"] for source in result["evidence"]["sources"]} == {current.audit_id, other.audit_id}
    context.unavailable_files = {"late-file": "E02"}
    result = facts.observe("U02:S-15", lambda: _mcr_batch(facts, agent, history=True))
    assert result["status"] == "error"


def test_dual_form_filename_and_part_are_not_enough_to_establish_project_and_customer(tmp_path):
    from audit.checker_lib.uaes.precheck_l1 import _paired_original_fact
    from audit.contracts import GroupContextInput
    from docx import Document

    rows = [["零件号", "P12345"], ["零件名称", "支架"], ["版本号", "A"]]
    book = Workbook()
    for row in rows:
        book.active.append(row)
    target_path = tmp_path / "pair.xlsx"
    book.save(target_path)
    book.close()
    document = Document()
    table = document.add_table(rows=3, cols=2)
    for number, row in enumerate(rows):
        for column, value in enumerate(row):
            table.cell(number, column).text = value
    reference_path = tmp_path / "pair.docx"
    document.save(reference_path)
    target = AuditInput(audit_id="target", file_url=str(target_path), file_metadata={"original_filename": "pair.xlsx"})
    reference = AuditInput(audit_id="reference", file_url=str(reference_path), file_metadata={"original_filename": "pair.docx"})
    body = preset_rule_body("UAES", "U02")
    agent = CLASS_BY_ENTRY["U02"](
        target, RuleInput(rule_id="rules", **{name: value for name, value in body.items() if name != "kind"}), GroupContextInput(by_audit_id={"target": target, "reference": reference})
    )
    result = _paired_original_fact(agent, "U02:S-08", measurement=True)
    assert result["status"] == "unknown"
    assert result["code"] is None
