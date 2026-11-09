"""PSW original-page results survive strict product evidence projection.

Conversion, object identity and observation receipts are controlled; actual
PDF bytes, shared PSW policy, source validation and product projection are real.
"""

import hashlib
import importlib
import json

import fitz
import pytest
from agent.mapping import _checker_finding
from audit.contracts import AuditInput, RuleInput
from audit.engine.checkers.psw_visual_evidence import PSW_RULE_FIELDS, evaluate_psw_observation
from audit.engine.runtime import bind_runtime
from documents.contracts import ConversionArtifact
from openpyxl import Workbook


@pytest.mark.parametrize("version", ["v2", "v3"])
@pytest.mark.parametrize(
    ("alteration", "expected"),
    [(None, "PASS"), ("file_version_id", "UNABLE"), ("source_sha256", "UNABLE"), ("page", "UNABLE"), ("bbox", "UNABLE"), ("coordinate_system", "UNABLE")],
)
def test_submission_visual_fallback_keeps_only_verified_original_locations(tmp_path, monkeypatch, version, alteration, expected):
    pdf = tmp_path / "original.pdf"
    with fitz.open() as document:
        page = document.new_page(width=240, height=320)
        page.insert_text((25, 45), "提交原因", fontname="china-s")
        page.draw_rect(fitz.Rect(25, 55, 35, 65), fill=(0, 0, 1))
        document.save(pdf)
    original = pdf.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    prepared = tmp_path / "converted.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "PSW主表"
    sheet["A1"] = "零件提交保证书"
    sheet["A20"] = "提交原因"
    sheet["A45"] = "认可情况"
    workbook.save(prepared)
    workbook.close()

    def convert(file_ref, **kwargs):
        assert file_ref.location == str(pdf)
        return ConversionArtifact(success=True, xlsx_path=str(prepared))

    monkeypatch.setattr(f"audit.checkers.{version}.pdf_agent.convert_pdf_to_excel_auto", convert)
    monkeypatch.setattr("audit.checkers.v4.paddle_evidence.observe_psw_shadow", lambda *args: None)
    calls = []

    def read(audit, fields, witness, *, operation, **kwargs):
        assert audit.audit_id == "frozen-pdf" and audit.file_url == str(pdf)
        assert operation == "psw_rule_7"
        calls.append(operation)
        locator = {
            "kind": "pdf_region",
            "page": 1,
            "bbox": [25.0, 55.0, 35.0, 65.0],
            "coordinate_system": "pdf_points_unrotated",
            "source_sha256": digest,
            "file_version_id": audit.audit_id,
            "scope": "pdf:1",
            "engine": "ocr_llm",
            "fact": "submission_any_checked",
            "coverage": "complete",
        }
        if alteration:
            locator[alteration] = {"file_version_id": "foreign-version", "source_sha256": "0" * 64, "page": 2, "bbox": [-1, 55, 35, 65], "coordinate_system": "image_pixels"}[alteration]
        witness(locator, "The located submission option is checked.", visual=True)
        return {"submission_any_checked": True, "submission_other_present": False}

    def observe(audit, rule_no, witness, _cache, _read_single):
        # This unit controls established facts at the current object-reader
        # boundary. The real policy and downstream provenance checks still run.
        assert rule_no == 7
        facts = read(audit, PSW_RULE_FIELDS[rule_no], witness, operation=f"psw_rule_{rule_no}")
        return facts, evaluate_psw_observation(rule_no, facts)

    monkeypatch.setattr("audit.engine.checkers.psw_scope.evaluate_psw_pdf_rule", observe)
    cls = importlib.import_module(f"audit.checkers.{version}.psw_pdf_agent").PswPDFAgent
    with bind_runtime(None):
        checker = cls(
            AuditInput(audit_id="frozen-pdf", file_url=str(pdf), file_metadata={"source_sha256": digest}),
            RuleInput(rule_id="rules", type="零件提交保证书", rules=[{"id": str(index)} for index in range(1, 11)]),
        )
        try:
            result = checker.check_source_rule(6)
        finally:
            checker.close()
    assert calls == ["psw_rule_7"]  # The incomplete converted table used the original.
    assert result.status == "PASS" and result.checks_performed == 1
    assert result.evidence.used == "visual"
    assert result.evidence.conversion_strategies == ("native_pdf", "ocr_llm")
    assert len(result.evidence.sources) == 1  # No invented converted-table-grid witness.
    status, _, incomplete, sources = _checker_finding(result, file_version_id="frozen-pdf", file_facts={"frozen-pdf": (pdf.name, "target")})
    assert status == expected
    if expected == "PASS":
        assert incomplete is False
        assert json.loads(result.evidence.sources[0].locator) == {"kind": "pdf_page", "file_version_id": "frozen-pdf", "page": 1, "region": [25.0, 55.0, 35.0, 65.0]}
        assert sources[0]["file_version_id"] == "frozen-pdf"
        assert sources[0]["locator"] == {"kind": "pdf_page", "page": 1, "region": [25.0, 55.0, 35.0, 65.0]}
    else:
        assert incomplete is True and sources == []
    assert pdf.read_bytes() == original
