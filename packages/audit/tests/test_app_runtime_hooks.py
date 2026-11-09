"""The public app runtime consumes real converter and original-image artifacts."""

import gc

import fitz
import pytest
from audit.contracts import (
    AuditInput,
    RuleInput,
)
from audit.engine.app_runtime import run_audit_engine
from audit.engine.checkers.client import AIClient
from documents.contracts import ConversionArtifact
from documents.ocr.client import current_ocr_observation
from openpyxl import Workbook


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
@pytest.mark.parametrize("stamp_detected", [True, False])
def test_app_runtime_reads_psw_facts_directly_through_ocr_configuration(tmp_path, monkeypatch, profile, stamp_detected):
    source = tmp_path / "original.pdf"
    with fitz.open() as document:
        document.new_page(width=240, height=320).draw_rect(fitz.Rect(10, 10, 60, 60))
        document.save(source)
    original_bytes = source.read_bytes()
    calls = []
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://ocr.invalid/v1")
    monkeypatch.setenv("OCR_MODEL_NAME", "configured-ocr")

    def convert(*args, **kwargs):
        return ConversionArtifact(success=False)

    def read(**request):
        if current_ocr_observation() != 2:
            calls.append(request)
        output = {}
        for field, spec in request["response_schema"]["schema"]["properties"].items():
            if "items" not in spec:
                output[field] = True if field == "psw_stamp_present" and stamp_detected else None
                continue
            output[field] = [
                {
                    "scope": scope,
                    "value": True if field == "psw_stamp_present" and stamp_detected else None,
                    "sources": [{"image_index": 0, "bbox": [0.1, 0.1, 0.3, 0.3]}],
                    "covered_image_indices": [0],
                    "coverage": "complete",
                    "excerpt": "章区域",
                }
                for scope in spec["items"]["properties"]["scope"]["enum"]
            ]
        return output

    monkeypatch.setattr("audit.engine.app_runtime.convert_pdf_to_excel_auto", convert)
    monkeypatch.setattr("audit.checkers.v2.pdf_agent.convert_pdf_to_excel_auto", convert)
    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    monkeypatch.setattr(AIClient, "structured_completion", lambda *a, **k: pytest.fail("PSW images must not enter text LLM"))
    monkeypatch.setattr("documents.ocr.paddle.reader.read_pdf_page_evidence", lambda *a, **k: pytest.fail("PSW must not wait for Paddle first"))
    rules = RuleInput(
        rule_id="confirmed-psw-rules",
        rule_profile=profile,
        type="零件提交保证书",
        rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, 11)],
    )
    results = run_audit_engine(AuditInput(audit_id="original-psw", file_url=str(source)), rules)
    assert len(results) == 10
    assert results[2].status == results[4].status == results[7].status == "UNABLE"
    assert results[4].evidence.used == ("visual" if stamp_detected else "none")
    assert len(calls) == 4
    fact_sets = [set(call["response_schema"]["schema"]["properties"]) for call in calls]
    assert fact_sets[0] == {"psw_stamp_present", "psw_stamp_ending_text"}
    assert {"authorized_signature_present"} in fact_sets
    assert {"disposition_visible", "approved_checked", "conditional_checked", "rejected_checked"} in fact_sets
    assert len({tuple(sorted(fields)) for fields in fact_sets}) == len(fact_sets)
    assert {"submission_any_checked", "submission_other_present"} in fact_sets
    assert source.read_bytes() == original_bytes


def test_fixed_runtime_missing_paddle_replay_stays_unknown_and_does_not_fall_through_to_network(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from audit.checkers.v4.visual_facts import VisualFactsUnavailable, read_original_pdf_visual_facts
    from audit.engine.rule_engine import Decision, GuardStop
    from audit.engine.runtime import bind_runtime

    source = tmp_path / "frozen.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(source)
    calls, witnesses, cache = [], [], {}

    def fixed_reader(path, page_number):
        calls.append((path, page_number))
        raise GuardStop(Decision.UNABLE, "No replayable Paddle observation")

    monkeypatch.setattr("documents.ocr.paddle.reader.read_pdf_page_evidence", lambda *_a, **_kw: pytest.fail("Fixed runtime must not fall through to live Paddle"))
    audit = AuditInput(audit_id="frozen-version", file_url=str(source))
    with bind_runtime(SimpleNamespace(read_paddle_page=fixed_reader)):
        for _ in range(2):
            with pytest.raises(VisualFactsUnavailable, match="No replayable Paddle observation"):
                read_original_pdf_visual_facts(audit, {"seal": "Actual seal"}, lambda *a, **k: witnesses.append((a, k)), cache=cache)
    assert calls == [(str(source), 1)]
    assert witnesses == []
