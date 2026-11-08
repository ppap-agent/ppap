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


@pytest.mark.parametrize("kind", ["ocr", "text", "paddle"])
def test_offline_miss_never_calls_or_records_a_provider(kind, tmp_path, monkeypatch):
    from audit.engine.checkers.client import AIClient
    from corpus_contracts.replay import OfflineReplayMiss
    from documents.ocr.paddle import client as paddle_client

    calls = []

    def denied(*args, **kwargs):
        calls.append(kind)
        raise AssertionError("external provider must not run")

    async def denied_async(*args, **kwargs):
        return denied(*args, **kwargs)

    monkeypatch.setattr(ocr_client, "_request_response", denied_async)
    monkeypatch.setattr(AIClient, "response_text", denied)
    monkeypatch.setattr(paddle_client, "_post_page_sync", denied)
    recorder = Recorder("offline", tmp_path, tmp_path, offline=True)
    install(monkeypatch, recorder)
    with pytest.raises(OfflineReplayMiss, match="external model calls are disabled"):
        if kind == "ocr":
            asyncio.run(ocr_client._request_response(_ocr_kwargs()))
        elif kind == "text":
            AIClient().response_text([{"role": "user", "content": "shown"}])
        else:
            paddle_client._post_page_sync("https://provider.invalid", {"file": "shown"}, {}, 1)
    recorder.finish(keep=True)
    assert recorder.misses == [kind]
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_offline_recorded_observation_needs_no_provider(tmp_path, monkeypatch):
    calls = _answering_model(monkeypatch, ("completed", "must not be read"))
    recorder = Recorder("offline", tmp_path, tmp_path, offline=True)
    request = {name: _ocr_kwargs()[name] for name in ("input", "text", "max_output_tokens")}
    recorder.entries.append({"kind": "ocr", "request": request, "answer": '{"seal": false}', "model": "recorded"})
    install(monkeypatch, recorder)
    response = asyncio.run(ocr_client._request_response(_ocr_kwargs()))
    assert response.output_text == '{"seal": false}'
    assert response.model == "recorded"
    assert recorder.misses == []
    assert calls == []


def test_peer_visual_observations_record_and_replay_two_distinct_answers(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from audit.engine.checkers.ocr_visual_facts import OcrImage, observe_ocr_images

    monkeypatch.setenv("OCR_MODEL_NAME", "controlled")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://controlled.invalid")
    calls = []

    async def answer(kwargs):
        calls.append(kwargs)
        return type("Response", (), {"status": "completed", "output_text": json.dumps({"seal": len(calls) == 1}), "usage": None})()

    monkeypatch.setattr(ocr_client, "_request_response", answer)
    recorder = Recorder("peer-observations", tmp_path, tmp_path)
    install(monkeypatch, recorder)
    frames = [OcrImage(b"synthetic", {"scope": "pdf:1", "page": 1, "context": True, "located_region": True}, 100, 100)]
    for _ in range(2):
        witness = Mock()
        assert observe_ocr_images(frames, {"seal": "actual seal"}, witness) == {"seal": None}
        witness.assert_not_called()
    assert len(calls) == 2 and len(recorder.pending) == 2
    assert recorder.pending[0]["request"] != recorder.pending[1]["request"]
    assert [json.loads(entry["answer"]) for entry in recorder.pending] == [{"seal": True}, {"seal": False}]


def test_test_grant_preserves_the_paired_key_endpoint_and_model(monkeypatch):
    from corpus_contracts.replay import configured_model_access

    for name in ("OCR_OPENAI_API_KEY", "OCR_OPENAI_BASE_URL", "OCR_MODEL_NAME", "OPENAI_API_KEY", "OPENAI_BASE_URL", "MODEL_NAME", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert configured_model_access() is None
    monkeypatch.setenv("OPENAI_API_KEY", "text-test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://text.invalid/v1")
    monkeypatch.setenv("MODEL_NAME", "text-model")
    access = configured_model_access()
    assert access is not None
    assert (access.api_key.get_secret_value(), access.base_url, access.model) == ("text-test-key", "https://text.invalid/v1", "text-model")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "ocr-test-key")
    with pytest.raises(ValueError, match="Incomplete corpus test model configuration"):
        configured_model_access()
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://ocr.invalid/v1")
    monkeypatch.setenv("OCR_MODEL_NAME", "ocr-model")
    access = configured_model_access()
    assert access is not None
    assert (access.api_key.get_secret_value(), access.base_url, access.model) == ("ocr-test-key", "https://ocr.invalid/v1", "ocr-model")
    assert "ocr-test-key" not in repr(access)


def test_explicit_offline_flag_does_not_change_replay_or_live_mode(monkeypatch):
    from corpus_contracts.replay import offline

    monkeypatch.setenv("PPAP_CORPUS_MODEL", REPLAY)
    monkeypatch.setenv("PPAP_CORPUS_OFFLINE", "1")
    assert offline() is True
    assert mode() == REPLAY
    monkeypatch.setenv("PPAP_CORPUS_OFFLINE", "0")
    assert offline() is False
    monkeypatch.setenv("PPAP_CORPUS_OFFLINE", "unknown")
    with pytest.raises(ValueError, match="PPAP_CORPUS_OFFLINE"):
        offline()
