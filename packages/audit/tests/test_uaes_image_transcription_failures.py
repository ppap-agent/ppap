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
