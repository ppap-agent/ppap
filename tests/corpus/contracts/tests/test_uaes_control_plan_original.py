"""UAES mark existence from retained cells, without changing the human Oracle."""

import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest
from audit.audits import RoundInput, RoundWork
from audit.checker_lib.source import NativeFacts, SourceAudit
from audit.standard_suites import predefined_directory_suites
from corpus_contracts.core_round import run_suite_round
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[4]
ORIGINAL = ROOT / "assets/UAES/样本/通用文件/表单模板/测试文件/10 控制计划/控制计划.xlsx"
PDF = ROOT / "assets/UAES/样本/F01R20V040_磁性罩/测试文件/10 控制计划/控制计划-模板3 WY (1).pdf"
MARKS = ("G20", "G21", "G22", "G34", "G35", "G36")


def run_original(content, filename=ORIGINAL.name):
    source = next(item for item in predefined_directory_suites() if item.source_path == "UAES/U08")
    rules = json.loads(source.files["rules.json"])
    rules["rules"] = [item for item in rules["rules"] if item["id"] == "U08:4"]
    rules["checker_binding"] = {"U08:4": source.entrypoints["U08:4"]}
    target = RoundInput("target", "file", "frozen-original", filename, "ready", rule_version_id="frozen-u08", rule_body=rules)
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


def test_native_pdf_mark_reaches_final_finding_with_original_page_region(monkeypatch):
    pdf = PDF
    content = pdf.read_bytes()
    before = hashlib.sha256(content).hexdigest()
    original_location = NativeFacts.cell_location
    metadata = []

    def track(native, *args):
        metadata.append(native.data)
        return original_location(native, *args)

    monkeypatch.setattr(NativeFacts, "cell_location", track)
    result = run_original(content, pdf.name)
    assert result["status"] == "PASS", result
    sources = result["locator"]["semantic"]["sources"]
    assert sources
    assert all(source["filename"] == pdf.name and source["file_version_id"] == "frozen-original" and source["role"] == "target" for source in sources)
    for source in sources:
        locator = source["locator"]
        assert set(locator) == {"kind", "page", "region"}
        assert locator["kind"] == "pdf_page" and 1 <= locator["page"] <= 4
        assert 0 <= locator["region"][0] < locator["region"][2] <= 842
        assert 0 <= locator["region"][1] < locator["region"][3] <= 596
    assert metadata and all(data["source"]["sha256"] == before and data["prepared_format"] == "xlsx" and data["prepared_sha256"] for data in metadata)
    mapped = [item["source"] for table in metadata[0]["tables"] for item in table["cells"].values() if item.get("source")]
    assert mapped and all(item["page"] and item["row"] and item["column"] and item["bbox"] for item in mapped)
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("corruption", ["sha", "frozen-version"])
def test_pdf_mark_cannot_pass_with_wrong_source_identity(monkeypatch, corruption):
    pdf = PDF
    if corruption == "sha":
        original_location = NativeFacts.cell_location

        def wrong_sha(native, *args):
            return {**original_location(native, *args), "source_sha256": "0" * 64}

        monkeypatch.setattr(NativeFacts, "cell_location", wrong_sha)
    else:
        original_witness = SourceAudit.witness

        def wrong_version(owner, locator, value, version=None, **options):
            return original_witness(owner, locator, value, "outside-frozen-round", **options)

        monkeypatch.setattr(SourceAudit, "witness", wrong_version)
        # Core rejects an outside-round reference before product mapping. This
        # is stronger than returning a publishable finding with that source.
        with pytest.raises(ValueError, match="result_invalid_file_reference"):
            run_original(pdf.read_bytes(), pdf.name)
        return
    result = run_original(pdf.read_bytes(), pdf.name)
    assert result["status"] == "UNABLE", result
    assert "本次冻结文件" in result["suggestions"]
    assert not result["locator"]["semantic"]["sources"]
