"""A failed image-model request is reported as a failed model request, never as an unreadable original."""

import pytest
from audit.checker_lib.uaes.excel import UaesExcelAgent
from audit.checker_lib.uaes.v1.reader import Reader
from audit.engine.rule_engine.context import GuardStop
from audit.engine.rule_engine.contracts import Decision
from audit.engine.runtime import bind_runtime
from documents.ai import AIError


class _Runtime:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    def read_ocr_visual_facts(self, **_request):
        raise self.failure


class _Excel:
    legacy = lambda self: object()  # noqa: E731
    _from_legacy = UaesExcelAgent._from_legacy
    transcribe = UaesExcelAgent.transcribe


class _Reader:
    transcribe = Reader.transcribe


@pytest.mark.parametrize("agent", [_Excel(), _Reader()], ids=["revision-0", "revision-3"])
@pytest.mark.parametrize("code", ["timeout", "invalid_output", "provider_status"])
def test_failed_model_answer_is_a_failed_model_request(agent, code):
    with bind_runtime(_Runtime(AIError(code))), pytest.raises(GuardStop) as stop:  # type: ignore[arg-type]
        agent.transcribe(b"png", "prompt", {"type": "object"}, "尺寸图片")
    assert stop.value.decision == Decision.ERR
    assert "模型请求失败" in stop.value.message


@pytest.mark.parametrize("agent", [_Excel(), _Reader()], ids=["revision-0", "revision-3"])
def test_other_read_failure_stays_an_unreadable_original(agent):
    with bind_runtime(_Runtime(ValueError("broken image"))), pytest.raises(GuardStop) as stop:  # type: ignore[arg-type]
        agent.transcribe(b"png", "prompt", {"type": "object"}, "尺寸图片")
    assert stop.value.decision == Decision.ERR
    assert "原件读取失败" in stop.value.message


def test_warrant_r4_recovers_the_embedded_picture_observation(monkeypatch) -> None:
    from audit.checker_lib.uaes.v1.warrant import PICTURE_PROMPT, PICTURE_SCHEMA
    from audit.checker_lib.uaes.v1.warrant_r4 import WarrantR4

    prompts: list[str] = []

    def provider(*, images, prompt, response_schema):
        assert images == [b"embedded picture"]
        assert response_schema["schema"]["properties"]["kind"]["type"] == "string"
        prompts.append(prompt)
        if len(prompts) == 1:
            raise AIError("invalid_output")
        return {"kind": "imprint", "detail": "圆形盖章印迹"}

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", provider)
    warrant = WarrantR4.__new__(WarrantR4)
    result = warrant.transcribe(b"embedded picture", PICTURE_PROMPT, PICTURE_SCHEMA, "嵌入图片")

    assert result == {"kind": "imprint", "detail": "圆形盖章印迹"}
    assert len(prompts) == 2 and "仅输出 JSON 对象" in prompts[1]
