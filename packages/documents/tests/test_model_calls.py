"""Model call records (#360): one record per direct call, never a credential, and a lost record never changes the call."""

import json
import re

import httpx2 as httpx
import pytest
from documents.ai import AIError, run_json
from documents.model_access import ModelAccess, model_access_scope
from documents.model_calls import CALL_HEADER, FORMAT, image_sha256, model_call_recorder, model_call_scope, request_sha256, text_request
from pydantic import SecretStr

# Shaped like a real provider key; split so no source line carries one.
KEY = "sk-" + "live-0123456789abcdefABCDEF"
# Known credential shapes: provider keys, bearer headers, key fields, cloud and signed-address signatures.
CREDENTIAL = re.compile(r"sk-[A-Za-z0-9_-]{8,}|Bearer\s|(?i:authorization)|(?i:api[_-]?key)|AKIA[0-9A-Z]{12,}|X-Amz-(?:Signature|Credential)|[?&]Signature=")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (24).to_bytes(4, "big") + (12).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"


def reply(text: str) -> dict:
    return {
        "id": "resp-controlled",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "controlled-model",
        "output": [{"type": "message", "id": "msg-controlled", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
    }


@pytest.fixture
def wire(monkeypatch):
    sent: list[httpx.Request] = []
    answers: list[httpx.Response] = []

    async def send(client, request, **kwargs):
        sent.append(request)
        answer = answers.pop(0)
        answer.request = request
        return answer

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return sent, answers


class Kept:
    def __init__(self, error: Exception | None = None, failure: Exception | None = None):
        self.records: list[dict] = []
        self.images: dict[str, bytes] = {}
        self.failures: list[tuple[str, str]] = []
        self.error, self.failure = error, failure

    def write(self, record, images):
        if self.error is not None:
            raise self.error
        self.records.append(record)
        self.images.update(images)

    def failed(self, record, code):
        self.failures.append((record["call_id"], code))
        if self.failure is not None:
            raise self.failure


def scoped(purpose):
    return ModelAccess(SecretStr(KEY), model=f"{purpose}-model", base_url="https://models.example.test/v1", revision="r" * 64, reasoning_effort="low")


def test_every_call_leaves_one_record_and_none_holds_a_credential(wire):
    sent, answers = wire
    answers += [httpx.Response(200, json=reply('{"relation":"supports"}')), httpx.Response(200, json=reply('{"number":"P-204"}')), httpx.Response(500, json={"error": {"message": KEY}})]
    schema = {"type": "object", "properties": {"number": {"type": "string"}}, "required": ["number"], "additionalProperties": False, "title": "reading"}
    kept = Kept()
    with model_access_scope(scoped), model_call_recorder(kept), model_call_scope(target="file-version-1", clause="U08:2"):
        text = run_json("rule_text_evidence", instructions="judge", prompt="原文片段", max_tokens=50)
        vision = run_json("ocr_evidence", purpose="vision", prompt="读编号", images=[PNG], schema=schema, max_tokens=60)
        with pytest.raises(AIError, match="provider_status"):
            run_json("rule_text_evidence", prompt="再问一次")

    assert [record["code"] for record in kept.records] == ["ok", "ok", "provider_status"]
    first, second, third = kept.records
    # Each record is one replay entry: the call's own request, its digest and the answer, plus what answered it.
    assert first["request"] == text_request("rule_text_evidence", instructions="judge", prompt="原文片段", max_tokens=50)
    assert first["request_sha256"] == request_sha256(first["request"]) and json.loads(first["answer"]) == text.output
    assert (first["kind"], first["model"], first["format"], first["task"], first["purpose"]) == ("text", "text-model", FORMAT, "rule_text_evidence", "text")
    assert first["identity"] == {"host": "models.example.test", "model": "text-model", "reasoning_effort": "low", "revision": "r" * 64, "source": "platform"}
    assert first["usage"] == {"input_tokens": 11, "output_tokens": 7, "source": "provider"} and first["duration_ms"] >= 0
    assert first["scope"] == {"target": "file-version-1", "clause": "U08:2"}
    # Images are named by content digest and handed over apart from the record.
    assert second["images"] == [image_sha256(PNG)] and kept.images == {image_sha256(PNG): PNG}
    assert json.loads(second["answer"]) == vision.output == {"number": "P-204"}
    # A failed call is recorded with its code and no answer; an omitted parameter stays omitted.
    assert third["answer"] is None and third["usage"] is None
    assert third["request"] == {"task": "rule_text_evidence", "instructions": None, "prompt": "再问一次", "schema": None, "max_tokens": None}
    # The request names its call, so the gateway records it once.
    assert [request.headers[CALL_HEADER] for request in sent] == [record["call_id"] for record in kept.records]
    stored = json.dumps(kept.records, ensure_ascii=False)
    assert KEY not in stored and not CREDENTIAL.search(stored)


def test_without_a_recorder_nothing_is_recorded_or_named(wire):
    sent, answers = wire
    answers.append(httpx.Response(200, json=reply('{"ok":true}')))
    with model_access_scope(scoped):
        assert run_json("probe", prompt="x").output == {"ok": True}
    assert CALL_HEADER not in sent[0].headers


@pytest.mark.parametrize("failure", [None, RuntimeError("diagnostic store down")])
def test_a_lost_record_leaves_the_call_unchanged_and_is_reported(wire, failure):
    _, answers = wire
    answers += [httpx.Response(200, json=reply('{"relation":"supports"}')), httpx.Response(200, json=reply('{"relation":"supports"}'))]
    with model_access_scope(scoped):
        expected = run_json("rule_text_evidence", prompt="x", max_tokens=20)
        kept = Kept(error=OSError("object store unavailable"), failure=failure)
        with model_call_recorder(kept):
            result = run_json("rule_text_evidence", prompt="x", max_tokens=20)
    assert result == expected
    assert kept.records == [] and len(kept.failures) == 1
    assert kept.failures[0][1] == "model_call_record_failed:OSError"
