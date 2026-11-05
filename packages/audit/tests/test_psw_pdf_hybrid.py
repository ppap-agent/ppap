"""PSW PDF decisions use actual tables and original pages, not transport errors."""

import asyncio
import gc
from xml.etree import ElementTree
from zipfile import ZipFile

import fitz
import pytest
from audit.checkers.v2.psw_pdf_agent import PswPDFAgent as V2PswPDFAgent
from audit.checkers.v3.psw_pdf_agent import PswPDFAgent as V3PswPDFAgent
from audit.contracts import (
    AuditInput,
    RuleInput,
)
from audit.engine.app_runtime import run_audit_engine
from audit.engine.checkers.client import AIClient
from audit.engine.checkers.psw_pdf_evidence import assess_psw_conversion_evidence
from audit.engine.runtime import bind_runtime
from documents.contracts import ConversionArtifact
from openpyxl import Workbook, load_workbook


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


@pytest.mark.parametrize(
    ("error_code", "expected"),
    [
        ("manual_linegrid_failed", "ERR"),
        ("download_failed", "ERR"),
        ("", "ERR"),
        ("ocr_evidence_unconfigured", "UNABLE"),
        ("paddle_evidence_unconfigured", "UNABLE"),
        ("paddle_page_selection_required", "UNABLE"),
        ("paddle_table_unconfirmed", "UNABLE"),
        ("paddle_table_incomplete", "UNABLE"),
        ("paddle_request_budget_exhausted", "UNABLE"),
        ("paddle_scope_closed", "UNABLE"),
    ],
)
def test_failed_table_conversion_does_not_become_a_business_ng(original_pdf, monkeypatch, profile, error_code, expected):
    original_bytes = original_pdf.read_bytes()

    def convert(file_ref, *, enable_cache=True, doc_type=None):
        assert file_ref.location == str(original_pdf)
        return ConversionArtifact(success=False, diagnostics={"error_code": error_code})

    monkeypatch.setattr("audit.engine.app_runtime.convert_pdf_to_excel_auto", convert)
    monkeypatch.setattr("audit.checkers.v2.pdf_agent.convert_pdf_to_excel_auto", convert)

    results = run_audit_engine(AuditInput(audit_id="psw-conversion-failed", file_url=str(original_pdf)), psw_rules(profile, count=2))

    assert [result.status for result in results] == [expected, expected]
    assert all(result.evidence is not None and result.evidence.used == "none" for result in results)
    code = "psw.pdf.conversion_failed" if expected == "ERR" else "psw.pdf." + error_code
    assert all(result.evidence.diagnostics[0].code == code for result in results)
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

    monkeypatch.setattr(AIClient, "structured_completion", reject_visual)

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

    results = run_audit_engine(AuditInput(audit_id="psw-converted-controls", file_url=str(original_pdf)), psw_rules(profile, count=8))

    assert len(results) == 8
    assert results[7].status == "UNABLE"
    assert "无法判断" in results[7].suggestions
    assert results[7].evidence.used == "none"


@pytest.fixture(autouse=True)
def no_psw_llm_or_render(monkeypatch):
    attempts = []

    def reject(*args, **kwargs):
        attempts.append("LLM or image rendering")
        raise AssertionError("PSW facts must use native/Paddle only")

    monkeypatch.setattr(AIClient, "structured_completion", reject)
    monkeypatch.setattr(V2PswPDFAgent, "_get_pdf_images_sync", reject)
    monkeypatch.setattr(V3PswPDFAgent, "_get_pdf_images_sync", reject)
    yield
    assert attempts == []


def test_rule_eight_uses_native_paddle_facts_without_llm_fallback(original_pdf, converted_table, monkeypatch, profile):
    use_external_conversion(monkeypatch, original_pdf, ConversionArtifact(success=True, xlsx_path=str(converted_table)))
    calls = []

    def read(audit, fields, witness, *, operation, cache, batch_fields=None):
        calls.append((operation, tuple(fields), id(cache)))
        assert audit.file_url == str(original_pdf)
        return dict.fromkeys(fields)

    monkeypatch.setattr("audit.checkers.v4.visual_facts.read_original_pdf_visual_facts", read)
    original = original_pdf.read_bytes()
    results = run_audit_engine(AuditInput(audit_id="psw-no-checkbox-proof", file_url=str(original_pdf)), psw_rules(profile, count=10))

    assert results[7].status == "UNABLE"
    assert results[7].evidence.diagnostics[0].code == "psw.paddle.fact_unconfirmed"
    assert [operation for operation, _, _ in calls] == ["psw_rule_3", "psw_rule_5", "psw_rule_7", "psw_rule_8", "psw_rule_9", "psw_rule_10"]
    assert len({cache_id for _, _, cache_id in calls}) == 1
    assert original_pdf.read_bytes() == original
    assert not converted_table.exists()


@pytest.mark.parametrize(
    ("signature", "stamp", "expected"), [(True, True, "PASS"), (False, True, "NG"), (True, False, "NG"), (None, True, "UNABLE"), (True, None, "UNABLE"), (False, None, "NG"), (None, None, "UNABLE")]
)
def test_signature_rule_uses_established_facts_and_preserves_unknown(original_pdf, converted_table, monkeypatch, profile, signature, stamp, expected):
    use_external_conversion(monkeypatch, original_pdf, ConversionArtifact(success=True, xlsx_path=str(converted_table)))

    def read(audit, fields, witness, **kwargs):
        witness({"kind": "pdf_region", "page": 1, "engine": "paddle", "bbox": [0, 0, 20, 20]}, "已确认的原件区域", visual=True)
        return {"psw_stamp_ends_with_company": None, "authorized_signature_present": signature, "psw_stamp_present": stamp}

    monkeypatch.setattr("audit.checkers.v4.visual_facts.read_original_pdf_visual_facts", read)
    results = run_audit_engine(AuditInput(audit_id="psw-signature-facts", file_url=str(original_pdf)), psw_rules(profile, count=5))
    assert results[4].status == expected
    assert results[1].status == "PASS"
    assert results[4].evidence.used == "visual"
    assert results[4].evidence.conversion_strategies == ("native_pdf", "paddle_single_page")
    assert not converted_table.exists()


@pytest.mark.parametrize("failure", [RuntimeError, TypeError, TimeoutError])
def test_paddle_service_failure_preserves_structured_results_without_retry(original_pdf, converted_table, monkeypatch, profile, failure):
    use_external_conversion(monkeypatch, original_pdf, ConversionArtifact(success=True, xlsx_path=str(converted_table)))
    calls = []

    def unavailable(audit, fields, witness, **kwargs):
        calls.append(kwargs["operation"])
        raise failure("private provider detail must not appear in diagnostics")

    monkeypatch.setattr("audit.checkers.v4.visual_facts.read_original_pdf_visual_facts", unavailable)
    results = run_audit_engine(AuditInput(audit_id="psw-paddle-failed", file_url=str(original_pdf)), psw_rules(profile, count=5))
    assert results[1].status == "PASS"
    assert [results[index].status for index in (2, 4)] == ["ERR", "ERR"]
    assert all(results[index].evidence.diagnostics[0].code == "psw.visual.runtime_error" for index in (2, 4))
    assert len(calls) == 2
    assert all("private provider detail" not in result.suggestions for result in results)
    assert not converted_table.exists()


def test_unavailable_facts_are_unable_without_fallback(original_pdf, monkeypatch, profile):
    from audit.checkers.v4.visual_facts import VisualFactsUnavailable

    use_external_conversion(monkeypatch, original_pdf, ConversionArtifact(success=False))

    def unsupported(*args, **kwargs):
        raise VisualFactsUnavailable("unsupported visual semantics")

    monkeypatch.setattr("audit.checkers.v4.visual_facts.read_original_pdf_visual_facts", unsupported)
    results = run_audit_engine(AuditInput(audit_id="psw-native-unavailable", file_url=str(original_pdf)), psw_rules(profile, count=5))
    assert [results[index].status for index in (2, 4)] == ["UNABLE", "UNABLE"]


def write_disposition_pdf(path, options, *, copies=1):
    """Create real native characters with independent PDF source geometry."""
    with fitz.open() as document:
        for _ in range(copies):
            page = document.new_page(width=600, height=800)
            page.insert_text((25, 50), "零件提交保证书", fontname="china-s", fontsize=11)
            for x, text in zip((25, 160, 290, 450), ("认可情况：", *options)):
                page.insert_text((x, 80), text, fontname="china-s", fontsize=11)
        document.save(path)
    return path


def disposition_page_response(*, complete=True, approved="□批准"):
    return {
        "disposition_visible": True,
        "approved_checked": approved == "☑批准" if complete else None,
        "conditional_checked": False if complete else None,
        "rejected_checked": False if complete else None,
    }


def psw_checker(monkeypatch, path, profile):
    # Use the public PSW constructor and check entrypoint. The external
    # converter is isolated so this verifies original-page fact selection.
    use_external_conversion(monkeypatch, path, ConversionArtifact(success=False, diagnostics={"error_code": "paddle_table_unconfirmed"}))
    agent_type = V2PswPDFAgent if profile == "v2" else V3PswPDFAgent
    return agent_type(AuditInput(audit_id="frozen-native-psw", file_url=str(path)), psw_rules(profile))


def ocr_response_boundary(monkeypatch, response):
    """Controlled OCR Responses boundary; Paddle is not sent before checkboxes."""
    attempts = []
    for name, value in (("OCR_MODEL_NAME", "controlled-ocr"), ("OCR_OPENAI_API_KEY", "controlled"), ("OCR_OPENAI_BASE_URL", "https://ocr.invalid/v1")):
        monkeypatch.setenv(name, value)

    async def analyze(images, prompt, *, response_schema, **kwargs):
        attempts.append(response_schema)
        if isinstance(response, Exception):
            raise response
        mapping = response
        result = {}
        for field, schema in response_schema["schema"]["properties"].items():
            if "items" not in schema:
                result[field] = mapping.get(field)
                continue
            values = []
            for scope in schema["items"]["properties"]["scope"]["enum"]:
                # Image indices are explicit captions in the transport prompt.
                indices = [int(line.split(":", 1)[0].split()[1]) for line in prompt.splitlines() if line.startswith("图 ") and f"'scope': '{scope}'" in line]
                values.append(
                    {
                        "scope": scope,
                        "value": mapping.get(field),
                        "sources": [{"image_index": indices[0], "bbox": [0, 0, 1, 1]}],
                        "covered_image_indices": indices,
                        "coverage": "complete",
                        "excerpt": "受控原件事实",
                    }
                )
            result[field] = values
        return result

    monkeypatch.setattr("documents.ocr.client.call_llm_async", analyze)
    return attempts


def test_public_rule_eight_native_empty_checkboxes_need_no_paddle(tmp_path, monkeypatch, profile):
    path = write_disposition_pdf(tmp_path / "native-controls.pdf", ("□批准", "□条件批准", "□拒绝"))
    with fitz.open(path) as document:
        # The case depends on actual native PDF characters, not a reader stub.
        assert all(text in document[0].get_text() for text in ("□批准", "□条件批准", "□拒绝"))
    attempts = ocr_response_boundary(monkeypatch, AssertionError("Native controls must avoid Paddle"))
    with bind_runtime(None):
        checker = psw_checker(monkeypatch, path, profile)
        try:
            result = checker.check(7)
        finally:
            checker.close()
    assert result.status == "NG"
    assert "需且仅需勾选一个选项" in result.suggestions
    assert result.evidence.used == "structure"
    assert attempts == []
    assert all(source.kind == "structure" for source in result.evidence.sources)


@pytest.mark.parametrize(("complete", "expected"), [(True, "NG"), (False, "UNABLE")])
def test_public_rule_eight_uses_ocr_directly_for_incomplete_native_controls(tmp_path, monkeypatch, profile, complete, expected):
    path = write_disposition_pdf(tmp_path / "native-labels.pdf", ("批准", "条件批准", "拒绝"))
    attempts = ocr_response_boundary(monkeypatch, disposition_page_response(complete=complete))
    with bind_runtime(None):
        checker = psw_checker(monkeypatch, path, profile)
        try:
            result = checker.check(7)
        finally:
            checker.close()
    assert result.status == expected
    assert len(attempts) == 1
    assert result.evidence.used == ("both" if complete else "structure")


def test_public_rule_eight_retains_known_native_state_over_conflicting_ocr(tmp_path, monkeypatch, profile):
    path = write_disposition_pdf(tmp_path / "partial-native-controls.pdf", ("□批准", "条件批准", "拒绝"))
    attempts = ocr_response_boundary(monkeypatch, disposition_page_response(approved="☑批准"))
    with bind_runtime(None):
        checker = psw_checker(monkeypatch, path, profile)
        try:
            result = checker.check(7)
        finally:
            checker.close()
    # Native approved=False remains authoritative; Paddle supplies only the
    # two previously unknown states, so the empty disposition is still NG.
    assert result.status == "NG"
    assert len(attempts) == 1


@pytest.mark.parametrize("failed", [False, True], ids=["successful-page", "failed-page"])
def test_public_rules_reuse_each_located_fact_group_success_or_failure(tmp_path, monkeypatch, profile, failed):
    path = write_disposition_pdf(tmp_path / "shared-original.pdf", ("批准", "条件批准", "拒绝"))
    attempts = ocr_response_boundary(monkeypatch, TimeoutError("private endpoint") if failed else disposition_page_response())
    with bind_runtime(None):
        checker = psw_checker(monkeypatch, path, profile)
        try:
            results = [checker.check(index) for index in (2, 4, 7)]
        finally:
            checker.close()
    groups = [tuple(sorted(attempt["schema"]["properties"])) for attempt in attempts]
    assert len(groups) == len(set(groups))
    assert len(groups) == (2 if failed else 3)
    assert [result.status for result in results] == (["ERR"] * 3 if failed else ["UNABLE", "UNABLE", "NG"])
    assert all("private endpoint" not in result.suggestions for result in results)


def test_public_rule_eight_checks_both_original_disposition_pages(tmp_path, monkeypatch, profile):
    path = write_disposition_pdf(tmp_path / "two-dispositions.pdf", ("批准", "条件批准", "拒绝"), copies=2)
    attempts = ocr_response_boundary(monkeypatch, disposition_page_response())
    with bind_runtime(None):
        checker = psw_checker(monkeypatch, path, profile)
        try:
            result = checker.check(7)
        finally:
            checker.close()
    assert result.status == "NG"
    assert len(attempts) == 2
    assert all(attempt["name"] == "located_field_facts" for attempt in attempts)


def test_public_partial_native_values_survive_ocr_on_another_business_page(tmp_path, monkeypatch, profile):
    path = write_disposition_pdf(tmp_path / "native-plus-unknown.pdf", ("□批准", "条件批准", "拒绝"))
    with fitz.open(path) as document:
        page = document.new_page(width=600, height=800)
        for x, text in zip((25, 160, 290, 450), ("认可情况：", "批准", "条件批准", "拒绝")):
            page.insert_text((x, 80), text, fontname="china-s", fontsize=11)
        document.saveIncr()
    attempts = ocr_response_boundary(monkeypatch, disposition_page_response(approved="☑批准"))
    with bind_runtime(None):
        checker = psw_checker(monkeypatch, path, profile)
        try:
            result = checker.check(7)
        finally:
            checker.close()
    assert result.status == "UNABLE"
    assert len(attempts) == 2


@pytest.mark.parametrize("department", [False, True], ids=["company-no-authorization-read", "department-reads-authorization"])
def test_psw_requests_only_facts_needed_by_the_active_branches(tmp_path, monkeypatch, profile, department):
    path = write_disposition_pdf(tmp_path / "staged-psw.pdf", ("批准", "条件批准", "拒绝"))
    with fitz.open(path) as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((25, 50), "部门章授权声明", fontname="china-s", fontsize=11)
        document.saveIncr()
    payload = {
        **disposition_page_response(),
        "psw_stamp_present": True,
        "authorized_signature_present": True,
        "psw_stamp_ending_text": "专用章" if department else "公司",
        "authorization_company_stamp_present": False,
    }
    attempts = ocr_response_boundary(monkeypatch, payload)
    with bind_runtime(None):
        checker = psw_checker(monkeypatch, path, profile)
        try:
            results = [checker.check(index) for index in (2, 4, 7, 8, 9)]
        finally:
            checker.close()
    assert [result.status for result in results] == ["NG" if department else "PASS", "PASS", "NG", "NG", "NG"]
    requested = [set(schema["schema"]["properties"]) for schema in attempts]
    assert len(attempts) == (4 if department else 3)
    assert requested[0] == {"psw_stamp_present", "psw_stamp_ending_text"}
    assert {"authorized_signature_present"} in requested
    assert {"approved_checked", "conditional_checked", "rejected_checked"} in requested
    if department:
        assert requested[1] == {"authorization_company_stamp_present"}
    assert not any({"approval_date", "comments_present", "dimensional_checked", "submission_any_checked"}.intersection(fields) for fields in requested)


def test_conditional_date_and_comments_are_one_separate_request_only_when_applicable(tmp_path, monkeypatch, profile):
    from datetime import date

    path = write_disposition_pdf(tmp_path / "conditional-psw.pdf", ("批准", "条件批准", "拒绝"))
    payload = {
        **disposition_page_response(),
        "conditional_checked": True,
        "psw_stamp_present": True,
        "psw_stamp_ending_text": "公司",
        "authorized_signature_present": True,
        "approval_date": date.today().isoformat(),
        "comments_present": True,
    }
    attempts = ocr_response_boundary(monkeypatch, payload)
    with bind_runtime(None):
        checker = psw_checker(monkeypatch, path, profile)
        try:
            results = [checker.check(index) for index in (7, 8, 9)]
        finally:
            checker.close()
    assert [result.status for result in results] == ["PASS", "PASS", "PASS"]
    assert len(attempts) == 2
    assert set(attempts[1]["schema"]["properties"]) == {"approval_date", "comments_present"}


@pytest.mark.parametrize("extension", ["png", "jpeg"])
def test_public_psw_raster_uses_the_same_dedicated_ocr_fact_path(tmp_path, monkeypatch, profile, extension):
    from PIL import Image

    path = tmp_path / ("original." + extension)
    with Image.new("RGB", (400, 500), "white") as image:
        image.save(path)
    attempts = ocr_response_boundary(monkeypatch, disposition_page_response())
    with bind_runtime(None):
        checker = psw_checker(monkeypatch, path, profile)
        try:
            result = checker.check(7)
        finally:
            checker.close()
    assert result.status == "NG"
    assert result.evidence.source_format == extension
    assert result.evidence.prepared_format == "png"
    assert len(attempts) == 1


def test_public_multi_page_partial_success_and_protocol_failure_are_reused(tmp_path, monkeypatch, profile):
    from documents.ocr import client

    path = write_disposition_pdf(tmp_path / "page-local-failure.pdf", ("批准", "条件批准", "拒绝"), copies=2)
    ocr_response_boundary(monkeypatch, disposition_page_response())
    controlled = client.call_llm_async
    scopes = []

    async def per_page(images, prompt, *, response_schema, **kwargs):
        field = next(iter(response_schema["schema"]["properties"].values()))
        if "items" in field:
            (scope,) = field["items"]["properties"]["scope"]["enum"]
        else:
            import ast

            scope = ast.literal_eval(next(line.split(": ", 1)[1] for line in prompt.splitlines() if line.startswith("图 0:")))["scope"]
        scopes.append(scope)
        if scope == "pdf:2":
            raise ValueError("OCR visual coverage is invalid")
        return await controlled(images, prompt, response_schema=response_schema, **kwargs)

    monkeypatch.setattr(client, "call_llm_async", per_page)
    with bind_runtime(None):
        checker = psw_checker(monkeypatch, path, profile)
        try:
            results = [checker.check(index) for index in (2, 4, 7)]
        finally:
            checker.close()
    assert [result.status for result in results] == ["ERR", "ERR", "ERR"]
    assert scopes == ["pdf:1", "pdf:2", "pdf:1", "pdf:2"]
