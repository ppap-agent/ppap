"""Recorded answers replace model calls, and only a passing case keeps a new answer."""

import asyncio
import json

import pytest
from corpus_contracts.replay import LIVE, REPLAY, Recorder, install, mode, request_key
from documents.ocr import client as ocr_client


def test_key_ignores_nothing_shown_to_the_model_and_is_order_stable():
    assert request_key("ocr", {"a": 1, "b": 2}) == request_key("ocr", {"b": 2, "a": 1})
    assert request_key("ocr", {"a": 1}) != request_key("ocr", {"a": 2})
    assert request_key("ocr", {"a": 1}) != request_key("text", {"a": 1})


def test_mode_defaults_to_replay_and_rejects_unknown(monkeypatch):
    monkeypatch.delenv("PPAP_CORPUS_MODEL", raising=False)
    assert mode() == REPLAY
    monkeypatch.setenv("PPAP_CORPUS_MODEL", LIVE)
    assert mode() == LIVE
    monkeypatch.setenv("PPAP_CORPUS_MODEL", "nope")
    with pytest.raises(ValueError):
        mode()


def _ocr_kwargs():
    return {"model": "any", "input": [{"text": "shown"}], "text": {"format": {}}, "max_output_tokens": 10}


def _answering_model(monkeypatch, answer):
    calls = []

    async def request(kwargs):
        calls.append(kwargs)
        return type("Response", (), {"status": answer[0], "output_text": answer[1], "usage": None})()

    monkeypatch.setattr(ocr_client, "_request_response", request)
    return calls


def test_new_answer_is_recorded_only_when_the_case_passes_then_replayed(tmp_path, monkeypatch):
    calls = _answering_model(monkeypatch, ("completed", '{"seal": false}'))
    with pytest.MonkeyPatch.context() as patched:
        recorder = Recorder(tmp_path, tmp_path)
        install(patched, recorder)
        asyncio.run(ocr_client._request_response(_ocr_kwargs()))
        asyncio.run(ocr_client._request_response(_ocr_kwargs()))
        recorder.finish(keep=False)
        assert list(tmp_path.iterdir()) == []
        asyncio.run(ocr_client._request_response({**_ocr_kwargs(), "model": "other"}))
        recorder.finish(keep=True)
    assert len(calls) == 2
    (path,) = tmp_path.iterdir()
    assert json.loads(path.read_text())["answer"] == '{"seal": false}'

    with pytest.MonkeyPatch.context() as patched:
        calls.clear()
        recorder = Recorder(tmp_path, tmp_path)
        install(patched, recorder)
        response = asyncio.run(ocr_client._request_response(_ocr_kwargs()))
        recorder.finish(keep=True)
    assert calls == []
    assert (response.status, response.output_text) == ("completed", '{"seal": false}')


def test_incomplete_answer_is_never_recorded(tmp_path, monkeypatch):
    _answering_model(monkeypatch, ("incomplete", ""))
    with pytest.MonkeyPatch.context() as patched:
        recorder = Recorder(tmp_path, tmp_path)
        install(patched, recorder)
        asyncio.run(ocr_client._request_response(_ocr_kwargs()))
        recorder.finish(keep=True)
    assert list(tmp_path.iterdir()) == []


def test_new_answers_can_go_to_a_separate_output(tmp_path, monkeypatch):
    _answering_model(monkeypatch, ("completed", "{}"))
    recordings, output = tmp_path / "recordings", tmp_path / "out"
    recordings.mkdir()
    with pytest.MonkeyPatch.context() as patched:
        recorder = Recorder(recordings, output)
        install(patched, recorder)
        asyncio.run(ocr_client._request_response(_ocr_kwargs()))
        recorder.finish(keep=True)
    assert list(recordings.iterdir()) == []
    assert len(list(output.iterdir())) == 1
