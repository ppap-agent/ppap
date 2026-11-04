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


def test_source_dvp_two_tier_timeline_and_blank_actuals(tmp_path):
    from pathlib import Path

    from openpyxl import load_workbook

    source = Path(__file__).resolve().parents[3] / "assets/PPAP PRD v3.7/客户归类模板与规则_备份/模板/理想/APQP-零部件设计验证计划.xlsx"
    workbook = load_workbook(source)
    sheet = workbook["DVP&R"]
    sheet["M10"] = "2024-05-01"
    sheet["N10"] = "2024-10-10"
    path = tmp_path / "dvp.xlsx"
    workbook.save(path)
    audit = AuditInput(audit_id="native", file_url=str(path), customer="理想")
    observed = result(audit, "L02", "CON-10")
    assert (observed["status"], observed["code"]) == ("defect", "W06"), observed
    sheet["M10"] = None
    sheet["N10"] = None
    workbook.save(path)
    assert result(audit, "L02", "CON-10")["status"] == "satisfied"


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
