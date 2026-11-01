"""The public app runtime consumes real converter and original-image artifacts."""

import base64
import gc
import json
from io import BytesIO

import fitz
import pytest
from audit.contracts import (
    AuditInput,
    RuleInput,
)
from audit.engine.app_runtime import run_audit_engine
from audit.engine.checkers.client import AIClient
from documents.contracts import ConversionArtifact
from openpyxl import Workbook
from PIL import Image


@pytest.mark.parametrize("profile", ["v2", "v3"])
@pytest.mark.parametrize(("weight", "expected"), [(3.1897, "PASS"), (3.189, "NG")], ids=["valid-weight", "invalid-weight"])
def test_app_runtime_pdf_conversion_uses_actual_values_and_releases_temporary_files_before_return(tmp_path, monkeypatch, profile, weight, expected):
    source = tmp_path / "suppliers.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), "Supplier list")
        document.save(source)
    original_bytes = source.read_bytes()
    converted = tmp_path / "converted.xlsx"
    workbook = Workbook()
    workbook.active.title = "材料产品及分供方清单"
    workbook.active.append(["零件号", "单件重量"])
    workbook.active.append(["PART-001", weight])
    workbook.save(converted)
    workbook.close()
    auxiliary = tmp_path / "converted.html"
    auxiliary.write_text("conversion diagnostic", encoding="utf-8")

    def convert(file_ref, *, enable_cache=True, doc_type=None):
        assert file_ref.location == str(source)
        assert enable_cache is True
        return ConversionArtifact(success=True, xlsx_path=str(converted), temp_files=[str(converted), str(auxiliary)])

    monkeypatch.setattr("audit.engine.app_runtime.convert_pdf_to_excel_auto", convert)
    monkeypatch.setattr("audit.checkers.v2.pdf_agent.convert_pdf_to_excel_auto", convert)
    rules = RuleInput(
        rule_id="confirmed-supplier-rules",
        rule_profile=profile,
        type="材料、产品及分供方清单",
        rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, 5)],
    )
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        results = run_audit_engine(AuditInput(audit_id="suppliers", file_url=str(source)), rules)

        assert len(results) == 4
        assert results[0].status == expected
        if expected == "NG":
            assert "单件重量必须是四位小数格式" in results[0].suggestions
        assert not converted.exists()
        assert not auxiliary.exists()
        assert source.read_bytes() == original_bytes
    finally:
        if gc_was_enabled:
            gc.enable()
        gc.collect()


@pytest.mark.parametrize("profile", ["v2", "v3"])
@pytest.mark.parametrize(("company_stamp", "expected"), [(True, "PASS"), (False, "NG")], ids=["company-stamp", "no-stamp"])
def test_app_runtime_renders_original_pdf_pixels_and_passes_facts_to_the_existing_rule(tmp_path, monkeypatch, profile, company_stamp, expected):
    source = tmp_path / "original.pdf"
    with fitz.open() as document:
        page = document.new_page(width=240, height=320)
        page.draw_rect(page.rect, fill=(0, 0, 1), color=(0, 0, 1))
        document.save(source)
    original_bytes = source.read_bytes()
    seen_images = []

    def convert(file_ref, *, enable_cache=True, doc_type=None):
        assert file_ref.location == str(source)
        return ConversionArtifact(success=False)

    def analyze(self, **kwargs):
        seen_images.extend(kwargs["image_base64_list"])
        return json.dumps(
            {
                "psw_stamp_present": company_stamp,
                "psw_stamp_ends_with_company": company_stamp,
                "authorization_declaration_present": False,
                "authorization_company_stamp_present": False,
            }
        )

    monkeypatch.setattr("audit.engine.app_runtime.convert_pdf_to_excel_auto", convert)
    monkeypatch.setattr("audit.checkers.v2.pdf_agent.convert_pdf_to_excel_auto", convert)
    monkeypatch.setattr(AIClient, "image_analysis_completion", analyze)
    rules = RuleInput(
        rule_id="confirmed-psw-rules",
        rule_profile=profile,
        type="零件提交保证书",
        rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, 11)],
    )

    results = run_audit_engine(AuditInput(audit_id="original-psw", file_url=str(source)), rules)

    assert len(results) == 10
    assert results[2].status == expected
    assert seen_images
    for encoded in seen_images:
        with Image.open(BytesIO(base64.b64decode(encoded.split(",", 1)[-1]))) as image:
            red, green, blue = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
            assert red < 10 and green < 10 and blue > 245
    assert source.read_bytes() == original_bytes
