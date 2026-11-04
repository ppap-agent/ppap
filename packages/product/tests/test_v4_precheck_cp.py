"""Independent original CP inputs separate preparation defects from engineering verdicts."""

from __future__ import annotations

import pytest
from audit.contracts import AuditInput
from audit.precheck_cp import cp_precheck
from openpyxl import Workbook


def original(tmp_path, *, uaes=False, capacity="10%", frequency="2h", specification="[9,11]", equipment="机床", measurement="卡尺", symbol="CC", empty_header=False, formula=False, reverse=False):
    book = Workbook()
    sheet = book.active
    sheet.title = "控制计划"
    headers = (
        ["供应商名称", "供应商代码", "编制人", "版本号", "受控日期", "零件名称", "零件号"]
        if uaes
        else [
            "控制计划类型",
            "控制计划编号",
            "联系人",
            "电话",
            "编制日期",
            "修订日期",
            "版本",
            "页数",
            "页码",
            "零件号",
            "零件名称",
            "核心小组",
            "公司名称",
            "公司代码",
            "公司批准",
            "顾客工程批准",
            "顾客质量批准",
            "其它批准",
        ]
    )
    for label in headers:
        value = "" if empty_header and label == "零件名称" else "已填事实"
        if label == "核心小组":
            value = "制造、质量"
        sheet.append([label, value])
    if not uaes:
        sheet.append(["批准日期", "2025-03-01"])
    columns = ["工序号", "工序名称", "生产设备", "特性分类", "产品特性", "过程特性", "规格/公差", "评价测量技术", "样本容量", "频次", "控制方法", "反应计划", "特性编号"]
    if uaes:
        columns = [name for name in columns if name not in {"产品特性", "过程特性", "频次", "控制方法"}]
    sheet.append(columns)
    for index in range(3):
        data = {
            "工序号": (3 - index if reverse else index + 1) * 10,
            "工序名称": "加工" + str(index),
            "生产设备": equipment,
            "特性分类": symbol,
            "产品特性": "尺寸",
            "过程特性": "参数",
            "规格/公差": specification,
            "评价测量技术": measurement,
            "样本容量": "=1/0" if formula else capacity,
            "频次": frequency,
            "控制方法": "SPC",
            "反应计划": "停机报告",
            "特性编号": 1,
        }
        sheet.append([data[name] for name in columns])
    path = tmp_path / "control-plan.xlsx"
    book.save(path)
    book.close()
    return AuditInput(audit_id="frozen-cp-version", file_url=str(path))


def checked(audit, entry="N03"):
    return {row["id"].split(":", 1)[1]: row for row in cp_precheck(audit, entry)}


def test_uaes_seven_headers_do_not_gain_eleven_other_customer_requirements(tmp_path):
    result = checked(original(tmp_path, uaes=True), "U08")
    assert result["M-01"]["status"] == "satisfied"
    assert result["M-04"]["status"] == "satisfied"
    assert result["M-02"]["status"] == "satisfied"
    assert result["C-11"]["code"] == "S02"
    assert result["C-09"]["code"] == "S02"
    assert result["P-05"]["code"] == "S01"


def test_structure_and_empty_header_are_separate_facts(tmp_path):
    result = checked(original(tmp_path, empty_header=True))
    assert result["M-01"]["status"] == "satisfied"
    assert result["M-02"]["status"] == "defect" and result["M-02"]["code"] == "W02"
    assert result["M-14"]["code"] == "S02"


@pytest.mark.parametrize("capacity", ["101%", "0%", "0件"])
def test_definite_capacity_domain_is_a_preparation_fact(tmp_path, capacity):
    result = checked(original(tmp_path, capacity=capacity))
    assert result["C-01"]["status"] == "defect" and result["C-01"]["code"] == "B05"
    assert result["C-01"]["certified"] is False
    assert result["C-01"]["evidence"]["sources"]


def test_clear_unit_swaps_and_interval_math_are_not_engineering_ng(tmp_path):
    result = checked(original(tmp_path, capacity="2h", frequency="10件", specification="[11,9]"))
    assert result["C-02"]["status"] == "defect" and result["C-02"]["code"] == "B05"
    assert result["C-03"]["status"] == "defect" and result["C-03"]["code"] == "B06"


def test_uncached_formula_is_unknown_and_never_means_zero_capacity(tmp_path):
    result = checked(original(tmp_path, formula=True))
    assert result["C-01"]["status"] == "unknown" and result["C-01"]["code"] is None
    assert result["M-04"]["status"] == "unknown"


def test_equipment_and_measurement_fill_rates_keep_distinct_severity(tmp_path):
    result = checked(original(tmp_path, equipment="", measurement=""))
    assert result["M-06"]["code"] == "B05" and result["M-06"]["critical"] is True
    assert result["M-07"]["code"] == "W02" and result["M-07"]["critical"] is False


def test_process_order_and_empty_classification_are_independent(tmp_path):
    result = checked(original(tmp_path, reverse=True, symbol=""))
    assert result["C-18"]["code"] == "W03"
    assert result["M-05"]["code"] == "B05"


def test_mixed_nominal_and_interval_must_be_consistent_even_between_valid_rows(tmp_path):
    from openpyxl import load_workbook

    audit = original(tmp_path)
    book = load_workbook(audit.file_url)
    book.active["G22"] = "10±1 [8,12]"
    book.save(audit.file_url)
    book.close()
    fact = checked(audit)["C-03"]
    assert (fact["status"], fact["code"]) == ("defect", "B06")


def test_unparsed_specification_cannot_hide_between_parseable_intervals(tmp_path):
    from openpyxl import load_workbook

    audit = original(tmp_path)
    book = load_workbook(audit.file_url)
    book.active["G22"] = "按特殊外形曲线规定"
    book.save(audit.file_url)
    book.close()
    assert checked(audit)["C-03"]["status"] == "unknown"


def amend(audit, *, fields=None, row_values=None, history=None):
    from openpyxl import load_workbook

    book = load_workbook(audit.file_url)
    sheet = book.active
    for row in sheet.iter_rows():
        if fields and row[0].value in fields:
            sheet.cell(row[0].row, 2, fields[row[0].value])
    header = next(row[0].row for row in sheet.iter_rows() if row[0].value == "工序号")
    columns = {cell.value: cell.column for cell in sheet[header]}
    for name, value in (row_values or {}).items():
        for row in range(header + 1, sheet.max_row + 1):
            sheet.cell(row, columns[name], value)
    if history is not None:
        changes = book.create_sheet("变更履历")
        for row in history:
            changes.append(row)
    book.save(audit.file_url)
    book.close()
    return audit


@pytest.mark.parametrize(("specification", "expected"), [("-2 N", "defect"), ("-2 ℃", "satisfied"), ("101 %", "defect"), ("10 mm 2 N", "satisfied")])
def test_physical_domain_keeps_negative_temperature_legal(tmp_path, specification, expected):
    observed = checked(original(tmp_path, specification=specification))
    assert observed["C-05"]["status"] == expected


def test_unit_warning_and_two_distinct_dimensions_are_separate(tmp_path):
    observed = checked(original(tmp_path, specification="10 mm 2 N"))
    assert observed["C-04"]["status"] == "satisfied"
    assert observed["C-06"]["status"] == "defect" and observed["C-06"]["code"] == "W03"
    observed = checked(original(tmp_path, specification="10 mm 2 cm"))
    assert observed["C-06"]["status"] == "satisfied"


def test_displayed_excel_error_is_warning_but_uncached_formula_is_unknown(tmp_path):
    observed = checked(amend(original(tmp_path), row_values={"规格/公差": "#REF!"}))
    assert observed["C-07"]["status"] == "defect" and observed["C-07"]["code"] == "W03"
    observed = checked(original(tmp_path, formula=True))
    assert observed["C-07"]["status"] == "unknown" and observed["C-07"]["code"] is None


def test_stage_selection_and_filename_conflict_are_read_as_facts(tmp_path):
    audit = amend(original(tmp_path), fields={"控制计划类型": "☑样件 □试生产 □生产"})
    audit.file_metadata["original_filename"] = "量产控制计划.xlsx"
    observed = checked(audit)
    assert observed["C-11"]["status"] == "satisfied"
    assert observed["C-12"]["status"] == "defect" and observed["C-12"]["code"] == "B06"
    observed = checked(amend(audit, fields={"控制计划类型": "☑样件 □试生产 ☑生产"}))
    assert observed["C-11"]["status"] == "defect"


def test_cp_history_checks_and_no_history_scope(tmp_path):
    audit = original(tmp_path)
    assert checked(audit)["C-14"]["code"] == "S02"
    amend(audit, fields={"版本": "B", "修订日期": "2025-02-01"}, history=[["版本", "修订日期", "变更说明", "工序号"], ["A", "2025-01-01", "发布", 10], ["B", "2025-02-01", "修订", 20]])
    observed = checked(audit)
    assert observed["C-14"]["status"] == observed["C-15"]["status"] == observed["C-16"]["status"] == "satisfied"
    observed = checked(amend(audit, fields={"版本": "C"}))
    assert observed["C-14"]["status"] == "defect" and observed["C-14"]["code"] == "W06"


def test_destructive_and_attribute_control_chart_contradictions(tmp_path):
    audit = amend(original(tmp_path, capacity="100%"), row_values={"产品特性": "盐雾试验"})
    assert checked(audit)["C-21"]["code"] == "B06"
    audit = amend(original(tmp_path), row_values={"产品特性": "外观目视", "控制方法": "均值极差图"})
    assert checked(audit)["C-22"]["code"] == "B06"
    audit = amend(original(tmp_path, capacity="1件"), row_values={"控制方法": "子组极差"})
    assert checked(audit)["C-23"]["code"] == "B06"


def test_uaes_controlled_date_label_is_not_a_controlled_stamp(tmp_path):
    audit = original(tmp_path, uaes=True)
    from openpyxl import load_workbook

    book = load_workbook(audit.file_url)
    book.active.insert_rows(8)
    book.active.cell(8, 1, "控制计划类型")
    book.active.cell(8, 2, "量产")
    book.save(audit.file_url)
    book.close()
    assert checked(audit, "U08")["M-14"]["status"] == "unknown"


def test_multi_worksheet_metadata_is_checked_without_choosing_a_fake_single_table(tmp_path):
    from openpyxl import load_workbook

    audit = amend(original(tmp_path), fields={"控制计划类型": "生产", "零件号": "P-1", "版本": "B"})
    book = load_workbook(audit.file_url)
    copied = book.copy_worksheet(book.active)
    copied.title = "控制计划续页"
    for row in copied.iter_rows():
        if row[0].value == "零件号":
            copied.cell(row[0].row, 2, "P-2")
        if row[0].value == "控制计划类型":
            copied.cell(row[0].row, 2, "试生产")
    book.save(audit.file_url)
    book.close()
    observed = checked(audit)
    assert observed["X-01"]["code"] == observed["X-02"]["code"] == observed["X-03"]["code"] == "B06"
    assert observed["C-01"]["status"] == "unknown"


def test_inline_reference_and_matching_attachment_are_executed(tmp_path):
    from openpyxl import load_workbook

    audit = amend(original(tmp_path), row_values={"反应计划": "见附表A"})
    assert checked(audit)["M-09"]["status"] == "unknown"
    book = load_workbook(audit.file_url)
    attachment = book.create_sheet("附表A")
    attachment.append(["反应计划定义"])
    attachment.append(["发现不良后隔离并报告质量工程师"])
    book.save(audit.file_url)
    book.close()
    observed = checked(audit)
    assert observed["M-09"]["status"] == observed["M-11"]["status"] == "satisfied"


def test_same_part_versions_need_baseline_and_different_parts_are_not_duplicates(tmp_path):
    from audit.contracts import GroupContextInput
    from audit.kinds import PpapDocumentType
    from audit.precheck_cp import cp_precheck

    audit = amend(original(tmp_path), fields={"零件号": "P-1"})
    other_path = tmp_path / "other"
    other_path.mkdir()
    other = amend(original(other_path), fields={"零件号": "P-1"})
    other.audit_id = "other-version"
    audit.file_metadata["role"] = "target"
    other.file_metadata["role"] = "target"
    group = GroupContextInput(task_group_id="frozen-task", by_audit_id={audit.audit_id: audit, other.audit_id: other}, by_doc_type={PpapDocumentType.CONTROL_PLAN.get_type_name(): [audit, other]})
    observed = {row["id"].split(":", 1)[1]: row for row in cp_precheck(audit, "N03", group)}
    assert observed["P-01"]["status"] == "satisfied"
    assert observed["P-07"]["status"] == "defect" and observed["P-07"]["code"] == "W04"
    assert observed["M-08"]["status"] == "unknown"
    amend(other, fields={"零件号": "P-2"})
    observed = {row["id"].split(":", 1)[1]: row for row in cp_precheck(audit, "N03", group)}
    assert observed["P-07"]["status"] == "not_checked" and observed["P-07"]["code"] == "S01"


def test_date_chain_without_approval_field_uses_only_existing_dates(tmp_path):
    from openpyxl import load_workbook

    audit = amend(original(tmp_path), fields={"编制日期": "2025-01-01", "修订日期": "2025-02-01"})
    book = load_workbook(audit.file_url)
    for row in book.active.iter_rows():
        if row[0].value == "批准日期":
            row[0].value = "本模板不含批准日期栏"
    book.save(audit.file_url)
    book.close()
    assert checked(audit)["C-13"]["status"] == "satisfied"
    observed = checked(amend(audit, fields={"编制日期": "2025-03-01"}))
    assert observed["C-13"]["status"] == "defect" and observed["C-13"]["code"] == "W06"


def test_exact_source_characteristic_roots_are_checked_without_guessing_synonyms(tmp_path):
    audit = amend(original(tmp_path), row_values={"产品特性": "长度", "过程特性": "温度"})
    assert checked(audit)["C-08"]["status"] == "satisfied"
    audit = amend(audit, row_values={"产品特性": "温度", "过程特性": "长度"})
    assert checked(audit)["C-08"]["code"] == "B05"
    audit = amend(audit, row_values={"产品特性": "产品工作温度", "过程特性": "其他"})
    assert checked(audit)["C-08"]["status"] == "unknown"


def test_physical_sequence_example_is_checked_and_engineering_limits_are_handoff(tmp_path):
    from openpyxl import load_workbook

    audit = original(tmp_path)
    book = load_workbook(audit.file_url)
    header = next(row[0].row for row in book.active.iter_rows() if row[0].value == "工序号")
    book.active.cell(header + 1, 2, "总装")
    book.active.cell(header + 2, 2, "下料")
    book.save(audit.file_url)
    book.close()
    observed = checked(audit)
    assert observed["C-19"]["code"] == "B06"
    assert observed["C-20"]["status"] == "handoff" and observed["C-20"]["code"] == "S03"


def test_declared_gauge_resolution_compares_actual_same_unit_band(tmp_path):
    audit = original(tmp_path, specification="10±0.01mm", measurement="卡尺 分辨率0.02mm")
    assert checked(audit)["C-24"]["status"] == "defect" and checked(audit)["C-24"]["code"] == "B06"
    audit = original(tmp_path, specification="10±0.01mm", measurement="卡尺 分辨率2μm")
    observed = checked(audit)
    assert observed["C-24"]["status"] == "handoff" and observed["C-24"]["code"] == "S03"


def test_exact_physical_dimension_counterexample_and_unprovided_matrix_are_separate(tmp_path):
    audit = amend(original(tmp_path, measurement="卡尺"), row_values={"产品特性": "", "过程特性": "扭矩"})
    assert checked(audit)["C-25"]["code"] == "B05"
    audit = amend(audit, row_values={"评价测量技术": "扭矩扳手"})
    observed = checked(audit)
    assert observed["C-25"]["status"] == "handoff" and observed["C-25"]["code"] == "S03"


def test_unknown_capacity_text_is_not_passed_by_nonempty_check(tmp_path):
    observed = checked(original(tmp_path, capacity="按需"))
    assert observed["C-01"]["status"] == "unknown"


def test_unknown_row_identity_is_not_silently_dropped_from_scope(tmp_path):
    from openpyxl import load_workbook

    audit = original(tmp_path)
    book = load_workbook(audit.file_url)
    header = next(row[0].row for row in book.active.iter_rows() if row[0].value == "工序号")
    book.active.cell(header + 2, 2, '=IF(1,"工序B","")')
    book.save(audit.file_url)
    book.close()
    assert checked(audit)["C-01"]["status"] == "unknown"


def test_existing_target_reference_roles_select_baseline_without_new_state(tmp_path):
    from audit.contracts import GroupContextInput
    from audit.kinds import PpapDocumentType
    from audit.precheck_cp import cp_precheck

    audit = amend(original(tmp_path), fields={"零件号": "P-1"})
    other_path = tmp_path / "baseline-ref"
    other_path.mkdir()
    other = amend(original(other_path), fields={"零件号": "P-1"})
    other.audit_id = "reference-version"
    audit.file_metadata["role"] = "target"
    other.file_metadata["role"] = "reference"
    group = GroupContextInput(task_group_id="frozen-task", by_audit_id={audit.audit_id: audit, other.audit_id: other}, by_doc_type={PpapDocumentType.CONTROL_PLAN.get_type_name(): [audit, other]})
    observed = {row["id"].split(":", 1)[1]: row for row in cp_precheck(audit, "N03", group)}
    assert observed["P-07"]["status"] == observed["M-08"]["status"] == observed["X-05"]["status"] == "satisfied"
