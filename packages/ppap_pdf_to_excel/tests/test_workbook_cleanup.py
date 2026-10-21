"""Workbook loading respects ownership of temporary conversion files."""

import pytest
from openpyxl import Workbook
from ppap_core.contracts import ConversionArtifact, FileRef
from ppap_pdf_to_excel import load_workbook_auto


@pytest.mark.parametrize("cleanup", [True, False], ids=["pipeline-owned", "caller-owned"])
def test_invalid_converted_workbook_preserves_the_load_error_and_respects_cleanup(tmp_path, monkeypatch, cleanup):
    converted = tmp_path / "converted.xlsx"
    converted.write_bytes(b"not an Excel workbook")
    auxiliary = tmp_path / "conversion.html"
    auxiliary.write_text("conversion diagnostic", encoding="utf-8")
    source = "https://files.example.test/flow.PDF?signature=fixture#page=1"

    def convert(file_ref, *, enable_cache=True):
        assert file_ref == FileRef(location=source)
        assert enable_cache is True
        return ConversionArtifact(success=True, xlsx_path=str(converted), temp_files=[str(converted), str(auxiliary)])

    monkeypatch.setattr("ppap_pdf_to_excel.pipeline.convert_pdf_to_excel_auto", convert)

    with pytest.raises(Exception, match="无法加载文件"):
        load_workbook_auto(source, cleanup=cleanup)

    assert converted.exists() is not cleanup
    assert auxiliary.exists() is not cleanup


@pytest.mark.parametrize("cleanup", [True, False], ids=["pipeline-owned", "caller-owned"])
def test_valid_converted_workbook_remains_readable_and_respects_cleanup(tmp_path, monkeypatch, cleanup):
    converted = tmp_path / "converted.xlsx"
    workbook = Workbook()
    workbook.active.append(["工序号", "工序名称"])
    workbook.active.append([10, "切割"])
    workbook.save(converted)
    workbook.close()
    auxiliary = tmp_path / "conversion.html"
    auxiliary.write_text("conversion diagnostic", encoding="utf-8")

    def convert(file_ref, *, enable_cache=True):
        return ConversionArtifact(success=True, xlsx_path=str(converted), temp_files=[str(converted), str(auxiliary)])

    monkeypatch.setattr("ppap_pdf_to_excel.pipeline.convert_pdf_to_excel_auto", convert)

    loaded = load_workbook_auto("flow.pdf", cleanup=cleanup)
    try:
        assert list(loaded.active.values) == [("工序号", "工序名称"), (10, "切割")]
        assert converted.exists() is not cleanup
        assert auxiliary.exists() is not cleanup
    finally:
        loaded.close()


@pytest.mark.parametrize("cleanup", [True, False], ids=["default-cleanup", "no-cleanup"])
def test_original_excel_is_not_a_temporary_conversion_file(tmp_path, cleanup):
    source = tmp_path / "original.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "原始资料"
    workbook.save(source)
    workbook.close()
    original_bytes = source.read_bytes()

    loaded = load_workbook_auto(str(source), cleanup=cleanup)
    try:
        assert loaded.active["A1"].value == "原始资料"
    finally:
        loaded.close()

    assert source.read_bytes() == original_bytes
