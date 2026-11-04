"""Independent source examples for fixed Li L1 preparation predicates."""

from types import SimpleNamespace

import pytest
from audit.checkers.v4.li_auto.precheck_inspection_validation import operations
from audit.checkers.v4.li_auto.precheck_l1 import L1Facts
from audit.contracts import AuditInput, GroupContextInput
from audit.precheck_sources import l1_criteria
from openpyxl import Workbook


def original(tmp_path, rows, *, sheets=None, filename="original.xlsx", audit_id="one"):
    path = tmp_path / filename
    workbook = Workbook()
    for name, records in (sheets or {"检验报告": rows}).items():
        sheet = workbook.active if workbook.active.max_row == 1 and workbook.active["A1"].value is None else workbook.create_sheet()
        sheet.title = name
        for record in records:
            sheet.append(record)
    workbook.save(path)
    audit = AuditInput(audit_id=audit_id, file_url=str(path), customer="理想", file_metadata={"original_filename": filename})
    return audit


def result(audit, entry, criterion, *, group=None, agent=None):
    facts = L1Facts(audit)
    agent = agent or SimpleNamespace(audit_file=audit, group_context=group)
    return facts.observe(entry + ":" + criterion, operations(agent, entry, facts)[criterion])


def ir_rows(standard="10±0.2", actual=10, method="卡尺", verdict="OK"):
    return [["序号", "判定标准", "检查方法", "检查数据", "判定结果"], [1, standard, method, actual, verdict]]


@pytest.mark.parametrize("entry", ["L01", "L02", "L03"])
def test_operations_have_authoritative_source_ids(tmp_path, entry):
    audit = original(tmp_path, ir_rows())
    facts = L1Facts(audit)
    mapping = operations(SimpleNamespace(audit_file=audit), entry, facts)
    assert mapping
    assert mapping.keys() <= {item["criterion_id"] for item in l1_criteria(entry)}


@pytest.mark.parametrize("criterion", ["IR-18", "IR-26", "IR-28", "IR-30", "IR-31", "IR-33", "IR-34", "IR-35"])
def test_structured_inspection_positive_has_original_evidence(tmp_path, criterion):
    observed = result(original(tmp_path, ir_rows()), "L01", criterion)
    assert observed["status"] == "satisfied", observed
    assert observed["evidence"]["sources"]
    assert observed["certified"] is False


@pytest.mark.parametrize(
    "criterion,rows,code",
    [
        ("IR-26", [["序号", "判定标准", "检查方法", "检查数据", "判定结果"], [2, "10±0.2", "卡尺", 10, "OK"]], "W06"),
        ("IR-30", ir_rows("2×Φ10±0.2"), "W06"),
        ("IR-34", ir_rows(actual="34..19"), "W03"),
        ("IR-34", ir_rows(actual="10\n\n11"), "W03"),
        ("IR-35", ir_rows(verdict="正确无误"), "W03"),
        ("IR-12", ir_rows(actual="OK"), "W06"),
    ],
)
def test_inspection_source_defects(tmp_path, criterion, rows, code):
    observed = result(original(tmp_path, rows), "L01", criterion)
    assert observed["status"] == "defect", observed
    assert observed["code"] == code


def test_li_qualitative_dimension_exception(tmp_path):
    observed = result(original(tmp_path, ir_rows(actual="OK", method="通止规")), "L01", "IR-12")
    assert observed["status"] == "satisfied", observed


def test_missing_formula_cache_is_unknown(tmp_path):
    observed = result(original(tmp_path, ir_rows(actual="=5+5")), "L01", "IR-34")
    assert observed["status"] == "unknown", observed


def test_project_full_check_needs_actual_full_and_expected_list(tmp_path):
    observed = result(original(tmp_path, [["提交范围", "PART"]]), "L01", "IR-01b")
    assert (observed["status"], observed["code"]) == ("not_checked", "S01")


def test_separate_signature_names_required(tmp_path):
    observed = result(original(tmp_path, [["检验人", "张三"], ["审核人", "张三"], ["批准人", "李四"]]), "L01", "IR-23")
    assert (observed["status"], observed["code"]) == ("defect", "W06")


@pytest.mark.parametrize("end,expected", [("2026-01-03", "satisfied"), ("2026-03-03", "defect")])
def test_signature_timeline_30_days(tmp_path, end, expected):
    audit = original(tmp_path, [["检验日期", "2026-01-01"], ["审核日期", "2026-01-02"], ["批准日期", end]])
    assert result(audit, "L01", "IR-22")["status"] == expected


def test_zero_weight_is_not_empty_or_pass(tmp_path):
    audit = original(tmp_path, [["客户零件号", "PN1"], ["供应商零件号", "S1"], ["版本", "A"], ["零件名称", "轴"], ["重量", 0]])
    observed = result(audit, "L01", "IR-20")
    assert (observed["status"], observed["code"]) == ("defect", "W03")


@pytest.mark.parametrize("unit,expected", [("MPa", "satisfied"), ("Mpa", "defect"), (None, "defect")])
def test_threshold_unit_case_and_absence(tmp_path, unit, expected):
    audit = original(tmp_path, [["判定阈值", "单位"], ["10±1", unit]])
    assert result(audit, "L02", "COR-05")["status"] == expected


def test_scope_absent_warns_without_blocking(tmp_path):
    observed = result(original(tmp_path, [["报告编号", "R1"]]), "L02", "COM-01")
    assert (observed["status"], observed["code"]) == ("defect", "W01")


def test_explicit_filename_part_mismatch(tmp_path):
    audit = original(tmp_path, [["零件号", "PN2"]], filename="PN=PN1.xlsx")
    observed = result(audit, "L03", "CON-04")
    assert (observed["status"], observed["code"]) == ("defect", "B06")


def test_arbitrary_filename_is_not_invented_part_identifier(tmp_path):
    audit = original(tmp_path, [["零件号", "PN2"]], filename="report-old-error.xlsx")
    assert result(audit, "L03", "CON-04")["status"] == "unknown"


def test_report_year_threshold_two_years(tmp_path):
    audit = original(tmp_path, [["报告编号", "LAB-2022-A"], ["签发日期", "2026-01-01"]])
    observed = result(audit, "L03", "CON-09")
    assert (observed["status"], observed["code"]) == ("defect", "W04")


def test_summary_detail_cardinality(tmp_path):
    audit = original(tmp_path, [], sheets={"汇总": [["试验项目"], ["盐雾"], ["振动"]], "详情": [["试验项目"], ["盐雾"]]})
    observed = result(audit, "L03", "CON-06")
    assert (observed["status"], observed["code"]) == ("defect", "W04")


@pytest.mark.parametrize("other_part", ["PN2", "PN-1"])
def test_other_part_in_group_is_not_automatically_conflict(tmp_path, other_part):
    one = original(tmp_path, [["零件号", "PN1"]])
    two = original(tmp_path, [["零件号", other_part]], filename="other.xlsx", audit_id="two")
    group = GroupContextInput(task_group_id="task", by_audit_id={"one": one, "two": two})
    assert result(one, "L03", "CON-10", group=group)["status"] == "unknown"


def test_same_part_cross_report_conflict_cites_both(tmp_path):
    one = original(tmp_path, [["零件号", "PN1"], ["车型", "W01"], ["供应商", "S1"], ["版本", "A"]])
    two = original(tmp_path, [["零件号", "PN1"], ["车型", "W02"], ["供应商", "S1"], ["版本", "A"]], filename="other.xlsx", audit_id="two")
    group = GroupContextInput(task_group_id="task", by_audit_id={"one": one, "two": two})
    observed = result(one, "L03", "CON-10", group=group)
    assert (observed["status"], observed["code"]) == ("defect", "W04")
    assert {__import__("json").loads(source["locator"])["file_version_id"] for source in observed["evidence"]["sources"]} == {"one", "two"}


@pytest.mark.parametrize("criterion", ["COM-07", "COM-13"])
def test_office_visual_labels_do_not_prove_actual_objects(tmp_path, criterion):
    audit = original(tmp_path, [["印章", "印章图片"], ["试验前", "照片"], ["试验中", "照片"], ["试验后", "照片"]])
    assert result(audit, "L03", criterion)["status"] == "unknown"


def test_pdf_actual_page_count_is_not_declared_total(tmp_path):
    import fitz

    path = tmp_path / "report.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((100, page.rect.height - 20), "1/888")
        doc.save(path)
    audit = AuditInput(audit_id="pdf", file_url=str(path), customer="理想")
    observed = result(audit, "L03", "CON-07")
    assert (observed["status"], observed["code"]) == ("defect", "W04"), observed


def test_declared_scope_missing_fields_is_b02(tmp_path):
    observed = result(original(tmp_path, [["提交范围", "PART"]]), "L02", "COM-01")
    assert (observed["status"], observed["code"]) == ("defect", "B02")


def test_declared_scope_matches_actual_frozen_file_list(tmp_path):
    audit = original(tmp_path, [["提交范围", "PART"], ["项目标识", "P1"], ["PPAP项清单", "DVP"], ["提交文件清单", "original.xlsx"]])
    group = GroupContextInput(task_group_id="task", by_audit_id={"one": audit})
    assert result(audit, "L02", "COM-01", group=group)["status"] == "satisfied"


def test_same_object_multiple_versions_need_written_choice(tmp_path):
    one = original(tmp_path, [["报告编号", "R1"], ["零件号", "P1"], ["版本", "A"]])
    two = original(tmp_path, [["报告编号", "R1"], ["零件号", "P1"], ["版本", "B"]], filename="other.xlsx", audit_id="two")
    group = GroupContextInput(task_group_id="task", by_audit_id={"one": one, "two": two})
    observed = result(one, "L03", "COM-02", group=group)
    assert (observed["status"], observed["code"]) == ("defect", "B02")


def test_independent_repeated_serial_is_a_gap(tmp_path):
    rows = ir_rows() + [[1, "20±0.2", "卡尺", 20, "OK"]]
    observed = result(original(tmp_path, rows), "L01", "IR-26")
    assert (observed["status"], observed["code"]) == ("defect", "W06")


def test_declared_borrowing_downgrades_timeline_to_record(tmp_path):
    rows = [["试验项目", "计划开始", "计划结束", "试验实际开始日期", "试验实际结束日期"], ["振动", "2026-04-01", "2026-04-10", "2026-01-01", "2026-07-01"], ["借用说明", "按本批声明借用既有试验"]]
    observed = result(original(tmp_path, rows), "L02", "CON-10")
    assert observed["status"] == "satisfied", observed


def test_pdf_visual_missing_seal_uses_li_warning(tmp_path):
    import fitz

    path = tmp_path / "visual.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(path)
    audit = AuditInput(audit_id="pdf", file_url=str(path), customer="理想")
    agent = SimpleNamespace(
        _original_visual_facts=lambda: {"seal": False, "before": True, "during": True, "after": True},
        _original_visual_sources=[{"kind": "visual", "locator": '{"kind":"pdf_page","page":1,"file_version_id":"pdf"}', "excerpt": "完整原件"}],
    )
    observed = result(audit, "L03", "COM-13", agent=agent)
    assert (observed["status"], observed["code"]) == ("defect", "W01")


def test_visual_unknown_never_becomes_photograph_pass(tmp_path):
    import fitz

    path = tmp_path / "visual.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(path)
    audit = AuditInput(audit_id="pdf", file_url=str(path), customer="理想")
    agent = SimpleNamespace(_original_visual_facts=lambda: {"before": True, "during": None, "after": True}, _original_visual_sources=[])
    assert result(audit, "L03", "COM-07", agent=agent)["status"] == "unknown"


@pytest.mark.parametrize("peer_value,expected", [(10, "satisfied"), (-10, "defect")])
def test_same_source_same_layout_compares_actual_values_not_only_identity(tmp_path, peer_value, expected):
    rows = [["报告编号", "R1"], ["零件号", "P1"], ["版本", "A"], ["车型", "W01"], ["供应商", "S1"], ["实测值", 10]]
    one = original(tmp_path, rows)
    two = original(tmp_path, [*rows[:-1], ["实测值", peer_value]], filename="other.xlsx", audit_id="two")
    group = GroupContextInput(task_group_id="task", by_audit_id={"one": one, "two": two})
    observed = result(one, "L02", "CON-01", group=group)
    assert observed["status"] == expected, observed
    if expected == "defect":
        assert observed["code"] == "B06"
        import json

        assert {json.loads(source["locator"])["file_version_id"] for source in observed["evidence"]["sources"]} == {"one", "two"}


def trial(tmp_path, *, filename="trial.xlsx", audit_id="one", rows=None):
    path = tmp_path / filename
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "DVP&R"
    for row in rows or [["试验项目", "实际试验结果", "备注"], ["振动", "合格", None]]:
        sheet.append(row)
    workbook.save(path)
    return AuditInput(audit_id=audit_id, file_url=str(path), customer="理想", file_metadata={"original_filename": filename})


def edit_native(audit, edit):
    from openpyxl import load_workbook

    workbook = load_workbook(audit.file_url)
    edit(workbook)
    workbook.save(audit.file_url)


def dropdown(audit, formula, *, ranges=("B2",)):
    from openpyxl.worksheet.datavalidation import DataValidation

    def edit(workbook):
        validation = DataValidation(type="list", formula1=formula)
        workbook.active.add_data_validation(validation)
        for address in ranges:
            validation.add(address)

    edit_native(audit, edit)


def batch(*audits):
    return GroupContextInput(task_group_id="task", by_audit_id={audit.audit_id: audit for audit in audits})


def test_native_inline_dropdown_dictionary_positive(tmp_path):
    audit = trial(tmp_path)
    dropdown(audit, '"合格,视同,偏差认可"')
    observed = result(audit, "L02", "CON-06", group=batch(audit))
    assert observed["status"] == "satisfied", observed
    assert any("原生数据验证" in source["excerpt"] for source in observed["evidence"]["sources"])


def test_native_conflicting_dropdowns_in_batch_are_warning_not_authority(tmp_path):
    one = trial(tmp_path)
    two = trial(tmp_path, filename="two.xlsx", audit_id="two")
    dropdown(one, '"合格,视同,偏差认可"')
    dropdown(two, '"OK,NG"')
    observed = result(one, "L02", "CON-06", group=batch(one, two))
    assert (observed["status"], observed["code"]) == ("defect", "W03"), observed
    assert observed["certified"] is False
    assert "规则枚举仍为权威" in observed["message"]
    import json

    assert {json.loads(source["locator"])["file_version_id"] for source in observed["evidence"]["sources"]} == {"one", "two"}


@pytest.mark.parametrize("named", [False, True])
def test_native_dropdown_static_range_or_defined_name_reads_original_cells(tmp_path, named):
    from openpyxl.workbook.defined_name import DefinedName

    audit = trial(tmp_path)

    def edit(workbook):
        sheet = workbook.create_sheet("结果字典")
        for word in ("合格", "视同", "偏差认可"):
            sheet.append([word])
        if named:
            workbook.defined_names.add(DefinedName("ResultOptions", attr_text="'结果字典'!$A$1:$A$3"))

    edit_native(audit, edit)
    dropdown(audit, "=ResultOptions" if named else "='结果字典'!$A$1:$A$3")
    observed = result(audit, "L02", "CON-06", group=batch(audit))
    assert observed["status"] == "satisfied", observed
    assert any("结果字典" in source["locator"] for source in observed["evidence"]["sources"])


@pytest.mark.parametrize("formula", ["=OFFSET(A1,0,0,3,1)", "='[outside.xlsx]Sheet1'!$A$1:$A$3", "=MissingName"])
def test_dynamic_external_or_missing_dropdown_is_unknown_without_evaluation(tmp_path, formula):
    audit = trial(tmp_path)
    dropdown(audit, formula)
    assert result(audit, "L02", "CON-06", group=batch(audit))["status"] == "unknown"


def test_native_formula_option_without_cached_value_is_unknown(tmp_path):
    audit = trial(tmp_path)

    def edit(workbook):
        sheet = workbook.create_sheet("结果字典")
        sheet.append(['="合格"'])

    edit_native(audit, edit)
    dropdown(audit, "='结果字典'!$A$1:$A$1")
    assert result(audit, "L02", "CON-06", group=batch(audit))["status"] == "unknown"


def test_no_dropdown_does_not_become_missing_rule_dictionary_defect(tmp_path):
    audit = trial(tmp_path)
    observed = result(audit, "L02", "CON-06", group=batch(audit))
    assert (observed["status"], observed["code"]) == ("not_checked", "S01")


def test_other_field_dropdown_is_not_conflicting_result_dictionary(tmp_path):
    audit = trial(tmp_path)
    dropdown(audit, '"合格,视同,偏差认可"')
    dropdown(audit, '"DV,PV"', ranges=("A2",))
    assert result(audit, "L02", "CON-06", group=batch(audit))["status"] == "satisfied"


def test_native_subset_dictionary_not_assumed_mutually_exclusive(tmp_path):
    one = trial(tmp_path)
    two = trial(tmp_path, filename="two.xlsx", audit_id="two")
    dropdown(one, '"合格,视同,偏差认可"')
    dropdown(two, '"合格"')
    assert result(one, "L02", "CON-06", group=batch(one, two))["status"] == "unknown"


@pytest.mark.parametrize("location", ["comment", "outside", "remark"])
def test_actual_native_unexpected_explanation_locations_warn(tmp_path, location):
    from openpyxl.comments import Comment

    audit = trial(tmp_path)

    def edit(workbook):
        sheet = workbook.active
        if location == "comment":
            sheet["B2"].comment = Comment("申请豁免：此项按项目说明不再执行", "supplier")
        elif location == "outside":
            sheet["E2"] = "借用 W01 的振动报告"
        else:
            sheet["C2"] = "本试验取消"

    edit_native(audit, edit)
    observed = result(audit, "L02", "COM-08")
    assert (observed["status"], observed["code"]) == ("defect", "W03"), observed
    assert any("批注" in source["excerpt"] or "表外" in source["excerpt"] for source in observed["evidence"]["sources"])


def test_native_comments_without_explanation_terms_are_not_defects(tmp_path):
    from openpyxl.comments import Comment

    audit = trial(tmp_path)
    edit_native(audit, lambda workbook: setattr(workbook.active["B2"], "comment", Comment("试验按已批准计划进行", "supplier")))
    assert result(audit, "L02", "COM-08")["status"] == "satisfied"


@pytest.mark.parametrize("has_legend,expected", [(True, "defect"), (False, "unknown")])
def test_native_red_fill_needs_explicit_cancellation_legend(tmp_path, has_legend, expected):
    from openpyxl.styles import PatternFill

    audit = trial(tmp_path)

    def edit(workbook):
        sheet = workbook.active
        for cell in sheet[2]:
            cell.fill = PatternFill("solid", fgColor="FFFF0000")
        if has_legend:
            sheet["A4"] = "颜色说明"
            sheet["B4"] = "红色表示取消"

    edit_native(audit, edit)
    observed = result(audit, "L02", "COM-08")
    assert observed["status"] == expected, observed
    if has_legend:
        assert observed["code"] == "W03"
        assert any("原生实心底色" in source["excerpt"] for source in observed["evidence"]["sources"])


def test_clear_native_explanation_locations_can_pass(tmp_path):
    audit = trial(tmp_path)
    assert result(audit, "L02", "COM-08")["status"] == "satisfied"


def test_provided_separate_list_suppresses_unexpected_comment_warning(tmp_path):
    from openpyxl.comments import Comment

    audit = trial(tmp_path)
    edit_native(audit, lambda workbook: setattr(workbook.active["B2"], "comment", Comment("申请豁免", "supplier")))
    explanation = original(tmp_path, [["豁免说明清单", "振动：经项目说明予以豁免"]], filename="explanation.xlsx", audit_id="explanation")
    assert result(audit, "L02", "COM-08", group=batch(audit, explanation))["status"] == "satisfied"


def test_structured_execution_status_prevents_only_in_comment_warning(tmp_path):
    from openpyxl.comments import Comment

    audit = trial(tmp_path, rows=[["试验项目", "实际试验结果", "备注", "计划执行状态"], ["振动", "合格", None, "取消"]])
    edit_native(audit, lambda workbook: setattr(workbook.active["B2"], "comment", Comment("本试验取消", "supplier")))
    assert result(audit, "L02", "COM-08")["status"] == "satisfied"


def test_unrelated_separate_list_cannot_exempt_native_comment(tmp_path):
    from openpyxl.comments import Comment

    audit = trial(tmp_path)
    edit_native(audit, lambda workbook: setattr(workbook.active["B2"], "comment", Comment("申请豁免", "supplier")))
    explanation = original(tmp_path, [["豁免说明清单", "盐雾：经项目说明予以豁免"]], filename="explanation.xlsx", audit_id="explanation")
    observed = result(audit, "L02", "COM-08", group=batch(audit, explanation))
    assert (observed["status"], observed["code"]) == ("defect", "W03")


def test_conditional_color_is_unknown_without_running_excel_formulas(tmp_path):
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import PatternFill

    audit = trial(tmp_path)
    edit_native(audit, lambda workbook: workbook.active.conditional_formatting.add("A2:C2", FormulaRule(formula=['B2="合格"'], fill=PatternFill("solid", fgColor="FFFF0000"))))
    assert result(audit, "L02", "COM-08")["status"] == "unknown"


def test_empty_native_trial_range_is_not_explanation_pass(tmp_path):
    audit = trial(tmp_path, rows=[["试验项目", "实际试验结果", "备注"]])
    assert result(audit, "L02", "COM-08")["status"] == "unknown"


def test_native_dropdown_default_options_exist_without_filled_trials(tmp_path):
    audit = trial(tmp_path, rows=[["试验项目", "实际试验结果", "备注"]])
    dropdown(audit, '"合格,视同,偏差认可"', ranges=("B2:B100",))
    observed = result(audit, "L02", "CON-06", group=batch(audit))
    assert observed["status"] == "satisfied", observed


def test_native_below_table_block_with_exact_trial_key_warns(tmp_path):
    audit = trial(tmp_path)
    edit_native(audit, lambda workbook: setattr(workbook.active["E4"], "value", "振动：申请豁免"))
    observed = result(audit, "L02", "COM-08")
    assert (observed["status"], observed["code"]) == ("defect", "W03"), observed


def test_native_orphan_comment_not_silently_skipped_or_guessed_as_defect(tmp_path):
    from openpyxl.comments import Comment

    audit = trial(tmp_path)
    edit_native(audit, lambda workbook: setattr(workbook.active["E4"], "comment", Comment("申请豁免", "supplier")))
    assert result(audit, "L02", "COM-08")["status"] == "unknown"


def test_duplicate_trial_names_do_not_let_general_list_exempt_all(tmp_path):
    from openpyxl.comments import Comment

    audit = trial(tmp_path, rows=[["试验项目", "实际试验结果", "备注"], ["振动", "合格", None], ["振动", "合格", None]])
    edit_native(audit, lambda workbook: setattr(workbook.active["B2"], "comment", Comment("申请豁免", "supplier")))
    explanation = original(tmp_path, [["豁免说明清单", "振动：经项目说明予以豁免"]], filename="explanation.xlsx", audit_id="explanation")
    assert result(audit, "L02", "COM-08", group=batch(audit, explanation))["status"] == "unknown"
