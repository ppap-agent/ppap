"""Native-original counterexamples for the remaining Li Auto L1 predicates."""

from datetime import datetime
from types import SimpleNamespace

import pytest
from audit.checkers.v4.li_auto.precheck_l1 import L1Facts
from audit.checkers.v4.li_auto.precheck_materials_msa_capacity import operations
from audit.contracts import AuditInput, GroupContextInput
from audit.kinds import PpapDocumentType
from openpyxl import Workbook


def execute(tmp_path, entry, criterion, sheets, *, complete_group=False, metadata=None, other=None, other_kind=None, **kwargs):
    book = Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    path = tmp_path / "原件.xlsx"
    book.save(path)
    book.close()
    audit = AuditInput(audit_id="original-version", file_url=str(path), file_metadata=metadata or {"original_filename": "原件.xlsx", "role": "target"}, **kwargs)
    group = None
    if complete_group:
        kind = {"L04": PpapDocumentType.SUB_SUPPLIER_LIST, "L05": PpapDocumentType.MSA_REPORT, "L06": PpapDocumentType.INITIAL_PROCESS_CAPACITY_STUDY}[entry]
        audits = [audit]
        if other:
            second = Workbook()
            second.remove(second.active)
            for title, rows in other.items():
                sheet = second.create_sheet(title)
                for row in rows:
                    sheet.append(row)
            second_path = tmp_path / "关联原件.xlsx"
            second.save(second_path)
            second.close()
            audits.append(AuditInput(audit_id="peer-version", file_url=str(second_path), file_metadata={"original_filename": "关联原件.xlsx", "role": "reference"}))
        by_type = {kind.get_type_name(): audits}
        if other_kind is not None:
            by_type = {kind.get_type_name(): [audit], other_kind.get_type_name(): audits[1:]}
        group = GroupContextInput(task_group_id="frozen-task", by_audit_id={item.audit_id: item for item in audits}, by_doc_type=by_type)
    agent = SimpleNamespace(group_context=group)
    facts = L1Facts(audit)
    return facts.observe(entry + ":" + criterion, operations(agent, entry, facts)[criterion])


def expect(fact, status, code=None):
    assert fact["status"] == status, fact
    assert fact["code"] == code, fact
    assert not fact["certified"]
    if status == "defect":
        assert fact["evidence"]["sources"]
        assert any("original-version" in source["locator"] for source in fact["evidence"]["sources"])


_MATERIAL_HEADER = [
    "零件号",
    "零件名称",
    "层级",
    "序号",
    "单件重量",
    "材料执行标准",
    "材料类型",
    "材料牌号",
    "材料规格/尺寸",
    "材料供应商名称",
    "通过何种质量管理体系认证",
    "自制",
    "外协-国产",
    "外协-进口",
    "领用件",
    "完成PPAP",
    "部件供应商名称",
]


def material(part="P001", name="支架", level=1):
    return [part, name, level, 1, 1, "GB/T 699", "金属", "45", "10mm", "材料厂", "IATF16949", "Y", None, None, None, "Y", "部件厂"]


def test_empty_material_body_is_not_a_pass(tmp_path):
    expect(execute(tmp_path, "L04", "PC-A7", {"材料清单": [_MATERIAL_HEADER]}), "defect", "B02")


@pytest.mark.parametrize(("level", "code"), [(3, "B04"), ("=1+1", None)])
def test_material_hierarchy_is_not_inferred_from_row_position(tmp_path, level, code):
    expect(execute(tmp_path, "L04", "PC-B3", {"材料清单": [_MATERIAL_HEADER, material(), material("P002", level=level)]}), "defect" if code else "unknown", code)


def test_self_made_assembly_needs_a_child(tmp_path):
    expect(execute(tmp_path, "L04", "PC-B5", {"材料清单": [_MATERIAL_HEADER, material(name="自制支架总成")]}), "defect", "B04")


def test_scope_requires_independent_task_declaration(tmp_path):
    rows = [["零件号", "P001"], _MATERIAL_HEADER, material()]
    expect(execute(tmp_path, "L04", "PC-A3", {"材料清单": rows}), "unknown")
    expect(execute(tmp_path, "L04", "PC-A3", {"材料清单": rows}, part="OTHER"), "defect", "B03")


def test_material_header_root_conflict(tmp_path):
    rows = [["零件号", "P002"], _MATERIAL_HEADER, material()]
    expect(execute(tmp_path, "L04", "PC-B2", {"材料清单": rows}), "defect", "B06")


def test_material_missing_required_own_generation_column(tmp_path):
    expect(execute(tmp_path, "L04", "PC-B8", {"材料清单": [_MATERIAL_HEADER[:-1], material()[:-1]]}), "satisfied")
    expect(execute(tmp_path, "L04", "PC-B8", {"材料清单": [_MATERIAL_HEADER[:10], material()[:10]]}), "defect", "B05")


def test_material_continuation_is_not_a_missing_part(tmp_path):
    split = [None] * len(_MATERIAL_HEADER)
    split[7] = "其他材料"
    expect(execute(tmp_path, "L04", "PC-D2", {"材料清单": [_MATERIAL_HEADER, material(), split]}), "satisfied")
    split[1] = "另一个零件"
    expect(execute(tmp_path, "L04", "PC-D1", {"材料清单": [_MATERIAL_HEADER, material(), split]}), "defect", "B03")


def test_leaf_weight_missing_and_unknown_formula_are_distinct(tmp_path):
    row = material()
    row[4] = None
    expect(execute(tmp_path, "L04", "PC-D4", {"材料清单": [_MATERIAL_HEADER, row]}), "defect", "W02")
    row[4] = "=1+1"
    expect(execute(tmp_path, "L04", "PC-D4", {"材料清单": [_MATERIAL_HEADER, row]}), "unknown")


@pytest.mark.parametrize(("supplier", "status"), [("上海", "defect"), ("博世", "unknown")])
def test_short_company_name_is_not_automatically_a_place_name(tmp_path, supplier, status):
    row = material()
    row[9] = supplier
    expect(execute(tmp_path, "L04", "PC-C8", {"材料清单": [_MATERIAL_HEADER, row]}), status, "W01" if status == "defect" else None)


def test_same_part_conflicts_across_material_slices(tmp_path):
    row = material()
    row[7] = "铝"
    expect(execute(tmp_path, "L04", "PC-E1", {"总成A": [_MATERIAL_HEADER, material()], "总成B": [_MATERIAL_HEADER, row]}), "defect", "W04")


def test_material_unit_suspicion_only_compares_same_level_and_type(tmp_path):
    row = material("P002")
    row[4] = 1000
    expect(execute(tmp_path, "L04", "PC-C4", {"材料清单": [_MATERIAL_HEADER, material(), row]}), "defect", "W06")
    row[2] = 2
    expect(execute(tmp_path, "L04", "PC-C4", {"材料清单": [_MATERIAL_HEADER, material(), row]}), "unknown")


def test_blank_msa_plan_and_numeric_corruption(tmp_path):
    expect(execute(tmp_path, "L05", "MSA-PRE-02", {"测量系统分析计划": [["量具编号", "量具名称"]]}), "defect", "B05")
    expect(execute(tmp_path, "L05", "MSA-PRE-04", {"R&R数据表": [["读数"], ["0。5"], [0.6]]}), "defect", "W03")
    expect(execute(tmp_path, "L05", "MSA-PRE-04", {"R&R数据表": [["读数"], ["O.5"]]}), "defect", "B05")
    expect(execute(tmp_path, "L05", "MSA-PRE-04", {"R&R数据表": [["读数"], ["=1+1"]]}), "unknown")


def test_msa_no_scope_is_warning_not_invented_full_submission(tmp_path):
    expect(execute(tmp_path, "L05", "MSA-PRE-13", {"测量系统分析计划": [["量具编号", "量具名称"], ["G1", "卡尺"]]}), "defect", "W01")


def test_declared_report_absence_needs_complete_frozen_collection(tmp_path):
    sheets = {"测量系统分析计划": [["提交范围", "计划和报告"], ["量具编号", "量具名称"], ["G1", "卡尺"]]}
    expect(execute(tmp_path, "L05", "MSA-PRE-14", sheets), "unknown")
    expect(execute(tmp_path, "L05", "MSA-PRE-14", sheets, complete_group=True), "defect", "B02")


def test_msa_metrics_are_valid_conclusion_expression_without_formal_threshold(tmp_path):
    expect(execute(tmp_path, "L05", "MSA-PRE-16", {"R&R分析表": [["%GRR", 42], ["ndc", 2]]}), "satisfied")
    expect(execute(tmp_path, "L05", "MSA-PRE-16", {"R&R分析表": [["量具编号", "G1"]]}), "defect", "B05")


def test_capability_required_field_missing_is_structural(tmp_path):
    expect(execute(tmp_path, "L06", "PRE-06", {"CCSC特性管理清单": [["特性名称", "P阶段Cpk结果"], ["孔径", 1.1]]}), "defect", "B05")


@pytest.mark.parametrize(("size", "count", "expected", "code"), [(1, 29, "defect", "W06"), (5, 19, "defect", "W06"), (5, 20, "unknown", None)])
def test_capability_sample_gate_uses_declared_counts(tmp_path, size, count, expected, code):
    expect(execute(tmp_path, "L06", "PRE-13", {"能力报告": [["子组容量", size], ["子组数", count]]}), expected, code)


def test_capability_constants_only_use_source_exact_supported_rows(tmp_path):
    expect(execute(tmp_path, "L06", "PRE-13", {"能力报告": [["子组容量", 5], ["子组数", 25], ["d2", 3.326]]}), "defect", "B06")
    expect(execute(tmp_path, "L06", "PRE-13", {"能力报告": [["子组容量", 7], ["子组数", 25], ["d2", 3.326]]}), "unknown")


def test_random_measurement_formula_has_original_syntax_even_without_cache(tmp_path):
    fact = execute(tmp_path, "L06", "PRE-14", {"能力报告": [["Cpk", "=RAND()"]]})
    expect(fact, "defect", "B05")
    assert any("RAND" in source["excerpt"] for source in fact["evidence"]["sources"])


def test_external_formula_missing_cache_is_not_proof_of_missing_target(tmp_path):
    expect(execute(tmp_path, "L06", "PRE-14", {"能力报告": [["Cpk", "='[data.xlsx]S'!A1"]]}), "unknown")


@pytest.mark.parametrize(
    ("times", "status", "code"),
    [
        (["2026-01-02 10:00", "2026-01-01 10:00"], "defect", "B06"),
        (["2026-01-01 10:00", "2026-01-01 10:00"], "defect", "W06"),
        (["2026-01-01", "2026-01-01"], "unknown", None),
        (["=1+1", "2026-01-01"], "unknown", None),
    ],
)
def test_capability_time_logic_does_not_blame_same_date_only(tmp_path, times, status, code):
    expect(execute(tmp_path, "L06", "PRE-15", {"能力报告": [["组号", "抽样时间"], [1, times[0]], [2, times[1]]]}), status, code)


def test_native_datetime_time_order_is_preserved(tmp_path):
    expect(execute(tmp_path, "L06", "PRE-15", {"能力报告": [["组号", "抽样时间"], [1, datetime(2026, 1, 1, 11)], [2, datetime(2026, 1, 1, 10)]]}), "defect", "B06")


def test_capability_cross_table_values_require_same_part_and_characteristic(tmp_path):
    sheets = {"汇总": [["零件号", "P001"], ["特性名称", "Cpk"], ["孔径", 1.3]], "明细": [["零件号", "P001"], ["特性名称", "Cpk"], ["孔径", 1.1]]}
    expect(execute(tmp_path, "L06", "PRE-16", sheets), "defect", "B06")
    sheets["明细"][0][1] = "P002"
    expect(execute(tmp_path, "L06", "PRE-16", sheets), "unknown")


def test_same_kind_different_parts_are_not_conflicting_versions(tmp_path):
    first = {"材料清单": [["零件号", "P001"], _MATERIAL_HEADER, material()]}
    second = {"材料清单": [["零件号", "P002"], _MATERIAL_HEADER, material("P002")]}
    expect(execute(tmp_path, "L04", "PC-A4", first, complete_group=True, other=second), "not_checked", "S01")


def test_same_part_reference_role_proves_effective_target_without_guessing_version(tmp_path):
    first = {"材料清单": [["零件号", "P001"], _MATERIAL_HEADER, material()]}
    expect(execute(tmp_path, "L04", "PC-A4", first, complete_group=True, other=first), "satisfied")


def test_msa_cross_file_closure_reads_peer_original(tmp_path):
    plan = {"测量系统分析计划": [["提交范围", "计划和报告"], ["量具编号", "量具名称", "零件号", "分析项目"], ["G1", "卡尺", "P001", "计量型"]]}
    report = {"R&R分析表": [["量具编号", "G1"], ["量具名称", "卡尺"], ["零件号", "P001"], ["分析项目", "计量型"], ["结论", "可接受"]]}
    fact = execute(tmp_path, "L05", "MSA-PRE-05", plan, complete_group=True, other=report)
    expect(fact, "satisfied")
    assert any("peer-version" in source["locator"] for source in fact["evidence"]["sources"])
    expect(execute(tmp_path, "L05", "MSA-PRE-06", plan, complete_group=True, other=report), "satisfied")
    report["R&R分析表"][2][1] = "P002"
    expect(execute(tmp_path, "L05", "MSA-PRE-06", plan, complete_group=True, other=report), "defect", "W04")


def test_msa_missing_report_is_not_blocked_without_declared_both_scope(tmp_path):
    plan = {"测量系统分析计划": [["量具编号", "量具名称"], ["G1", "卡尺"], ["G2", "千分尺"]]}
    report = {"R&R分析表": [["量具编号", "G1"], ["结论", "可接受"]]}
    expect(execute(tmp_path, "L05", "MSA-PRE-05", plan, complete_group=True, other=report), "not_checked", "S04-S")
    plan["测量系统分析计划"].insert(0, ["提交范围", "计划和报告"])
    expect(execute(tmp_path, "L05", "MSA-PRE-05", plan, complete_group=True, other=report), "defect", "B02")


def test_repeated_material_header_is_unresolved_slice_not_bad_data(tmp_path):
    sheets = {"材料清单": [_MATERIAL_HEADER, material(), _MATERIAL_HEADER, material("P002")]}
    expect(execute(tmp_path, "L04", "PC-D1", sheets), "unknown")


@pytest.mark.parametrize("entry", ["L04", "L05", "L06"])
def test_all_remaining_operations_have_real_source_or_specific_unknown_not_runtime_error(tmp_path, entry):
    book = Workbook()
    sheet = book.active
    sheet.title = {"L04": "材料清单", "L05": "测量系统分析计划", "L06": "CCSC特性管理清单"}[entry]
    rows = {"L04": [_MATERIAL_HEADER, material()], "L05": [["量具编号", "量具名称"], ["G1", "卡尺"]], "L06": [["特性名称", "P阶段Cpk结果", "P阶段结果判定"], ["孔径", 1.1, "可接受"]]}[entry]
    for row in rows:
        sheet.append(row)
    path = tmp_path / "已填原件.xlsx"
    book.save(path)
    book.close()
    audit = AuditInput(audit_id="source", file_url=str(path))
    facts = L1Facts(audit)
    mapping = operations(SimpleNamespace(group_context=None), entry, facts)
    for criterion, operation in mapping.items():
        fact = facts.observe(entry + ":" + criterion, operation)
        assert fact["status"] != "error", (criterion, fact)
        assert "尚未自动执行" not in fact["message"]
        assert "需要补充实现" not in fact["message"]
        if fact["status"] == "satisfied":
            assert fact["evidence"]["sources"], (criterion, fact)


def test_control_chart_scope_is_derived_from_actual_subgroup_not_all_three(tmp_path):
    sheets = {"CCSC特性管理清单": [["子组容量", 1]], "I-MR": [["Cpk", 1.1]]}
    expect(execute(tmp_path, "L06", "PRE-08", sheets), "unknown")
    del sheets["I-MR"]
    expect(execute(tmp_path, "L06", "PRE-08", sheets), "defect", "B05")
    sheets = {"CCSC特性管理清单": [["特性名称", "P阶段Cpk结果"], ["孔径", 1.1]]}
    expect(execute(tmp_path, "L06", "PRE-08", sheets), "unknown")


@pytest.mark.parametrize(("value", "status"), [(1.1, "satisfied"), ("1.1", "defect"), ("NA", "satisfied"), ("=1+1", "unknown")])
def test_capability_type_preserves_actual_native_cell_type(tmp_path, value, status):
    expect(execute(tmp_path, "L06", "PRE-10", {"CCSC特性管理清单": [["特性名称", "P阶段Cpk结果", "P阶段结果判定"], ["孔径", value, "合格"]]}), status, "B05" if status == "defect" else None)


def test_capability_row_empty_is_source_b04_not_source_type_failure(tmp_path):
    sheets = {"CCSC特性管理清单": [["特性名称", "P阶段Cpk结果", "P阶段结果判定"], ["孔径", None, "合格"]]}
    expect(execute(tmp_path, "L06", "PRE-07", sheets), "defect", "B04")


def test_duplicate_material_certification_columns_are_checked_independently(tmp_path):
    headers = _MATERIAL_HEADER + ["通过何种质量管理体系认证"]
    expect(execute(tmp_path, "L04", "PC-D3", {"材料清单": [headers, material() + [None]]}), "defect", "W01")


def test_rand_text_literal_is_not_random_number_generation(tmp_path):
    expect(execute(tmp_path, "L06", "PRE-14", {"能力报告": [["Cpk", '=IF(A1="RAND()",1,2)']]}), "unknown")


def test_capability_blank_sign_date_is_not_missing_label(tmp_path):
    sheets = {"能力报告": [["签署日期", None], ["组号", "抽样时间"], [1, "2026-01-01 10:00"], [2, "2026-01-01 11:00"]]}
    expect(execute(tmp_path, "L06", "PRE-15", sheets), "defect", "W02")


def test_valid_na_text_is_not_counted_as_a_blank_material_column(tmp_path):
    row = material()
    row[10] = "NA"
    expect(execute(tmp_path, "L04", "PC-D3", {"材料清单": [_MATERIAL_HEADER, row]}), "satisfied")


def test_placeholder_part_does_not_become_legal_material_continuation(tmp_path):
    split = [None] * len(_MATERIAL_HEADER)
    split[0], split[7] = "NA", "其他材料"
    expect(execute(tmp_path, "L04", "PC-D2", {"材料清单": [_MATERIAL_HEADER, material(), split]}), "defect", "B03")


def test_cross_file_old_version_cannot_close_current_msa_plan(tmp_path):
    plan = {"测量系统分析计划": [["版本", "V2"], ["量具编号", "量具名称", "零件号", "分析项目"], ["G1", "卡尺", "P001", "计量型"]]}
    report = {"R&R分析表": [["版本", "V1"], ["量具编号", "G1"], ["零件号", "P001"], ["结论", "可接受"]]}
    expect(execute(tmp_path, "L05", "MSA-PRE-05", plan, complete_group=True, other=report), "unknown")


def test_material_same_version_pair_compares_actual_weights(tmp_path):
    first = {"材料清单": [["零件号", "P001"], ["版本", "V1"], _MATERIAL_HEADER, material()]}
    second = {"材料清单": [["零件号", "P001"], ["版本", "V1"], _MATERIAL_HEADER, material()]}
    expect(execute(tmp_path, "L04", "PC-E3", first, complete_group=True, other=second), "satisfied")
    second["材料清单"][-1][4] = 2
    expect(execute(tmp_path, "L04", "PC-E3", first, complete_group=True, other=second), "defect", "W04")
    second["材料清单"][1][1] = "V0"
    expect(execute(tmp_path, "L04", "PC-E3", first, complete_group=True, other=second), "not_checked", "S02")


def test_material_declared_attachment_is_checked_only_with_frozen_group(tmp_path):
    first = {"材料清单": [["附件：PSW"], _MATERIAL_HEADER, material()]}
    expect(execute(tmp_path, "L04", "PC-A8", first), "unknown")
    expect(execute(tmp_path, "L04", "PC-A8", first, complete_group=True), "defect", "W01")


def test_material_no_attachment_declaration_does_not_invent_drawing_dependency(tmp_path):
    expect(execute(tmp_path, "L04", "PC-A8", {"材料清单": [_MATERIAL_HEADER, material()]}), "not_checked", "S02")


def test_msa_multiform_metadata_is_compared_across_frozen_originals(tmp_path):
    first = {"R&R数据表": [["量具编号", "G1"], ["量具名称", "卡尺"], ["零件号", "P1"], ["规格公差", "10±0.2"], ["评价人", "张工"]]}
    other = {"R&R分析表": [["量具编号", "G1"], ["量具名称", "卡尺"], ["零件号", "P1"], ["规格公差", "10±0.2"], ["评价人", "李工"]]}
    fact = execute(tmp_path, "L05", "MSA-PRE-07", first, complete_group=True, other=other)
    expect(fact, "defect", "W04")
    assert any("peer-version" in source["locator"] for source in fact["evidence"]["sources"])
    other["R&R分析表"][-1][1] = "张工"
    expect(execute(tmp_path, "L05", "MSA-PRE-07", first, complete_group=True, other=other), "satisfied")


def test_capability_summary_detail_set_difference_uses_stable_ids(tmp_path):
    first = [["零件号", "P1"], ["特性编号", "特性名称", "Cpk"], ["C1", "孔径", 1.1]]
    second = [["零件号", "P1"], ["特性编号", "特性名称", "Cpk"], ["C2", "孔径", 1.1]]
    expect(execute(tmp_path, "L06", "PRE-16", {"汇总清单": first, "明细页": second}), "defect", "B06")
    first[1][0] = "其他列"
    second[1][0] = "其他列"
    expect(execute(tmp_path, "L06", "PRE-16", {"汇总清单": first, "明细页": second}), "unknown")


@pytest.mark.parametrize(("spec", "status", "code"), [("10±0.2mm", "satisfied", None), ("1±0.02cm", "satisfied", None), ("10±0.3mm", "defect", "B06"), ("10±0.2", "unknown", None)])
def test_cross_file_control_plan_specs_use_frozen_part_id_and_explicit_units(tmp_path, spec, status, code):
    capability = {"能力汇总清单": [["零件号", "P1"], ["单位", "mm"], ["特性编号", "特性名称", "LSL", "USL", "Cpk"], ["C1", "孔径", 9.8, 10.2, 1.1]]}
    cp = {"控制计划": [["零件号", "P1"], ["特性编号", "特性名称", "规格/公差"], ["C1", "孔径", spec]]}
    fact = execute(tmp_path, "L06", "PRE-16", capability, complete_group=True, other=cp, other_kind=PpapDocumentType.CONTROL_PLAN)
    expect(fact, status, code)
    if status == "defect":
        assert any("peer-version" in source["locator"] for source in fact["evidence"]["sources"])


def test_control_plan_characteristic_set_difference_is_warning_not_missing_engineering_pass(tmp_path):
    capability = {"能力汇总清单": [["零件号", "P1"], ["特性编号", "特性名称", "Cpk"], ["C1", "孔径", 1.1]]}
    cp = {"控制计划": [["零件号", "P1"], ["特性编号", "特性名称"], ["C2", "孔径"]]}
    expect(execute(tmp_path, "L06", "PRE-16", capability, complete_group=True, other=cp, other_kind=PpapDocumentType.CONTROL_PLAN), "defect", "W04")


def test_material_generation_uses_original_customer_and_template_declaration(tmp_path):
    sheets = {"材料清单": [["客户", "理想"], ["模板版本", "V3.0"], _MATERIAL_HEADER, material()]}
    expect(execute(tmp_path, "L04", "PC-A5", sheets), "satisfied")
    sheets["材料清单"][1][1] = "V9.0"
    expect(execute(tmp_path, "L04", "PC-A5", sheets), "unknown")
    sheets["材料清单"][1][1] = "非标准"
    expect(execute(tmp_path, "L04", "PC-A5", sheets), "defect", "B03")


def test_no_self_list_only_closes_reconciliation_subitem_not_entire_capability_checks(tmp_path):
    sheets = {"CCSC特性管理清单": [["特性名称", "P阶段Cpk结果", "P阶段结果判定"], ["孔径", 1.1, "合格"]]}
    expect(execute(tmp_path, "L06", "PRE-03", sheets), "unknown")
    expect(execute(tmp_path, "L06", "PRE-03", sheets, complete_group=True), "satisfied")


def test_declared_msa_report_type_can_be_supplied_by_frozen_peer(tmp_path):
    plan = {"测量系统分析计划": [["提交范围", "计划和报告"], ["量具编号", "量具名称"], ["G1", "卡尺"]]}
    report = {"R&R报告": [["量具编号", "G1"], ["结论", "可接受"]]}
    expect(execute(tmp_path, "L05", "MSA-PRE-14", plan, complete_group=True, other=report), "satisfied")
