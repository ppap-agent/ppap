"""Visual fact transport contracts; mocked model replies do not certify accuracy."""

import json
from types import SimpleNamespace

import fitz
import pytest
from audit.checkers.v4.li_auto import PDF_AGENTS
from audit.checkers.v4.visual_facts import VisualFactsUnavailable, read_original_pdf_visual_facts
from audit.contracts import AuditInput, RuleInput
from audit.engine.checkers.client import AIClient
from audit.engine.runtime import bind_runtime
from audit.presets import preset_rule_body
from documents.conversion import convert_pdf_to_excel_auto


def original_pdf(path, count=1):
    document = fitz.open()
    for _ in range(count):
        page = document.new_page(width=500, height=600)
        for index, (label, value) in enumerate([("报告编号", "R-001"), ("试验结论", "通过"), ("试验类型", "PV"), ("样品数量", "3")]):
            page.insert_text((60, 90 + 40 * index), label, fontname="china-s", fontsize=12)
            page.insert_text((260, 90 + 40 * index), value, fontname="china-s", fontsize=12)
        for y in range(60, 221, 40):
            page.draw_line((50, y), (450, y))
        for x in (50, 250, 450):
            page.draw_line((x, 60), (x, 220))
    document.save(path)
    document.close()


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({"seal": True, "before": True, "during": True, "after": True}, ("PASS", "PASS")),
        ({"seal": False, "before": True, "during": False, "after": True}, ("NG", "NG")),
        ({"seal": None, "before": True, "during": None, "after": True}, ("UNABLE", "UNABLE")),
    ],
)
def test_l03_uses_one_bounded_original_read_and_keeps_null_unknown(tmp_path, monkeypatch, reply, expected):
    path = tmp_path / "原件.pdf"
    original_pdf(path)
    calls = []

    def model(*args, **kwargs):
        calls.append(kwargs)
        assert len(kwargs["image_base64_list"]) == 1
        return json.dumps(reply)

    monkeypatch.setattr(AIClient, "image_analysis_completion", model)
    runtime = SimpleNamespace(convert_pdf_to_excel=lambda ref, **kwargs: convert_pdf_to_excel_auto(ref, allow_ocr=False, **kwargs))
    with bind_runtime(runtime):
        agent = PDF_AGENTS["L03"](AuditInput(audit_id="original-version", file_url=str(path)), RuleInput(rule_id="r", **preset_rule_body("理想", "L03")))
        try:
            results = [agent._execute_rule_check(4), agent._execute_rule_check(5)]
            assert tuple(result.status for result in results) == expected
            for result in results:
                visual = [source for source in result.evidence.sources if source.kind == "visual"]
                assert {json.loads(source.locator)["file_version_id"] for source in visual} == {"original-version"}
                assert {json.loads(source.locator)["page"] for source in visual} == {1}
        finally:
            agent.close()
    assert len(calls) == 1


@pytest.mark.parametrize("response", ['{"seal":"yes"}', '{"seal":1}', "{}", '{"seal":true,"extra":false}'])
def test_shared_reader_rejects_non_boolean_or_incomplete_model_facts(tmp_path, monkeypatch, response):
    path = tmp_path / "原件.pdf"
    original_pdf(path)
    monkeypatch.setattr(AIClient, "image_analysis_completion", lambda *args, **kwargs: response)
    witnessed = []
    with pytest.raises(VisualFactsUnavailable):
        read_original_pdf_visual_facts(AuditInput(audit_id="v", file_url=str(path)), {"seal": "实际印章"}, lambda *args, **kwargs: witnessed.append(args))
    assert witnessed[0][0] == {"kind": "pdf_page", "page": 1}


def test_shared_reader_checks_all_pages_and_does_not_guess_after_page_limit(tmp_path, monkeypatch):
    path = tmp_path / "长原件.pdf"
    original_pdf(path, count=11)
    monkeypatch.setattr(AIClient, "image_analysis_completion", lambda *args, **kwargs: pytest.fail("Out-of-budget source must not call the model"))
    with pytest.raises(VisualFactsUnavailable):
        read_original_pdf_visual_facts(AuditInput(audit_id="v", file_url=str(path)), {"seal": "实际印章"}, lambda *args, **kwargs: None)


def test_malformed_model_response_is_a_cached_technical_failure(tmp_path, monkeypatch):
    path = tmp_path / "原件.pdf"
    original_pdf(path)
    calls = []

    def malformed(*args, **kwargs):
        calls.append(kwargs)
        return "not json"

    monkeypatch.setattr(AIClient, "image_analysis_completion", malformed)
    runtime = SimpleNamespace(convert_pdf_to_excel=lambda ref, **kwargs: convert_pdf_to_excel_auto(ref, allow_ocr=False, **kwargs))
    with bind_runtime(runtime):
        agent = PDF_AGENTS["L03"](AuditInput(audit_id="v", file_url=str(path)), RuleInput(rule_id="r", **preset_rule_body("理想", "L03")))
        try:
            assert agent._execute_rule_check(4).status == "ERR"
            assert agent._execute_rule_check(5).status == "ERR"
        finally:
            agent.close()
    assert len(calls) == 1
