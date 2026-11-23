"""A production model call record (#360) is a replay entry: offline replay answers the same call from it."""

import asyncio
import json

import documents.ai
import pytest
from audit.engine.checkers import ocr_visual_facts
from corpus_contracts.replay import Recorder, install
from documents.ai import AIResult, run_json
from documents.model_access import ModelAccess, model_access_scope
from documents.model_calls import model_call_recorder
from pydantic import SecretStr

SCHEMA = {"name": "seal", "strict": True, "schema": {"type": "object", "properties": {"seal": {"type": ["boolean", "null"]}}, "required": ["seal"], "additionalProperties": False}}
# The corpus conftest replays every test; this one exercises the seams themselves.
_SEAMS = {(ocr_visual_facts, "read_visual_facts"): ocr_visual_facts.read_visual_facts, (documents.ai, "run_json_async"): documents.ai.run_json_async}


@pytest.fixture(autouse=True)
def real_seams(monkeypatch):
    for (owner, name), original in _SEAMS.items():
        monkeypatch.setattr(owner, name, original)


def _provider(monkeypatch, answers):
    """The provider below the direct layer, answering by task."""
    calls = []

    async def run(access, **kwargs):
        calls.append(kwargs)
        return AIResult(answers[kwargs["purpose"]], input_tokens=3, output_tokens=2, model=access.model)

    monkeypatch.setattr("documents.ai._run", run)
    return calls


class Kept:
    def __init__(self):
        self.records = []

    def write(self, record, images):
        self.records.append(record)

    def failed(self, record, code):
        raise AssertionError(code)


def _calls():
    text = run_json("rule_text_evidence", instructions="judge", prompt="原文片段", max_tokens=1200, timeout=20)
    seal = asyncio.run(ocr_visual_facts.read_visual_facts([b"synthetic-image"], "看印章", response_schema=SCHEMA))
    return text.output, seal


def test_a_live_call_record_answers_the_same_call_offline(tmp_path, monkeypatch):
    access = ModelAccess(SecretStr("live-key-never-recorded"), model="live-model", base_url="https://models.example.test/v1", reasoning_effort="none")
    calls = _provider(monkeypatch, {"text": {"relation": "supports"}, "vision": {"seal": True}})
    kept = Kept()
    with model_access_scope(access), model_call_recorder(kept):
        live = _calls()
    assert len(calls) == 2 and [record["kind"] for record in kept.records] == ["text", "ocr"]

    # The records exactly as stored, in a replay file of another scope.
    (tmp_path / "round.json").write_text(json.dumps(kept.records, ensure_ascii=False))
    calls.clear()
    recorder = Recorder("another-scope", tmp_path, tmp_path, offline=True)
    with pytest.MonkeyPatch.context() as patched, model_access_scope(access):
        install(patched, recorder)
        replayed = _calls()
    assert replayed == live
    assert calls == [] and recorder.misses == []
