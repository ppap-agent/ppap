"""Source-owned v5 rules against retained originals and explicitly derived variants.

Template blanks and printed guidance are not supplier conformity or human Oracle
verification. The expectations below follow the quoted clause and exact cells.
"""

import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest
from audit.audits import RoundInput, RoundWork
from audit.checker_lib.source import SourceAudit
from audit.standard_suites import predefined_directory_suites
from corpus_contracts.core_round import run_suite_round
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[4]
TEMPLATES = ROOT / "assets/PPAP PRD v3.7/客户归类模板与规则_备份/模板/理想"
MSA = TEMPLATES / "APQP-测量系统分析报告.xlsx"


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("These original-native checks must not call a model")

    monkeypatch.setattr(SourceAudit, "visual_pdf", forbidden)
    monkeypatch.setattr(SourceAudit, "text_result", forbidden)


def run_clause(entry, clause, content, filename):
    source = next(item for item in predefined_directory_suites() if item.source_path == f"v5/理想/{entry}")
    rules = json.loads(source.files["rules.json"])
    rules["rules"] = [item for item in rules["rules"] if item["id"] == clause]
    rules["checker_binding"] = {clause: source.entrypoints[clause]}
    selected = replace(source, entrypoints=rules["checker_binding"])
    target = RoundInput("target", "file", "frozen-version", filename, "ready", rule_version_id="v5-" + entry, rule_body=rules)
    result = run_suite_round(RoundWork("native-original-round", "tenant", [target]), {"frozen-version": content}, suites={"frozen-version": selected})
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["locator"]["rule_item_id"] == clause
    assert result["coverage"]["rule_items"][0]["coverage"] == "executed"
    return finding


def msa_variant(*, missing=None, formula=None, invalid_header=False, missing_evaluator=False, second_missing=False, keep_charts=False):
    """Copy the retained form and author a new, explicit measurement input."""
    book = load_workbook(io.BytesIO(MSA.read_bytes()))
    sheet = book["测量系统分析报告（R&R）仅供参考"]
    sheet.title = "量具重复性和再现性"
    if not keep_charts:
        # The derived native-only input has no template illustration overlay.
        sheet._charts.clear()
    for first in (8, 13, 18):
        for row in range(first, first + 3):
            for column in range(3, 13):
                sheet.cell(row, column, 0)
    if missing:
        sheet[missing] = None
    if formula:
        sheet[formula] = "=1+1"
    if invalid_header:
        sheet["L7"] = 9
    if missing_evaluator:
        sheet["A14"] = None
    if second_missing:
        other = book.copy_worksheet(sheet)
        other.title = "量具重复性和再现性-第二份"
        other["L20"] = None
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def test_original_msa_reference_and_filled_guide_are_not_supplier_measurements():
    before = hashlib.sha256(MSA.read_bytes()).hexdigest()
    result = run_clause("L05", "L05:3", MSA.read_bytes(), MSA.name)
    assert result["status"] == "UNABLE"
    assert "参考" in result["suggestions"] or "指南" in result["suggestions"]
    assert hashlib.sha256(MSA.read_bytes()).hexdigest() == before


def test_form_derived_msa_has_all_90_located_values_including_zero():
    result = run_clause("L05", "L05:3", msa_variant(), "正式MSA.xlsx")
    assert result["status"] == "PASS", result
    assert result["locator"]["semantic"]["sources"]


@pytest.mark.parametrize("cell", ["C8", "C13", "C18", "L20"])
def test_form_derived_msa_rejects_a_known_missing_value_in_every_evaluator(cell):
    result = run_clause("L05", "L05:3", msa_variant(missing=cell), "正式MSA.xlsx")
    assert result["status"] == "NG", result["suggestions"]
    assert cell in result["suggestions"]


@pytest.mark.parametrize("options", [{"formula": "L20"}, {"invalid_header": True}, {"missing_evaluator": True}])
def test_form_derived_msa_unknown_cell_or_unrecognized_layout_never_passes(options):
    result = run_clause("L05", "L05:3", msa_variant(**options), "正式MSA.xlsx")
    assert result["status"] == "UNABLE", result


def test_form_derived_msa_does_not_ignore_a_second_incomplete_report():
    result = run_clause("L05", "L05:3", msa_variant(second_missing=True), "两份正式MSA.xlsx")
    assert result["status"] == "NG", result["suggestions"]


def test_template_chart_with_unresolved_empty_cell_stays_unknown():
    result = run_clause("L05", "L05:3", msa_variant(missing="L20", keep_charts=True), "带图表MSA.xlsx")
    assert result["status"] == "UNABLE", result


def test_form_derived_msa_merged_values_cannot_count_as_independent_samples():
    book = load_workbook(io.BytesIO(msa_variant()))
    book["量具重复性和再现性"].merge_cells("C8:D8")
    content = io.BytesIO()
    book.save(content)
    book.close()
    result = run_clause("L05", "L05:3", content.getvalue(), "合并样本MSA.xlsx")
    assert result["status"] == "UNABLE", result
    assert "合并" in result["suggestions"]


MATERIALS = TEMPLATES / "APQP-材料、产品及分供方清单.xlsx"


def material_variant(value=1.2, number_format="0.0000", *, second=None, formula=False, keep_images=False):
    """Retain the original form; remove illustrations only for native-only cases."""
    book = load_workbook(io.BytesIO(MATERIALS.read_bytes()))
    sheet = book["清单"]
    if not keep_images:
        sheet._images.clear()
        sheet._charts.clear()
    sheet["B10"], sheet["M10"] = "P123", "=1+1" if formula else value
    sheet["M10"].number_format = number_format
    if second is not None:
        sheet["B11"], sheet["M11"] = "P456", second
        sheet["M11"].number_format = "0.000"
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def test_original_material_template_has_no_confirmed_part_rows():
    content = MATERIALS.read_bytes()
    before = hashlib.sha256(content).hexdigest()
    result = run_clause("L04", "L04:1", content, MATERIALS.name)
    assert result["status"] == "UNABLE", result
    assert hashlib.sha256(MATERIALS.read_bytes()).hexdigest() == before


@pytest.mark.parametrize(("value", "number_format"), [(1.2, "0.0000"), (0, "0.0000"), ("1.2300", "General")])
def test_form_derived_material_weight_uses_original_four_place_display(value, number_format):
    result = run_clause("L04", "L04:1", material_variant(value, number_format), "原生材料清单.xlsx")
    assert result["status"] == "PASS", result
    assert any(source["locator"].get("cell") == "M10" for source in result["locator"]["semantic"]["sources"])


@pytest.mark.parametrize(("value", "number_format"), [(1.2, "0.000"), (1.2, "0.00000"), ("1.23", "0.0000"), (None, "0.0000"), ("1.2300 kg", "General"), (True, "0.0000")])
def test_form_derived_material_weight_rejects_known_wrong_precision(value, number_format):
    result = run_clause("L04", "L04:1", material_variant(value, number_format), "原生材料清单.xlsx")
    assert result["status"] == "NG", result
    assert "M10" in result["suggestions"]


@pytest.mark.parametrize("options", [{"number_format": "General"}, {"number_format": "0.0000%"}, {"formula": True}])
def test_material_weight_uncertain_display_or_formula_never_passes(options):
    result = run_clause("L04", "L04:1", material_variant(**options), "原生材料清单.xlsx")
    assert result["status"] == "UNABLE", result


def test_material_weight_checks_second_part_instead_of_only_first():
    result = run_clause("L04", "L04:1", material_variant(second=2.3), "原生材料清单.xlsx")
    assert result["status"] == "NG", result
    assert "M11" in result["suggestions"]
