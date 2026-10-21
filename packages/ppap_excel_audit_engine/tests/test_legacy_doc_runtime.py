"""Legacy runtime routing consumes real Word output at the converter boundary."""

import gc
import tempfile
from io import BytesIO

import pytest
from docx import Document
from ppap_core.contracts import AuditInput, RuleInput
from ppap_docops.word import LegacyDocConversionError
from ppap_excel_audit_engine.app_runtime import run_audit_engine


@pytest.fixture(params=["v2", "v3"])
def profile(request):
    return request.param


@pytest.fixture
def conversion_directory(tmp_path, monkeypatch):
    directory = tmp_path / "temporary-conversion"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield directory
    finally:
        if was_enabled:
            gc.enable()
        gc.collect()


def psw_rules(profile):
    return RuleInput(rule_id="confirmed-psw-rules", rule_profile=profile, type="零件提交保证书", rules=[{"id": str(index)} for index in range(1, 11)])


def word_bytes(weight="1.2345", *, wrong_form=False):
    document = Document()
    table = document.add_table(rows=5, cols=2)
    if wrong_form:
        table.cell(0, 0).text = "发运清单"
        table.cell(1, 0).text = "运单号"
        table.cell(1, 1).text = "SHIP-001"
    else:
        table.cell(0, 0).text = "零件提交保证书"
        table.cell(1, 0).text = "重量(kg)"
        table.cell(1, 1).text = weight
        table.cell(2, 0).text = "供应商名称/供应商代码"
        table.cell(3, 0).text = "总公司/S001"
        table.cell(4, 0).text = "提交原因"
    output = BytesIO()
    document.save(output)
    return output.getvalue()


@pytest.mark.parametrize("source_format", ["doc", "docx"])
@pytest.mark.parametrize(("weight", "expected"), [("1.2345", "PASS"), ("1.234", "NG")])
def test_word_runtime_uses_actual_converted_fields_and_cleans_only_its_temporary_file(tmp_path, monkeypatch, profile, conversion_directory, source_format, weight, expected):
    converted = word_bytes(weight)
    source = tmp_path / f"原件.{source_format}"
    original = b"original DOC bytes supplied to the external converter" if source_format == "doc" else converted
    source.write_bytes(original)
    audit = AuditInput(audit_id="original-word-version", file_url=str(source), file_metadata={"version": "frozen"})

    def convert(content, name, **kwargs):
        assert source_format == "doc", "Native DOCX must not enter legacy conversion"
        assert content == original
        assert name == "原件.doc"
        return converted

    monkeypatch.setattr("ppap_excel_audit_engine.app_runtime.convert_legacy_doc_to_docx", convert)

    results = run_audit_engine(audit, psw_rules(profile))

    assert len(results) == 10
    assert results[1].status == expected
    if expected == "NG":
        assert "小数位数应为4位" in results[1].suggestions
    assert results[1].evidence.source_format == source_format
    assert results[1].evidence.used == "structure"
    assert list(conversion_directory.iterdir()) == []
    assert source.read_bytes() == original
    assert audit.file_metadata == {"version": "frozen"}


def test_readable_conversion_without_the_required_psw_form_keeps_the_declared_rule_contract(tmp_path, monkeypatch, profile, conversion_directory):
    source = tmp_path / "original.doc"
    source.write_bytes(b"original bytes sent to the external converter")
    original = source.read_bytes()
    monkeypatch.setattr("ppap_excel_audit_engine.app_runtime.convert_legacy_doc_to_docx", lambda *args, **kwargs: word_bytes(wrong_form=True))

    results = run_audit_engine(AuditInput(audit_id="wrong-word-form", file_url=str(source)), psw_rules(profile))

    assert len(results) == 10
    first_description = "附件列内容检查" if profile == "v2" else "生产件批准交付物文件清单附件检查"
    assert results[0].item == f"零件提交保证书 - {first_description}"
    assert results[-1].item == "零件提交保证书 - 条件批准补充评论检查"
    assert all(result.status == "NG" and "结构契约的主表" in result.suggestions for result in results)
    assert all(result.classification == "DOCX转换质量" for result in results)
    assert all(result.evidence.used == "none" and result.evidence.diagnostics[0].code == "psw.structure.insufficient" for result in results)
    assert list(conversion_directory.iterdir()) == []
    assert source.read_bytes() == original


@pytest.mark.parametrize("code", ["converter_unavailable", "conversion_timeout"])
def test_legacy_converter_failure_reports_each_rule_as_an_execution_error(tmp_path, monkeypatch, profile, conversion_directory, code):
    source = tmp_path / "original.doc"
    source.write_bytes(b"original bytes sent to the external converter")
    original = source.read_bytes()

    def convert(content, name, **kwargs):
        assert content == original and name == source.name
        raise LegacyDocConversionError(code, "Converter cannot complete this request")

    monkeypatch.setattr("ppap_excel_audit_engine.app_runtime.convert_legacy_doc_to_docx", convert)

    results = run_audit_engine(AuditInput(audit_id="word-conversion-failed", file_url=str(source)), psw_rules(profile))

    assert len(results) == 10
    assert all(result.status == "ERR" and code in result.suggestions for result in results)
    assert all(result.classification == "DOC转换运行时" for result in results)
    for result in results:
        assert result.evidence.used == "none"
        assert result.evidence.sources == ()
        assert result.evidence.diagnostics[0].code == "psw.doc.conversion_failed"
        assert result.evidence.diagnostics[0].stage == "conversion"
    assert list(conversion_directory.iterdir()) == []
    assert source.read_bytes() == original


def test_invalid_legacy_bytes_use_the_real_converter_validation_and_never_become_business_ng(tmp_path, profile, conversion_directory):
    source = tmp_path / "invalid.doc"
    source.write_bytes(b"not an OLE Word document")

    results = run_audit_engine(AuditInput(audit_id="invalid-original-word", file_url=str(source)), psw_rules(profile))

    assert len(results) == 10
    assert all(result.status == "ERR" and "invalid_legacy_doc" in result.suggestions for result in results)
    assert all(result.evidence.diagnostics[0].code == "psw.doc.conversion_failed" for result in results)
    assert list(conversion_directory.iterdir()) == []
    assert source.read_bytes() == b"not an OLE Word document"
