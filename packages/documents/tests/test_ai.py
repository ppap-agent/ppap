"""The direct model layer: key and model from the current scope, bounded calls, codes only."""

import json

import httpx2 as httpx
import pytest
from documents.ai import AIError, run_json, usage_observer
from documents.model_access import ModelAccess, ModelUnavailable, model_access_scope
from pydantic import SecretStr


def reply(text: str, *, usage: bool = True) -> dict:
    body = {
        "id": "resp-controlled",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "controlled-model",
        "output": [{"type": "message", "id": "msg-controlled", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": text, "annotations": []}]}],
    }
    if usage:
        body["usage"] = {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}
    return body


@pytest.fixture
def wire(monkeypatch):
    sent: list[httpx.Request] = []
    answers: list[httpx.Response | Exception] = []

    async def send(client, request, **kwargs):
        sent.append(request)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        answer.request = request
        return answer

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return sent, answers


def access(purpose_model="text-model", **kwargs):
    return ModelAccess(SecretStr("scoped-key"), model=purpose_model, base_url="https://models.example.test/v1", **kwargs)


def test_text_and_vision_use_the_scoped_key_model_and_options(wire, monkeypatch):
    sent, answers = wire
    monkeypatch.setenv("LLM_API_KEY", "ambient-key")
    selected = {"text": access("text-model", reasoning_effort="low"), "vision": access("vision-model", extra_body_json='{"reasoning":{"effort":"none"}}')}
    answers += [httpx.Response(200, json=reply('{"capital":"北京"}')), httpx.Response(200, json=reply('{"number":"P-204"}'))]
    schema = {"type": "object", "properties": {"number": {"type": "string"}}, "required": ["number"], "additionalProperties": False, "title": "reading"}
    with model_access_scope(selected.__getitem__):
        text = run_json("probe", instructions="Answer in JSON.", prompt="中国的首都？", max_tokens=50)
        vision = run_json("probe", purpose="vision", prompt="Read.", images=[b"\x89PNG-controlled"], schema=schema, max_tokens=60)
    assert text.output == {"capital": "北京"} and vision.output == {"number": "P-204"}
    assert text.usage() == {"input_tokens": 11, "output_tokens": 7, "source": "provider"}
    first, second = (json.loads(request.content) for request in sent)
    assert all(request.headers["Authorization"] == "Bearer scoped-key" for request in sent)
    assert all(str(request.url) == "https://models.example.test/v1/responses" for request in sent)
    assert (first["model"], first["store"], first["max_output_tokens"], first["reasoning"]["effort"]) == ("text-model", False, 50, "low")
    assert second["model"] == "vision-model" and second["reasoning"] == {"effort": "none"}
    assert second["text"]["format"]["type"] == "json_schema"
    assert any(part.get("type") == "input_image" for item in second["input"] for part in item.get("content", []) if isinstance(part, dict))


def test_no_scope_key_means_no_call(wire):
    sent, _ = wire
    with model_access_scope(None), pytest.raises(ModelUnavailable):
        run_json("probe", prompt="x")
    assert sent == []


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (httpx.Response(500, json={"error": {"message": "private provider body scoped-key"}}), "provider_status"),
        (httpx.Response(307, headers={"Location": "https://elsewhere.example.test/v1/responses"}), "request_failed"),
        (httpx.ConnectError("private transport detail"), "request_failed"),
        (httpx.ReadTimeout("slow"), "timeout"),
    ],
)
def test_failures_are_codes_without_provider_text_retry_or_redirect(wire, answer, code):
    sent, answers = wire
    answers.append(answer)
    calls = []
    with model_access_scope(access()), usage_observer(lambda task, result, seconds: calls.append(result)):
        with pytest.raises(AIError) as failure:
            run_json("probe", prompt="x")
    assert failure.value.code == code and str(failure.value) == code
    assert len(sent) == 1 and calls == [None]


def test_one_bounded_correction_of_an_unparsable_answer(wire):
    sent, answers = wire
    answers += [httpx.Response(200, json=reply("not json")), httpx.Response(200, json=reply("still not json"))]
    with model_access_scope(access()), pytest.raises(AIError, match="invalid_output"):
        run_json("probe", prompt="x")
    assert len(sent) == 2


def test_missing_usage_is_unknown_not_zero(wire):
    _, answers = wire
    answers.append(httpx.Response(200, json=reply('{"ok":true}', usage=False)))
    seen = []
    with model_access_scope(access()), usage_observer(lambda task, result, seconds: seen.append((task, result))):
        result = run_json("probe", prompt="x")
    assert result.output == {"ok": True} and result.usage() is None
    assert seen == [("probe", result)]
