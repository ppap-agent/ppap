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
def test_app_runtime_uses_single_page_paddle_evidence_without_model_verdicts(tmp_path, monkeypatch, profile, stamp_detected):
    from documents.ocr.paddle import PaddleStructureService

    source = tmp_path / "original.pdf"
    with fitz.open() as document:
        page = document.new_page(width=240, height=320)
        page.draw_rect(page.rect, fill=(0, 0, 1), color=(0, 0, 1))
        document.save(source)
    original_bytes = source.read_bytes()
    calls, model_calls = [], []

    def convert(file_ref, *, enable_cache=True, doc_type=None):
        assert file_ref.location == str(source)
        return ConversionArtifact(success=False)

    def read_page(self, file_path, page_number, **kwargs):
        calls.append((str(file_path), page_number))
        assert self.timeout == 20
        return {
            "dataInfo": {"pages": [{"width": 240, "height": 320}]},
            "layoutParsingResults": [
                {
                    "prunedResult": {
                        "layout_det_res": {"boxes": [{"label": "seal", "coordinate": [10, 10, 60, 60]}] if stamp_detected else []},
                        "seal_res_list": [{"rec_texts": ["测试部门章"], "bbox": [10, 10, 60, 60]}] if stamp_detected else [],
                        "parsing_res_list": [],
                        "table_res_list": [],
                    }
                }
            ],
        }

    monkeypatch.setenv("PADDLE_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("PADDLE_OCR_URL", "https://paddle.invalid/layout-parsing")
    monkeypatch.setattr("audit.engine.app_runtime.convert_pdf_to_excel_auto", convert)
    monkeypatch.setattr("audit.checkers.v2.pdf_agent.convert_pdf_to_excel_auto", convert)
    monkeypatch.setattr(PaddleStructureService, "analyze_page", read_page)
    monkeypatch.setattr(AIClient, "image_analysis_completion", lambda self, **kwargs: model_calls.append(kwargs))
    rules = RuleInput(
        rule_id="confirmed-psw-rules",
        rule_profile=profile,
        type="零件提交保证书",
        rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, 11)],
    )
    results = run_audit_engine(AuditInput(audit_id="original-psw", file_url=str(source)), rules)

    assert len(results) == 10
    # Seal presence alone proves neither complete company wording nor signature.
    assert results[2].status == results[4].status == results[7].status == "UNABLE"
    assert results[4].evidence.used == ("visual" if stamp_detected else "none")
    assert calls == [(str(source), 1)]
    assert model_calls == []
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

    monkeypatch.setenv("PADDLE_EVIDENCE_ENABLED", "true")
    monkeypatch.setattr("documents.ocr.paddle.reader.read_pdf_page_evidence", lambda *_a, **_kw: pytest.fail("Fixed runtime must not fall through to live Paddle"))
    audit = AuditInput(audit_id="frozen-version", file_url=str(source))
    with bind_runtime(SimpleNamespace(read_paddle_page=fixed_reader)):
        for _ in range(2):
            with pytest.raises(VisualFactsUnavailable, match="No replayable Paddle observation"):
                read_original_pdf_visual_facts(audit, {"seal": "Actual seal"}, lambda *a, **k: witnesses.append((a, k)), cache=cache)
    assert calls == [(str(source), 1)]
    assert witnesses == []
