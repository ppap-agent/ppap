"""Recorded answers replace model calls, and only a passing case keeps a new answer."""

import asyncio
import base64
import json
import struct

import pytest
from audit.engine.checkers import ocr_visual_facts
from corpus_contracts.replay import LIVE, REPLAY, OfflineReplayMiss, Recorder, install, mode, ocr_request, shown_to_model
from documents.ai import AIError, AIResult, run_json
from documents.ocr.observation import ocr_observation_scope


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


SCHEMA = {"name": "seal", "strict": True, "schema": {"type": "object", "properties": {"seal": {"type": ["boolean", "null"]}}, "required": ["seal"], "additionalProperties": False}}


import documents.ai  # noqa: E402

# The corpus conftest replays every test; these tests exercise the seams themselves.
_SEAMS = {(ocr_visual_facts, "read_visual_facts"): ocr_visual_facts.read_visual_facts, (documents.ai, "run_json_async"): documents.ai.run_json_async}


@pytest.fixture(autouse=True)
def vision(monkeypatch):
    for (owner, name), original in _SEAMS.items():
        monkeypatch.setattr(owner, name, original)
    monkeypatch.setenv("PPAP_MODEL_VISION_MODEL", "controlled")
    monkeypatch.setenv("PPAP_MODEL_VISION_API_KEY", "controlled")
    monkeypatch.setenv("PPAP_MODEL_VISION_BASE_URL", "https://controlled.invalid/v1")
    monkeypatch.setenv("PPAP_MODEL_VISION_EXTRA_BODY_JSON", "{}")
    monkeypatch.setenv("PPAP_MODEL_TEXT_MODEL", "controlled-text")
    monkeypatch.setenv("PPAP_MODEL_TEXT_API_KEY", "controlled")
    monkeypatch.setenv("PPAP_MODEL_TEXT_BASE_URL", "https://controlled.invalid/v1")


def _answering_model(monkeypatch, answer):
    """The provider below the direct layer; it records what reached it."""
    calls = []

    async def run(access, **kwargs):
        calls.append(kwargs)
        if isinstance(answer, Exception):
            raise answer
        return AIResult(answer(len(calls)) if callable(answer) else answer, model=access.model)

    monkeypatch.setattr("documents.ai._run", run)
    return calls


def _read(prompt="shown", image=b"synthetic-image"):
    return asyncio.run(ocr_visual_facts.read_visual_facts([image], prompt, response_schema=SCHEMA))


def test_new_answer_is_recorded_only_when_the_case_passes_then_replayed(tmp_path, monkeypatch):
    calls = _answering_model(monkeypatch, {"seal": False})
    with pytest.MonkeyPatch.context() as patched:
        recorder = Recorder("case", tmp_path, tmp_path)
        install(patched, recorder)
        _read()
        _read()
        recorder.finish(keep=False)
        assert list(tmp_path.iterdir()) == []
        _read()
        recorder.finish(keep=True)
    assert len(calls) == 2
    (entry,) = json.loads((tmp_path / "case.json").read_text())
    assert json.loads(entry["answer"]) == {"seal": False}
    assert entry["request"]["input"][0]["content"][0] == {"type": "input_text", "text": "shown"}

    with pytest.MonkeyPatch.context() as patched:
        calls.clear()
        recorder = Recorder("case", tmp_path, tmp_path)
        install(patched, recorder)
        answer = _read()
        recorder.finish(keep=True)
    assert calls == []
    assert answer == {"seal": False}


def test_a_changed_prompt_or_crop_asks_again(tmp_path, monkeypatch):
    calls = _answering_model(monkeypatch, {"seal": None})
    with pytest.MonkeyPatch.context() as patched:
        install(patched, Recorder("case", tmp_path, tmp_path))
        _read()
        _read(prompt="changed")
        _read(image=b"another-crop")
    assert len(calls) == 3


def test_a_failed_answer_is_never_recorded(tmp_path, monkeypatch):
    _answering_model(monkeypatch, AIError("invalid_output"))
    with pytest.MonkeyPatch.context() as patched:
        recorder = Recorder("case", tmp_path, tmp_path)
        install(patched, recorder)
        with pytest.raises(AIError):
            _read()
        recorder.finish(keep=True)
    assert list(tmp_path.iterdir()) == []


def test_new_answers_can_go_to_a_separate_output(tmp_path, monkeypatch):
    _answering_model(monkeypatch, {"seal": None})
    recordings, output = tmp_path / "recordings", tmp_path / "out"
    recordings.mkdir()
    with pytest.MonkeyPatch.context() as patched:
        recorder = Recorder("case", recordings, output)
        install(patched, recorder)
        _read()
        recorder.finish(keep=True)
    assert list(recordings.iterdir()) == []
    assert (output / "case.json").is_file()


def test_text_answers_are_recorded_and_replayed_through_the_direct_layer(tmp_path, monkeypatch):
    calls = _answering_model(monkeypatch, {"relation": "supports"})
    for expected_calls in (1, 1):
        with pytest.MonkeyPatch.context() as patched:
            recorder = Recorder("text", tmp_path, tmp_path)
            install(patched, recorder)
            assert run_json("rule_text_evidence", instructions="judge", prompt="shown", max_tokens=10).output == {"relation": "supports"}
            recorder.finish(keep=True)
        assert len(calls) == expected_calls
    (entry,) = json.loads((tmp_path / "text.json").read_text())
    assert entry["request"] == {"task": "rule_text_evidence", "instructions": "judge", "prompt": "shown", "schema": None, "max_tokens": 10}


@pytest.mark.parametrize("kind", ["ocr", "text"])
def test_offline_miss_never_calls_or_records_a_provider(kind, tmp_path, monkeypatch):
    calls = _answering_model(monkeypatch, AssertionError("external provider must not run"))
    recorder = Recorder("offline", tmp_path, tmp_path, offline=True)
    install(monkeypatch, recorder)
    with pytest.raises(OfflineReplayMiss, match="external model calls are disabled"):
        if kind == "ocr":
            _read()
        else:
            run_json("probe", prompt="shown")
    recorder.finish(keep=True)
    assert recorder.misses == [kind]
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_offline_recorded_observation_needs_no_provider(tmp_path, monkeypatch):
    calls = _answering_model(monkeypatch, AssertionError("must not run"))
    recorder = Recorder("offline", tmp_path, tmp_path, offline=True)
    recorder.entries.append({"kind": "ocr", "request": ocr_request([b"synthetic-image"], "shown", SCHEMA, 4096), "answer": '{"seal": false}', "model": "recorded"})
    install(monkeypatch, recorder)
    assert _read() == {"seal": False}
    assert recorder.misses == []
    assert calls == []


def test_peer_visual_observations_record_and_replay_two_distinct_answers(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from audit.engine.checkers.ocr_visual_facts import OcrImage, observe_ocr_images

    calls = _answering_model(monkeypatch, lambda number: {"seal": number == 1})
    recorder = Recorder("peer-observations", tmp_path, tmp_path)
    install(monkeypatch, recorder)
    frames = [OcrImage(b"synthetic", {"scope": "pdf:1", "page": 1, "context": True, "located_region": True}, 100, 100)]
    for _ in range(2):
        witness = Mock()
        assert observe_ocr_images(frames, {"seal": "actual seal"}, witness) == {"seal": None}
        witness.assert_not_called()
    assert len(calls) == 2 and len(recorder.pending) == 2
    assert calls[0] == calls[1]
    assert [entry["request"]["observation"] for entry in recorder.pending] == [1, 2]
    requests = [{key: value for key, value in entry["request"].items() if key != "observation"} for entry in recorder.pending]
    assert requests[0] == requests[1]
    assert [json.loads(entry["answer"]) for entry in recorder.pending] == [{"seal": True}, {"seal": False}]


def test_offline_second_observation_cannot_use_the_first_or_an_untagged_answer(tmp_path, monkeypatch):
    calls = _answering_model(monkeypatch, AssertionError("must not run"))
    recorder = Recorder("peer-offline", tmp_path, tmp_path, offline=True)
    request = ocr_request([b"synthetic-image"], "shown", SCHEMA, 4096)
    for identity in ({}, {"observation": 1}):
        recorder.entries.append({"kind": "ocr", "request": {**request, **identity}, "answer": '{"seal":true}'})
    install(monkeypatch, recorder)
    with ocr_observation_scope(2), pytest.raises(OfflineReplayMiss):
        _read()
    assert calls == [] and recorder.pending == []


def test_live_corpus_uses_one_platform_model_per_use(monkeypatch):
    from corpus_contracts.replay import configured_model_access

    for name in (
        "PPAP_MODEL_TEXT_API_KEY",
        "PPAP_MODEL_TEXT_BASE_URL",
        "PPAP_MODEL_TEXT_MODEL",
        "PPAP_MODEL_VISION_API_KEY",
        "PPAP_MODEL_VISION_BASE_URL",
        "PPAP_MODEL_VISION_MODEL",
        "PPAP_MODEL_VISION_EXTRA_BODY_JSON",
    ):
        monkeypatch.delenv(name, raising=False)
    assert configured_model_access() is None
    for name, value in {
        "PPAP_MODEL_TEXT_API_KEY": "text-test-key",
        "PPAP_MODEL_TEXT_BASE_URL": "https://text.invalid/v1",
        "PPAP_MODEL_TEXT_MODEL": "text-model",
        "PPAP_MODEL_VISION_API_KEY": "ocr-test-key",
        "PPAP_MODEL_VISION_BASE_URL": "https://ocr.invalid/v1",
        "PPAP_MODEL_VISION_MODEL": "ocr-model",
    }.items():
        monkeypatch.setenv(name, value)
    resolve = configured_model_access()
    assert resolve is not None
    text, vision, coding = resolve("text"), resolve("vision"), resolve("coding")
    assert (text.api_key.get_secret_value(), text.base_url, text.model) == ("text-test-key", "https://text.invalid/v1", "text-model")
    assert (vision.api_key.get_secret_value(), vision.base_url, vision.model) == ("ocr-test-key", "https://ocr.invalid/v1", "ocr-model")
    # As in production: vision calls default to no reasoning, coding falls back to the text model.
    assert vision.extra_body() == {"reasoning": {"effort": "none"}}
    assert (coding.base_url, coding.model) == ("https://text.invalid/v1", "text-model")
    assert "ocr-test-key" not in repr(vision)


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
