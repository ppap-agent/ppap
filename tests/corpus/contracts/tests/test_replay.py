"""Recorded answers replace model calls, and only a passing case keeps a new answer."""

import asyncio
import base64
import json
import struct

import pytest
from corpus_contracts.replay import LIVE, REPLAY, Recorder, install, mode, shown_to_model
from documents.ocr import client as ocr_client


def test_recorded_request_is_readable_and_keeps_only_the_image_size():
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 30, 20) + b"\x08\x02" + b"\x00" * 3000
    shown = shown_to_model({"text": "看印章", "image": "data:image/png;base64," + base64.b64encode(png).decode(), "file": "QUJD" * 2000})
    assert shown == {"text": "看印章", "image": "<png 30x20>", "file": "<binary>"}


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
        recorder = Recorder("case", tmp_path, tmp_path)
        install(patched, recorder)
        asyncio.run(ocr_client._request_response(_ocr_kwargs()))
        asyncio.run(ocr_client._request_response(_ocr_kwargs()))
        recorder.finish(keep=False)
        assert list(tmp_path.iterdir()) == []
        asyncio.run(ocr_client._request_response({**_ocr_kwargs(), "model": "other"}))
        recorder.finish(keep=True)
    assert len(calls) == 2
    (entry,) = json.loads((tmp_path / "case.json").read_text())
    assert entry["answer"] == '{"seal": false}'
    assert entry["request"]["input"] == [{"text": "shown"}]

    with pytest.MonkeyPatch.context() as patched:
        calls.clear()
        recorder = Recorder("case", tmp_path, tmp_path)
        install(patched, recorder)
        response = asyncio.run(ocr_client._request_response(_ocr_kwargs()))
        recorder.finish(keep=True)
    assert calls == []
    assert (response.status, response.output_text) == ("completed", '{"seal": false}')


def test_a_changed_prompt_or_crop_asks_again(tmp_path, monkeypatch):
    calls = _answering_model(monkeypatch, ("completed", "{}"))
    with pytest.MonkeyPatch.context() as patched:
        recorder = Recorder("case", tmp_path, tmp_path)
        install(patched, recorder)
        asyncio.run(ocr_client._request_response(_ocr_kwargs()))
        asyncio.run(ocr_client._request_response({**_ocr_kwargs(), "input": [{"text": "changed"}]}))
    assert len(calls) == 2


def test_incomplete_answer_is_never_recorded(tmp_path, monkeypatch):
    _answering_model(monkeypatch, ("incomplete", ""))
    with pytest.MonkeyPatch.context() as patched:
        recorder = Recorder("case", tmp_path, tmp_path)
        install(patched, recorder)
        asyncio.run(ocr_client._request_response(_ocr_kwargs()))
        recorder.finish(keep=True)
    assert list(tmp_path.iterdir()) == []


def test_new_answers_can_go_to_a_separate_output(tmp_path, monkeypatch):
    _answering_model(monkeypatch, ("completed", "{}"))
    recordings, output = tmp_path / "recordings", tmp_path / "out"
    recordings.mkdir()
    with pytest.MonkeyPatch.context() as patched:
        recorder = Recorder("case", recordings, output)
        install(patched, recorder)
        asyncio.run(ocr_client._request_response(_ocr_kwargs()))
        recorder.finish(keep=True)
    assert list(recordings.iterdir()) == []
    assert (output / "case.json").is_file()
