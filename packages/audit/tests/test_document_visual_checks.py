"""Public fixed checks use OCR facts and keep incomplete observations unknown."""

import fitz
import pytest
from audit.checkers.v2.checking_fixture_approval_agent import CheckingFixtureApprovalAgent as V2Fixture
from audit.checkers.v2.dimension_measurement_pdf_agent import DimensionMeasurementPDFAgent as V2Dimension
from audit.checkers.v3.checking_fixture_approval_agent import CheckingFixtureApprovalAgent as V3Fixture
from audit.contracts import AuditInput, RuleInput
from audit.engine.runtime import bind_runtime
from documents.contracts import ConversionArtifact
from documents.ocr.observation import current_ocr_observation


@pytest.fixture
def original(tmp_path, monkeypatch):
    source = tmp_path / "original.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((40, 60), "检具清单 检具认可报告 所附文件 重要度", fontname="china-s")
        page.draw_rect(fitz.Rect(40, 80, 240, 180))
        document.save(source)
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://ocr.invalid/v1")
    monkeypatch.setenv("OCR_MODEL_NAME", "configured-ocr")
    for version in ("v2", "v3"):
        monkeypatch.setattr(f"audit.checkers.{version}.pdf_agent.convert_pdf_to_excel_auto", lambda *args, **kwargs: ConversionArtifact(success=False))
    monkeypatch.setattr("documents.ai.run_json", lambda *args, **kwargs: pytest.fail("Fixed visual predicates must not use the text model"))
    return source


def receipt(request, values):
    if request["response_schema"]["name"] == "located_field_facts":
        return {field: values.get(field) for field in request["response_schema"]["schema"]["properties"]}
    result = {}
    for field, spec in request["response_schema"]["schema"]["properties"].items():
        result[field] = [
            {
                "scope": scope,
                "value": values.get(field),
                "sources": [{"image_index": 0, "bbox": [0.1, 0.1, 0.8, 0.8]}],
                "covered_image_indices": [0],
                "coverage": "complete",
                "excerpt": "原件中的受控测试观察",
            }
            for scope in spec["items"]["properties"]["scope"]["enum"]
        ]
    return result


def make_agent(cls, source, *, fixture):
    rules = RuleInput(
        rule_id="confirmed",
        type="检具认可报告" if fixture else "尺寸检验报告",
        rules=[{"id": str(index), "category": "fixed", "priority": "P0", "rule_to_check": "已确认条款", "type_of_rule": "image"} for index in range(1, 4)],
    )
    return cls(AuditInput(audit_id="original", file_url=str(source)), rules)


@pytest.mark.parametrize("cls", [V2Fixture, V3Fixture])
@pytest.mark.parametrize("filled,expected", [(True, "PASS"), (False, "NG"), (None, "UNABLE")])
def test_fixture_shares_one_original_observation_across_fixed_rules(original, monkeypatch, cls, filled, expected):
    calls = []
    values = {
        "fixture_list_present": True,
        "fixture_report_present": True,
        "fixture_numbered_cells_filled": filled,
        "fixture_attachments_present": True,
        "fixture_attachment_choices_readable": True,
        "fixture_negative_attachment_selected": True,
        "fixture_conclusion_present": False,
    }

    def read(**request):
        if current_ocr_observation() != 2:
            calls.append(request)
        return receipt(request, values)

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    with bind_runtime(None):
        agent = make_agent(cls, original, fixture=True)
        try:
            first, second = agent.check(0), agent.check(2)
        finally:
            agent.close()
    assert first.status == expected
    assert second.status == "NG"
    assert len(calls) == 1
    assert second.evidence.sources and second.evidence.used == "visual"


@pytest.mark.parametrize("cls", [V2Dimension])
@pytest.mark.parametrize("value,expected", [(True, "PASS"), (False, "NG"), (None, "UNABLE")])
def test_dimension_uses_the_same_ocr_fact_boundary(original, monkeypatch, cls, value, expected):
    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", lambda **request: receipt(request, {"importance_marks_present": value}))
    with bind_runtime(None):
        agent = make_agent(cls, original, fixture=False)
        try:
            result = agent.check(1)
        finally:
            agent.close()
    assert result.status == expected


@pytest.mark.parametrize("response", [RuntimeError("private-provider-error"), {"status": "PASS"}])
def test_visual_failure_is_not_a_business_verdict_and_is_not_retried(original, monkeypatch, response):
    calls = []

    def read(**request):
        if current_ocr_observation() != 2:
            calls.append(request)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    with bind_runtime(None):
        agent = make_agent(V3Fixture, original, fixture=True)
        try:
            results = [agent.check(0), agent.check(2)]
        finally:
            agent.close()
    assert [item.status for item in results] == ["ERR", "ERR"]
    assert len(calls) == 1
    assert all("private-provider" not in item.suggestions for item in results)
