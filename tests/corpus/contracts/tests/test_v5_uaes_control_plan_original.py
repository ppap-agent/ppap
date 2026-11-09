"""UAES mark existence from retained cells, without changing the human Oracle."""

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
ORIGINAL = ROOT / "assets/corpus/documents/v4-uaes/控制计划.xlsx"
MARKS = ("G20", "G21", "G22", "G34", "G35", "G36")


def run_original(content):
    source = next(item for item in predefined_directory_suites() if item.source_path == "v5/UAES/U08")
    rules = json.loads(source.files["rules.json"])
    rules["rules"] = [item for item in rules["rules"] if item["id"] == "U08:4"]
    rules["checker_binding"] = {"U08:4": source.entrypoints["U08:4"]}
    target = RoundInput("target", "file", "frozen-original", ORIGINAL.name, "ready", rule_version_id="frozen-u08", rule_body=rules)
    result = run_suite_round(
        RoundWork("original-u08", "tenant", [target]),
        {"frozen-original": content},
        suites={"frozen-original": replace(source, entrypoints=rules["checker_binding"])},
    )
    assert result["coverage"]["rule_items"][0]["coverage"] == "executed"
    assert len(result["findings"]) == 1
    return result["findings"][0]


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Native mark existence must not call a model")

    monkeypatch.setattr(SourceAudit, "visual_pdf", forbidden)
    monkeypatch.setattr(SourceAudit, "text_result", forbidden)


def variant(*, clear=False, merged_marks=False, formula=False):
    book = load_workbook(io.BytesIO(ORIGINAL.read_bytes()))
    sheet = book["Table 1"]
    if clear:
        for cell in MARKS:
            sheet[cell] = None
    if merged_marks:
        sheet.merge_cells("G20:G22")
        sheet.merge_cells("G34:G36")
    if formula:
        sheet["G20"] = "=1+1"
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def test_original_control_plan_has_independent_mark_cells_despite_merged_header_and_footer():
    content = ORIGINAL.read_bytes()
    before = hashlib.sha256(content).hexdigest()
    book = load_workbook(io.BytesIO(content), data_only=False)
    sheet = book["Table 1"]
    assert sheet["G8"].value == "特殊/特性/分类 (Special Char.\nClass)"
    assert {sheet[cell].value for cell in MARKS} == {"F/C,F"}
    assert "G8:G10" in {str(item) for item in sheet.merged_cells.ranges}
    book.close()
    result = run_original(content)
    assert result["status"] == "PASS", result
    sources = result["locator"]["semantic"]["sources"]
    assert {item["locator"]["cell"] for item in sources} == set(MARKS)
    assert all(item["locator"]["sheet"] == "Table 1" for item in sources)
    assert hashlib.sha256(ORIGINAL.read_bytes()).hexdigest() == before


def test_no_independent_mark_with_unresolved_merged_scope_is_unknown():
    result = run_original(variant(clear=True))
    assert result["status"] == "UNABLE", result
    assert "合并" in result["suggestions"]


def test_only_merged_mark_values_cannot_prove_an_independent_mark():
    result = run_original(variant(merged_marks=True))
    assert result["status"] == "UNABLE", result


def test_uncached_formula_is_not_ignored_even_when_other_marks_exist():
    result = run_original(variant(formula=True))
    assert result["status"] == "UNABLE", result
    assert "公式" in result["suggestions"]
