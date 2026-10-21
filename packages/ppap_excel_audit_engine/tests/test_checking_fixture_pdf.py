"""Checking-fixture PDF guards through the public checker boundary."""

import fitz
import pytest
from ppap_core.contracts import AuditInput, ConversionArtifact, RuleInput
from ppap_excel_audit_engine.checkers.agents import pdf_agent as v2_pdf
from ppap_excel_audit_engine.checkers.agents.checking_fixture_approval_agent import CheckingFixtureApprovalAgent as V2Agent
from ppap_excel_audit_engine.checkers.agents_v3_overlay import pdf_agent as v3_pdf
from ppap_excel_audit_engine.checkers.agents_v3_overlay.checking_fixture_approval_agent import CheckingFixtureApprovalAgent as V3Agent
from ppap_excel_audit_engine.checkers.client import AIClient
from ppap_excel_audit_engine.runtime import bind_runtime


@pytest.fixture(params=[("v2", V2Agent, v2_pdf), ("v3", V3Agent, v3_pdf)], ids=["v2", "v3"])
def checker(request, monkeypatch):
    profile, agent_class, pdf_module = request.param
    model_calls = []
    monkeypatch.setattr(pdf_module, "convert_pdf_to_excel_auto", lambda *args, **kwargs: ConversionArtifact(success=False))

    def no_model_result(self, **kwargs):
        model_calls.append(kwargs)
        raise AssertionError("Document guards must not invent a model verdict")

    monkeypatch.setattr(AIClient, "image_analysis_completion", no_model_result)
    monkeypatch.setattr(AIClient, "structured_completion", no_model_result)

    def check(path, index):
        rules = RuleInput(
            rule_id="confirmed-fixture-rules",
            rule_profile=profile,
            type="检具认可报告",
            rules=[{"id": str(index), "category": "local", "rule_to_check": "用户确认的要求", "type_of_rule": "table"} for index in range(1, 4)],
        )
        with bind_runtime(None):
            agent = agent_class(AuditInput(audit_id="fixture", file_url=str(path)), rules)
            result = agent.check(index)
        assert not model_calls
        assert result is not None
        return result

    return check


def write_pdf(path, text):
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((40, 60), text, fontname="china-s", fontsize=14)
        document.save(path)


@pytest.mark.parametrize("text", ["待填写表格", "项目检具清单", "检具认可报告"])
def test_readable_pdf_missing_required_sections_is_ng(tmp_path, checker, text):
    path = tmp_path / "fixture.pdf"
    write_pdf(path, text)

    result = checker(path, 0)

    assert result.status == "NG"
    assert "缺少必要部分" in result.suggestions


@pytest.mark.parametrize("content", [None, b"", b"not a PDF"], ids=["missing", "empty", "corrupt"])
@pytest.mark.parametrize("index", [0, 2], ids=["completeness", "logic"])
def test_unreadable_source_is_an_execution_error(tmp_path, checker, content, index):
    path = tmp_path / "fixture.pdf"
    if content is not None:
        path.write_bytes(content)

    result = checker(path, index)

    assert result.status == "ERR"
    assert "审核过程中出现错误" in result.suggestions
