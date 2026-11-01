"""PSW PDF decisions use actual tables and original pages, not transport errors."""

import asyncio
import gc
import json
from base64 import b64decode
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

import fitz
import pytest
from audit.contracts import (
    AuditInput,
    RuleInput,
)
from audit.engine.app_runtime import run_audit_engine
from audit.checkers.v2.psw_pdf_agent import PswPDFAgent as V2PswPDFAgent
from audit.checkers.v3.psw_pdf_agent import PswPDFAgent as V3PswPDFAgent
from audit.engine.checkers.client import AIClient
from audit.engine.checkers.psw_pdf_evidence import assess_psw_conversion_evidence
from audit.engine.runtime import bind_runtime
from documents.contracts import ConversionArtifact
from openpyxl import Workbook, load_workbook
from PIL import Image


@pytest.fixture(params=["v2", "v3"])
def profile(request):
    return request.param


@pytest.fixture(autouse=True)
def explicit_cleanup_boundary():
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()
        gc.collect()


@pytest.fixture
def original_pdf(tmp_path):
    path = tmp_path / "original.pdf"
    with fitz.open() as document:
        page = document.new_page(width=240, height=320)
        page.draw_rect(page.rect, color=(0, 0, 1), fill=(0, 0, 1))
        document.save(path)
    return path


def psw_rules(profile, *, count=10):
    return RuleInput(
        rule_id="confirmed-psw-rules",
        rule_profile=profile,
        type="零件提交保证书",
        rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, count + 1)],
    )


@pytest.fixture
def converted_table(tmp_path):
    path = tmp_path / "converted.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "PSW主表"
    sheet["A1"] = "零件提交保证书"
    sheet["A2"] = "重量(kg)"
    sheet["B2"] = "1.2345"
    sheet["A3"] = "供应商名称/供应商代码"
    sheet["A4"] = "示例总公司/S001"
    sheet["A20"] = "提交原因"
    sheet["A45"] = "认可情况"
    workbook.save(path)
    workbook.close()
    return path


def use_external_conversion(monkeypatch, source, outcome):
    def convert(file_ref, *, enable_cache=True, doc_type=None):
        assert file_ref.location == str(source)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr("audit.engine.app_runtime.convert_pdf_to_excel_auto", convert)
    monkeypatch.setattr("audit.checkers.v2.pdf_agent.convert_pdf_to_excel_auto", convert)
    monkeypatch.setattr("audit.checkers.v3.pdf_agent.convert_pdf_to_excel_auto", convert)


@pytest.mark.parametrize("error_code", ["manual_linegrid_failed", "download_failed", ""], ids=["conversion-rejected", "download-failed", "no-code"])
def test_failed_table_conversion_does_not_become_a_business_ng(original_pdf, monkeypatch, profile, error_code):
    original_bytes = original_pdf.read_bytes()

    def convert(file_ref, *, enable_cache=True, doc_type=None):
        assert file_ref.location == str(original_pdf)
        return ConversionArtifact(success=False, diagnostics={"error_code": error_code})

    monkeypatch.setattr("audit.engine.app_runtime.convert_pdf_to_excel_auto", convert)
    monkeypatch.setattr("audit.checkers.v2.pdf_agent.convert_pdf_to_excel_auto", convert)

    results = run_audit_engine(AuditInput(audit_id="psw-conversion-failed", file_url=str(original_pdf)), psw_rules(profile, count=2))

    assert [result.status for result in results] == ["ERR", "ERR"]
    assert all(result.evidence is not None and result.evidence.used == "none" for result in results)
    assert all(result.evidence.diagnostics[0].code == "psw.pdf.conversion_failed" for result in results)
    assert original_pdf.read_bytes() == original_bytes


@pytest.mark.parametrize(
    "response",
    ["not-json", "[]", "null", json.dumps({"classification": "视觉检查", "item": "视觉检查", "priority": "P0", "status": "ERR", "suggestions": "模型无法完成识别"})],
    ids=["invalid-json", "list", "null", "explicit-error"],
)
def test_invalid_model_response_does_not_become_a_business_ng(original_pdf, converted_table, monkeypatch, profile, response):
    use_external_conversion(monkeypatch, original_pdf, ConversionArtifact(success=True, xlsx_path=str(converted_table)))
    monkeypatch.setattr(AIClient, "image_analysis_completion", lambda self, **kwargs: response)

    results = run_audit_engine(AuditInput(audit_id="psw-invalid-model-response", file_url=str(original_pdf)), psw_rules(profile, count=5))

    assert len(results) == 5
    assert results[1].status == "PASS"
    assert [results[index].status for index in (2, 4)] == ["ERR", "ERR"]
    assert all(results[index].evidence.diagnostics[0].code == "psw.visual.unverifiable" for index in (2, 4))
    assert not converted_table.exists()


def test_rule_five_requires_the_reported_signature_even_when_the_model_claims_pass(original_pdf, converted_table, monkeypatch, profile):
    original = original_pdf.read_bytes()
    use_external_conversion(monkeypatch, original_pdf, ConversionArtifact(success=True, xlsx_path=str(converted_table)))
    calls = []

    def analyze(self, **kwargs):
        calls.append(kwargs)
        return json.dumps(
            {
                "classification": "视觉检查",
                "item": "经授权签字和盖章",
                "priority": "P0",
                "status": "PASS",
                "suggestions": "印刷姓名存在，签字栏为空",
                "psw_stamp_ends_with_company": True,
                "authorized_signature_present": False,
                "psw_stamp_present": True,
            }
        )

    monkeypatch.setattr(AIClient, "image_analysis_completion", analyze)
    results = run_audit_engine(AuditInput(audit_id="psw-signature-facts", file_url=str(original_pdf)), psw_rules(profile, count=5))

    assert results[4].status == "NG"
    assert "签字" in results[4].suggestions
    assert [results[index].status for index in (1, 2, 3)] == ["PASS"] * 3
    assert len(calls) == 2
    signature_call = next(call for call in calls if "经授权的供方代表签字" in call["user_text"])
    assert '"status"' not in signature_call["user_text"]
    assert "印刷姓名" in signature_call["user_text"]
    assert original_pdf.read_bytes() == original
    assert not converted_table.exists()


@pytest.mark.parametrize(
    ("signature", "stamp", "expected"),
    [
        (True, True, "PASS"),
        (False, True, "NG"),
        (True, False, "NG"),
        (False, False, "NG"),
        (None, True, "UNABLE"),
        (True, None, "UNABLE"),
        (None, None, "UNABLE"),
        (False, None, "NG"),
        (None, False, "NG"),
    ],
)
def test_rule_five_uses_both_signature_facts_without_turning_unknown_into_ng(original_pdf, converted_table, monkeypatch, profile, signature, stamp, expected):
    use_external_conversion(monkeypatch, original_pdf, ConversionArtifact(success=True, xlsx_path=str(converted_table)))
    response = {
        "psw_stamp_ends_with_company": True,
        "authorized_signature_present": signature,
        "psw_stamp_present": stamp,
        "classification": "不能覆盖冻结规则的分类",
        "item": "不能覆盖冻结规则的名称",
        "priority": "P0",
        "status": "NG" if expected == "PASS" else "PASS",
        "suggestions": "模型结论不能代替两项事实",
    }
    monkeypatch.setattr(AIClient, "image_analysis_completion", lambda self, **kwargs: json.dumps(response))

    results = run_audit_engine(AuditInput(audit_id="psw-signature-fact-matrix", file_url=str(original_pdf)), psw_rules(profile, count=5))

    assert results[4].status == expected
    assert results[4].classification == "经授权签字和盖章检查"
    assert results[4].item == "经授权签字和盖章检查"
    assert [results[index].status for index in (1, 2, 3)] == ["PASS"] * 3
    assert results[4].evidence.used == ("none" if expected == "UNABLE" else "visual")
    if signature is False:
        assert "签字" in results[4].suggestions
    if stamp is False:
        assert "盖章" in results[4].suggestions
    assert not converted_table.exists()


def test_missing_required_model_facts_are_not_a_document_nonconformity(original_pdf, converted_table, monkeypatch, profile):
    use_external_conversion(monkeypatch, original_pdf, ConversionArtifact(success=True, xlsx_path=str(converted_table)))
    response = {"classification": "视觉检查", "item": "视觉检查", "priority": "P0", "status": "PASS", "suggestions": "已完成检查"}
    monkeypatch.setattr(AIClient, "image_analysis_completion", lambda self, **kwargs: json.dumps(response))

    results = run_audit_engine(AuditInput(audit_id="psw-missing-model-facts", file_url=str(original_pdf)), psw_rules(profile))

    assert len(results) == 10
    assert results[1].status == "PASS"
    assert [results[index].status for index in (2, 4, 7, 8, 9)] == ["UNABLE"] * 5


def test_explicit_model_inability_is_not_a_system_failure(original_pdf, converted_table, monkeypatch, profile):
    use_external_conversion(monkeypatch, original_pdf, ConversionArtifact(success=True, xlsx_path=str(converted_table)))
    response = {"classification": "视觉检查", "item": "视觉检查", "priority": "P0", "status": "UNABLE", "suggestions": "无法辨认签字"}
    monkeypatch.setattr(AIClient, "image_analysis_completion", lambda self, **kwargs: json.dumps(response))

    results = run_audit_engine(AuditInput(audit_id="psw-model-unable", file_url=str(original_pdf)), psw_rules(profile, count=5))

    assert results[1].status == "PASS"
    assert [results[index].status for index in (2, 4)] == ["UNABLE", "UNABLE"]
    assert all(results[index].evidence.diagnostics[0].severity == "warning" for index in (2, 4))


@pytest.mark.parametrize(
    ("note", "visual_status", "company_stamp", "visual_expected", "submission_expected", "submission_source"),
    [(None, "Pass", True, "PASS", "PASS", "both"), (None, "NG", False, "NG", "NG", "both"), ("材料变更后的重新提交", "NG", False, "NG", "PASS", "structure")],
    ids=["positive-original-facts", "negative-original-facts", "actual-submission-note"],
)
def test_complete_pdf_keeps_structured_decisions_and_original_visual_results(
    original_pdf, converted_table, monkeypatch, profile, note, visual_status, company_stamp, visual_expected, submission_expected, submission_source
):
    original_bytes = original_pdf.read_bytes()
    if note:
        workbook = load_workbook(converted_table)
        workbook.active["A33"] = "其他-请在下方说明"
        workbook.active["B33"] = note
        workbook.save(converted_table)
        workbook.close()
    use_external_conversion(monkeypatch, original_pdf, ConversionArtifact(success=True, xlsx_path=str(converted_table), strategy_used="manual_linegrid"))
    calls = []

    def analyze(self, **kwargs):
        calls.append(kwargs)
        return json.dumps(
            {
                "classification": "视觉检查",
                "item": "视觉检查",
                "priority": "P0",
                "status": visual_status,
                "suggestions": "视觉检查完成",
                "psw_stamp_ends_with_company": company_stamp,
                "authorized_signature_present": company_stamp,
                "psw_stamp_present": company_stamp,
                "authorization_table_present": False,
                "authorization_company_stamp_present": False,
                "disposition_visible": True,
                "approved_checked": company_stamp,
                "conditional_checked": False,
                "rejected_checked": False,
                "dimensional_checked": True,
                "appearance_checked": True,
                "material_functional_checked": True,
                "statistical_checked": True,
                "requirements_yes_checked": True,
            }
        )

    monkeypatch.setattr(AIClient, "image_analysis_completion", analyze)

    results = run_audit_engine(AuditInput(audit_id="psw-hybrid", file_url=str(original_pdf)), psw_rules(profile))

    assert [results[index].status for index in (0, 1, 3, 5)] == ["NG", "PASS", "PASS", "NG"]
    assert [results[index].status for index in (2, 4, 7, 8, 9)] == [visual_expected] * 5
    assert results[6].status == submission_expected
    assert [result.evidence.used for result in results] == ["structure", "structure", "visual", "structure", "visual", "structure", submission_source, "visual", "visual", "visual"]
    assert all(result.evidence.source_format == "pdf" for result in results)
    assert results[1].evidence.conversion_strategies == ("manual_linegrid",)
    if note:
        assert all("提交原因" not in call["user_text"] for call in calls)
    assert calls
    for call in calls:
        assert call["image_base64_list"]
        for encoded in call["image_base64_list"]:
            with Image.open(BytesIO(b64decode(encoded.split(",", 1)[-1]))) as image:
                red, green, blue = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
                assert red < 10 and green < 10 and blue > 245
    assert not converted_table.exists()
    assert original_pdf.read_bytes() == original_bytes


@pytest.mark.parametrize("missing_anchor", [False, True], ids=["complete-anchors", "missing-supplier-anchor"])
@pytest.mark.parametrize(("weight", "expected"), [("1.2345", "PASS"), ("1.234", "NG")], ids=["four-decimal-weight", "three-decimal-weight"])
def test_table_diagnostics_do_not_replace_the_actual_weight_decision(original_pdf, converted_table, monkeypatch, profile, missing_anchor, weight, expected):
    workbook = load_workbook(converted_table)
    workbook.active["B2"] = weight
    if missing_anchor:
        workbook.active["A3"] = None
    workbook.save(converted_table)
    workbook.close()
    assessment = assess_psw_conversion_evidence(converted_table)
    assert assessment.to_dict() == {
        "version": "1.0",
        "structured_fields": {"complete": not missing_anchor, "missing_anchors": ["供应商名称/供应商代码"] if missing_anchor else []},
        "checkbox_controls": {"complete": False, "count": 0, "complete_rules": [], "reason": "no_authoritative_checkbox_controls"},
        "stamp_signature": {"complete": False, "workbook_image_count": 0, "reason": "converted_workbook_not_authoritative_for_stamp_or_signature"},
        "diagnostics": (["missing_structured_anchors"] if missing_anchor else []) + ["missing_authoritative_checkbox_controls", "stamp_signature_requires_original_pdf"],
    }
    use_external_conversion(monkeypatch, original_pdf, ConversionArtifact(success=True, xlsx_path=str(converted_table)))
    model_calls = []

    def reject_visual(self, **kwargs):
        model_calls.append(kwargs)
        raise AssertionError("Structured PSW rules must not request image analysis")

    monkeypatch.setattr(AIClient, "image_analysis_completion", reject_visual)

    results = run_audit_engine(AuditInput(audit_id="psw-structured", file_url=str(original_pdf)), psw_rules(profile, count=2))

    assert len(results) == 2
    assert results[1].status == expected
    assert results[1].evidence.used == "structure"
    assert model_calls == []
    assert not converted_table.exists()


def test_converter_exception_before_an_artifact_is_an_execution_error(original_pdf, monkeypatch, profile):
    use_external_conversion(monkeypatch, original_pdf, OSError("converter unavailable"))

    results = run_audit_engine(AuditInput(audit_id="psw-converter-exception", file_url=str(original_pdf)), psw_rules(profile, count=2))

    assert [result.status for result in results] == ["ERR", "ERR"]
    assert all(result.evidence.diagnostics[0].code == "psw.pdf.conversion_failed" for result in results)


def test_unreadable_original_pages_are_execution_errors_without_model_calls(original_pdf, monkeypatch, profile):
    original_pdf.write_bytes(b"not a readable PDF")
    original_bytes = original_pdf.read_bytes()
    use_external_conversion(monkeypatch, original_pdf, ConversionArtifact(success=False))
    model_calls = []

    def reject_visual(self, **kwargs):
        model_calls.append(kwargs)
        raise AssertionError("Unreadable pages must not produce model inputs")

    monkeypatch.setattr(AIClient, "image_analysis_completion", reject_visual)

    results = run_audit_engine(AuditInput(audit_id="psw-render-failed", file_url=str(original_pdf)), psw_rules(profile, count=5))

    assert len(results) == 5
    assert [results[index].status for index in (2, 4)] == ["ERR", "ERR"]
    assert all(results[index].evidence.diagnostics[0].code == "psw.visual.runtime_error" for index in (2, 4))
    assert model_calls == []
    assert original_pdf.read_bytes() == original_bytes


@pytest.mark.parametrize("failure", [RuntimeError, TypeError], ids=["runtime-error", "type-error"])
def test_model_service_exception_preserves_structured_results_and_releases_converted_files(original_pdf, converted_table, monkeypatch, profile, failure):
    original_bytes = original_pdf.read_bytes()
    use_external_conversion(monkeypatch, original_pdf, ConversionArtifact(success=True, xlsx_path=str(converted_table)))
    attempted_prompts = []

    def unavailable(self, **kwargs):
        attempted_prompts.append(kwargs["user_text"])
        raise failure("model service unavailable")

    monkeypatch.setattr(AIClient, "image_analysis_completion", unavailable)

    results = run_audit_engine(AuditInput(audit_id="psw-model-service-failed", file_url=str(original_pdf)), psw_rules(profile, count=5))

    assert len(results) == 5
    assert results[1].status == "PASS"
    assert [results[index].status for index in (2, 4)] == ["ERR", "ERR"]
    assert all(results[index].evidence.diagnostics[0].code == "psw.evaluation.runtime_error" for index in (2, 4))
    assert len(attempted_prompts) == 2
    assert len(set(attempted_prompts)) == 2
    assert not converted_table.exists()
    assert original_pdf.read_bytes() == original_bytes


@pytest.mark.parametrize("converted", [True, False], ids=["with-table", "without-table"])
def test_low_level_empty_rule_input_never_silently_returns_no_results(original_pdf, converted_table, monkeypatch, profile, converted):
    artifact = ConversionArtifact(success=True, xlsx_path=str(converted_table)) if converted else ConversionArtifact(success=False)
    use_external_conversion(monkeypatch, original_pdf, artifact)
    agent_type = V2PswPDFAgent if profile == "v2" else V3PswPDFAgent
    with bind_runtime(None):
        agent = agent_type(AuditInput(audit_id="psw-empty-rules", file_url=str(original_pdf)), psw_rules(profile, count=0))
        try:
            results = asyncio.run(agent.run())
        finally:
            agent.close()

    assert len(results) == (10 if converted else 1)
    assert all(result.evidence is not None for result in results)
    if converted:
        assert results[1].status == "PASS"
        assert results[2].status == "ERR"
        assert results[2].evidence.diagnostics[0].code == "psw.evaluation.rule_unavailable"
        assert not converted_table.exists()
    else:
        assert results[0].status == "ERR"
        assert results[0].evidence.diagnostics[0].code == "psw.evaluation.rule_unavailable"


@pytest.mark.parametrize("rows", [(33, 45, 50), (100, 101, 102)], ids=["near-psw-fields", "unrelated-controls"])
def test_actual_converted_controls_do_not_replace_original_pdf_checkbox_facts(original_pdf, converted_table, monkeypatch, profile, rows):
    with ZipFile(converted_table) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    sheet_xml = ElementTree.fromstring(parts["xl/worksheets/sheet1.xml"])
    controls = ElementTree.fromstring('<controls xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>')
    relationships = ElementTree.Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
    content_types = ElementTree.fromstring(parts["[Content_Types].xml"])
    for index, row in enumerate(rows, start=1):
        controls.append(
            ElementTree.fromstring(f"""<control xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
          xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" name="Checked{index}" shapeId="{1024 + index}" r:id="rIdCtrl{index}">
          <controlPr><anchor moveWithCells="1" sizeWithCells="1">
            <from><xdr:col>1</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{row - 1}</xdr:row><xdr:rowOff>0</xdr:rowOff></from>
            <to><xdr:col>2</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{row}</xdr:row><xdr:rowOff>0</xdr:rowOff></to>
          </anchor></controlPr></control>""")
        )
        ElementTree.SubElement(
            relationships, "Relationship", Id=f"rIdCtrl{index}", Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/ctrlProp", Target=f"../ctrlProps/ctrlProp{index}.xml"
        )
        parts[f"xl/ctrlProps/ctrlProp{index}.xml"] = b'<formControlPr xmlns="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main" objectType="CheckBox" checked="Checked"/>'
        ElementTree.SubElement(
            content_types,
            "{http://schemas.openxmlformats.org/package/2006/content-types}Override",
            PartName=f"/xl/ctrlProps/ctrlProp{index}.xml",
            ContentType="application/vnd.ms-excel.controlproperties+xml",
        )
    sheet_xml.append(controls)
    parts["xl/worksheets/sheet1.xml"] = ElementTree.tostring(sheet_xml)
    parts["xl/worksheets/_rels/sheet1.xml.rels"] = ElementTree.tostring(relationships)
    parts["[Content_Types].xml"] = ElementTree.tostring(content_types)
    with ZipFile(converted_table, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    assessment = assess_psw_conversion_evidence(converted_table)
    assert assessment.checkbox_control_count == 3
    assert assessment.checkbox_controls_complete is False
    assert assessment.supports_checkbox_rule(8) is False
    assert "checkbox_controls_observed_without_completeness_proof" in assessment.diagnostics
    use_external_conversion(monkeypatch, original_pdf, ConversionArtifact(success=True, xlsx_path=str(converted_table)))
    response = {
        "classification": "视觉检查",
        "item": "视觉检查",
        "priority": "P0",
        "status": "PASS",
        "suggestions": "",
        "psw_stamp_ends_with_company": True,
        "disposition_visible": True,
        "approved_checked": False,
        "conditional_checked": False,
        "rejected_checked": False,
    }
    monkeypatch.setattr(AIClient, "image_analysis_completion", lambda self, **kwargs: json.dumps(response))

    results = run_audit_engine(AuditInput(audit_id="psw-converted-controls", file_url=str(original_pdf)), psw_rules(profile, count=8))

    assert len(results) == 8
    assert results[7].status == "NG"
    assert "需且仅需勾选一个选项" in results[7].suggestions
    assert results[7].evidence.used == "visual"
