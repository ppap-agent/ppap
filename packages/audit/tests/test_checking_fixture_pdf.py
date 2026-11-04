"""Checking-fixture PDF guards through the public checker boundary."""

import json

import fitz
import pytest
from audit.checkers.v2 import pdf_agent as v2_pdf
from audit.checkers.v2.checking_fixture_approval_agent import CheckingFixtureApprovalAgent as V2Agent
from audit.checkers.v3 import pdf_agent as v3_pdf
from audit.checkers.v3.checking_fixture_approval_agent import CheckingFixtureApprovalAgent as V3Agent
from audit.contracts import (
    AuditInput,
    RuleInput,
)
from audit.engine.checkers.client import AIClient
from audit.engine.runtime import bind_runtime
from documents.contracts import ConversionArtifact


@pytest.fixture(params=[("v2", V2Agent, v2_pdf), ("v3", V3Agent, v3_pdf)], ids=["v2", "v3"])
def checker(request, monkeypatch):
    profile, agent_class, pdf_module = request.param
    monkeypatch.setattr(pdf_module, "convert_pdf_to_excel_auto", lambda *args, **kwargs: ConversionArtifact(success=False))

    def check(path, index, model_response=None):
        model_calls = []

        def analyze(self, **kwargs):
            model_calls.append(kwargs)
            if model_response is None:
                raise AssertionError("Document guards must not invent a model verdict")
            if isinstance(model_response, Exception):
                raise model_response
            return model_response

        monkeypatch.setattr(AIClient, "image_analysis_completion", analyze)
        monkeypatch.setattr(AIClient, "structured_completion", analyze)
        rules = RuleInput(
            rule_id="confirmed-fixture-rules",
            rule_profile=profile,
            type="检具认可报告",
            rules=[{"id": str(index), "category": "local", "rule_to_check": "用户确认的要求", "type_of_rule": "table"} for index in range(1, 4)],
        )
        with bind_runtime(None):
            agent = agent_class(AuditInput(audit_id="fixture", file_url=str(path)), rules)
            try:
                result = agent.check(index)
            finally:
                agent.close()
        assert model_calls == []
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


@pytest.mark.parametrize(
    ("text", "missing"),
    [("项目检具清单", "检具认可报告"), ("检具认可报告", "所附文件"), ("所附文件", "检具认可报告")],
)
def test_readable_pdf_missing_logic_sections_is_a_business_failure(tmp_path, checker, text, missing):
    path = tmp_path / "fixture.pdf"
    write_pdf(path, text)

    result = checker(path, 2)

    assert result.status == "NG", result.suggestions
    assert missing in result.suggestions


@pytest.mark.parametrize(
    "response",
    [
        json.dumps({"status": "PASS", "suggestions": "附件与结论一致"}),
        json.dumps({"status": "NG", "suggestions": "否定状态缺少结论"}),
        RuntimeError("model unavailable"),
        "{}",
    ],
    ids=["pass", "ng", "service-error", "invalid-output"],
)
def test_complete_logic_sections_need_proven_graphic_facts_without_a_model_fallback(tmp_path, checker, response):
    path = tmp_path / "fixture.pdf"
    write_pdf(path, "检具认可报告\n所附文件\n不合格\n结论")

    result = checker(path, 2, response)

    assert result.status == "UNABLE"
    assert "Paddle" in result.suggestions


@pytest.mark.parametrize("content", [None, b"", b"not a PDF"], ids=["missing", "empty", "corrupt"])
@pytest.mark.parametrize("index", [0, 2], ids=["completeness", "logic"])
def test_unreadable_source_is_an_execution_error(tmp_path, checker, content, index):
    path = tmp_path / "fixture.pdf"
    if content is not None:
        path.write_bytes(content)

    result = checker(path, index)

    assert result.status == "ERR"
    assert "审核过程中出现错误" in result.suggestions
