"""Additional retained UAES forms at the real v5/Core boundary.

These templates are not supplier submissions or an independent all-PASS Oracle.
Native cells establish the stated structural result; unread signature/selection
facts remain unknown. The tests never purchase or invent visual observations.
"""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
import xlrd
from audit.audits import RoundInput, RoundWork
from audit.checker_lib.source import SourceAudit
from audit.standard_suites import predefined_directory_suites
from corpus_contracts.core_round import run_suite_round
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[4]
TEMPLATES = ROOT / "assets/PPAP PRD v3.7/客户归类模板与规则_备份/模板/UAES"


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    """No provider facts are available; native-only clauses must not ask for any."""
    requests = []

    def visual(_owner, fields, **_options):
        requests.append(set(fields))
        return dict.fromkeys(fields)

    def text(*_args, **_kwargs):
        pytest.fail("These native original checks must not call a text model")

    monkeypatch.setattr(SourceAudit, "visual_pdf", visual)
    monkeypatch.setattr(SourceAudit, "text_result", text)
    return requests


def frozen(name, sha256):
    path = TEMPLATES / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256
    return path


def run_clause(path, entry, clause, *, reference=None):
    source = next(item for item in predefined_directory_suites() if item.source_path == f"v5/UAES/{entry}")
    rules = json.loads(source.files["rules.json"])
    rules["rules"] = [item for item in rules["rules"] if item["id"] == clause]
    rules["checker_binding"] = {clause: source.entrypoints[clause]}
    selected = replace(source, entrypoints=rules["checker_binding"])
    before = {item: hashlib.sha256(item.read_bytes()).hexdigest() for item in [path, *([reference] if reference else [])]}
    inputs = [RoundInput("target", "target-file", "target-version", path.name, "ready", rule_version_id="frozen-" + entry, rule_body=rules)]
    blobs = {"target-version": path.read_bytes()}
    if reference:
        inputs.append(RoundInput("reference", "reference-file", "reference-version", reference.name, "ready"))
        blobs["reference-version"] = reference.read_bytes()
    result = run_suite_round(RoundWork("additional-uaes-originals", "tenant", inputs), blobs, suites={"target-version": selected})
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["locator"]["rule_item_id"] == clause
    assert finding["locator"]["file_version_id"] == "target-version"
    assert result["coverage"]["rule_items"][0]["coverage"] == "executed"
    assert all(hashlib.sha256(item.read_bytes()).hexdigest() == digest for item, digest in before.items())
    return finding


def locations(finding):
    return {(item["file_version_id"], item["locator"].get("sheet"), item["locator"].get("cell")) for item in finding["locator"].get("semantic", {}).get("sources", [])}


def inspection():
    return frozen("F-PU-207_样品检验报告_Sample Inspection Report_V01-.xls (1).xls", "17967eb50d77c7c6942c3052effc382fbcfec4c3338281881f33c55c060c5718")


def warrant():
    return frozen("新-F-PU-206_零件提交保证书_PartSubmissionWarrant_V03 (1).xls", "31ecfb6b5b86651cbbb2078529de71d7c59114133d57765bd03045010a31361c")


def test_original_inspection_ten_measurement_columns_violate_the_literal_multiple_of_three(no_provider):
    path = inspection()
    book = xlrd.open_workbook(path, formatting_info=True)
    sheet = book.sheet_by_name("尺寸报告dimension")
    assert sheet.cell_value(4, 5).startswith("Actual-Value Supplier/供应商实测值")
    assert (4, 5, 5, 15) in sheet.merged_cells  # F5:O5, not three sample columns.
    assert sheet.row_values(5, 5, 15) == list(range(1, 11))
    result = run_clause(path, "U01", "U01:1-1")
    assert result["status"] == "NG", result
    assert "10" in result["suggestions"] and "多产线/多模穴" in result["suggestions"]
    assert ("target-version", "尺寸报告dimension", "F5") in locations(result)
    assert no_provider == []


def test_original_inspection_empty_measurements_and_result_labels_are_not_a_business_pass(no_provider):
    path = inspection()
    sheet = xlrd.open_workbook(path).sheet_by_name("尺寸报告dimension")
    assert sheet.row_values(6, 1, 15) == [""] * 14
    assert sheet.cell_value(5, 15) == "OK"
    assert "NOT" in sheet.cell_value(5, 16)
    result = run_clause(path, "U01", "U01:2")
    assert result["status"] == "UNABLE", result
    assert ("target-version", "尺寸报告dimension", "P6") in locations(result)
    assert no_provider == []


def test_original_materials_choice_groups_are_unknown_instead_of_scalar_blank_defects(no_provider):
    path = frozen("7. 材料及分供方清单模板 (1).xlsx", "389ece6713a7c57f87a5dd607767f8d5ddc52bd24d8ec44fa1f000c843cc1aef")
    book = load_workbook(path, data_only=False)
    sheet = book["XXX项目 材料及分供方清单"]
    assert sheet["A9"].value == 1
    assert {"Q6:U6", "F6:J7"}.issubset({str(item) for item in sheet.merged_cells.ranges})
    book.close()
    result = run_clause(path, "U04", "U04:1")
    assert result["status"] == "UNABLE" and "合并表头" in result["suggestions"]
    assert ("target-version", "XXX项目 材料及分供方清单", "A6") in locations(result)
    assert no_provider == []


def test_original_measurement_guide_does_not_supply_real_four_party_signatures(no_provider):
    path = frozen("F-PU-166_测量比较报告_Measure Comparison Report_V02.xls (1).xls", "f2865e0957fe79d7b430399e55bcd1a57ed8f15dfac13f139e4bde43ee7706be")
    book = xlrd.open_workbook(path)
    assert "填写指南" in book.sheet_by_name("User_Instruction_Chinese").cell_value(1, 4)
    sheet = book.sheet_by_name("Measurement Comparison")
    assert "供应商" in sheet.cell_value(84, 3)
    assert "UAES技术中心" in sheet.cell_value(84, 6)
    assert "UAES测量部门" in sheet.cell_value(84, 13)
    assert "UAES供应商质量开发工程师" in sheet.cell_value(84, 19)
    result = run_clause(path, "U02", "U02:1")
    assert result["status"] == "UNABLE", result
    assert ("target-version", "Measurement Comparison", "G85") in locations(result)
    assert no_provider == [{"four_signatures"}]


def test_original_explained_warrant_blank_part_name_never_establishes_a_business_pass(no_provider):
    path = warrant()
    sheet = xlrd.open_workbook(path).sheet_by_name("PSW (with explanation)")
    assert "零件名称" in sheet.cell_value(3, 1)
    assert sheet.cell_value(3, 4) == ""
    result = run_clause(path, "U17", "U17:1")
    assert result["status"] == "UNABLE" and "零件名称" in result["suggestions"]
    assert ("target-version", "PSW (with explanation)", "E4") in locations(result)
    assert no_provider == []


@pytest.mark.parametrize(
    ("clause", "row", "label", "fields"),
    [
        ("U17:4", 35, "提交原因", {"reason_selected", "other_selected", "other_explained"}),
        ("U17:5", 42, "要求提交的等级", {"level_exactly_one"}),
    ],
)
def test_original_warrant_choice_labels_cannot_replace_unavailable_graphical_facts(no_provider, clause, row, label, fields):
    path = warrant()
    sheet = xlrd.open_workbook(path).sheet_by_name("PSW (with explanation)")
    assert label in sheet.cell_value(row - 1, 1)
    result = run_clause(path, "U17", clause)
    assert result["status"] == "UNABLE", result
    assert no_provider == [fields]


def test_original_traceability_numbered_empty_sections_do_not_prove_completed_traceability(no_provider):
    path = frozen("零件追溯检查单-模板 20250609.xlsx", "56e448154085d335ef94194f7794da60ef7f79f0cf3a8d8b67b8bac051e5cc8b")
    book = load_workbook(path, data_only=False)
    sheet = book["零件追溯清单"]
    for row, label in [(3, "基本信息"), (12, "分供方追溯"), (19, "生产过程追溯"), (27, "交付追溯"), (38, "追溯状态总结")]:
        assert label in sheet[f"B{row}"].value
        assert sheet[f"I{row}"].value is None
    assert sheet["B39"].value == "优点：\n弱点：\n后续计划："
    book.close()
    result = run_clause(path, "U18", "U18:1")
    assert result["status"] == "UNABLE" and "基本信息" in result["suggestions"]
    assert ("target-version", "零件追溯清单", "B3") in locations(result)
    assert no_provider == []


def test_original_sample_release_unrecognized_home_layout_is_a_reader_gap(no_provider):
    path = frozen("F01RC0YA8601 样品认可报告 (1).xls", "1d1e7efd5bb492226970a9dfbf5fc962fae2f987f1750807cd367040d4cc843a")
    book = xlrd.open_workbook(path)
    assert "样品认可报告" in book.sheet_by_name("首页").cell_value(0, 0)
    assert book.sheet_by_name("首页").cell_value(13, 2) == "F01RC0YA86"
    assert "偏差汇总清单" in book.sheet_names()
    result = run_clause(path, "U19", "U19:1")
    assert result["status"] == "UNABLE" and "业务表" in result["suggestions"]
    assert no_provider == []


def test_original_blank_pgqp_identity_cannot_be_paired_with_another_pfmea(no_provider):
    path = frozen("F-PU-127_采购件质量计划_Purchased Goods Quality Plan PGQP1_V06 (1).xlsx", "11eb2bf981cca18f85e091960570c510635a2f0987a847648ed04e114a38b2d2")
    book = load_workbook(path, data_only=False)
    sheet = book["PGQP1"]
    assert "Part No/零件号" in sheet["E2"].value
    assert str(next(item for item in sheet.merged_cells.ranges if item.start_cell.coordinate == "J2")) == "J2:L2"
    assert sheet["J2"].value is None
    book.close()
    pfmea = ROOT / "assets/corpus/documents/uaes/通用文件/表单模板/测试文件/09 潜在过程失效模式及后果分析/PFMEA.xls"
    result = run_clause(pfmea, "U07", "U07:4", reference=path)
    assert result["status"] == "UNABLE" and "身份" in result["suggestions"]
    assert any(version == "reference-version" and sheet == "PGQP1" for version, sheet, _cell in locations(result))
    assert no_provider == []
