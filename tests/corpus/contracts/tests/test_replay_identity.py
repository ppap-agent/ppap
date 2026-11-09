"""Readable recordings must prove the complete request, including image content."""

import base64
import io
import json

import pytest
from corpus_contracts.replay import Recorder, shown_to_model
from PIL import Image


def request(color="white"):
    data = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(data, format="PNG")
    image = "data:image/png;base64," + base64.b64encode(data.getvalue()).decode()
    return {
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Read the seal."}, {"type": "input_image", "image_url": image}]}],
        "text": {"format": {}},
        "max_output_tokens": 4096,
        "observation": 1,
    }


def test_same_size_changed_pixels_never_reuse_the_old_answer(tmp_path):
    original, changed = request(), request("black")
    assert shown_to_model(original) == shown_to_model(changed)
    recorder = Recorder("pixels", tmp_path, tmp_path)
    recorder.add("ocr", original, '{"seal":true}')
    assert recorder.lookup("ocr", original) is not None
    assert recorder.lookup("ocr", changed) is None


def test_request_identity_survives_disk_without_storing_image_bytes(tmp_path):
    original = request()
    recorder = Recorder("pixels", tmp_path, tmp_path)
    recorder.add("ocr", original, '{"seal":true}')
    recorder.finish(keep=True)
    saved = (tmp_path / "pixels.json").read_text()
    (entry,) = json.loads(saved)
    assert len(entry["request_sha256"]) == 64
    assert "data:image" not in saved
    replay = Recorder("pixels", tmp_path, tmp_path, offline=True)
    assert replay.lookup("ocr", original)["answer"] == '{"seal":true}'
    assert replay.lookup("ocr", request("black")) is None


def test_size_only_recording_cannot_certify_current_pixels_and_is_not_rewritten(tmp_path):
    original = request()
    path = tmp_path / "old.json"
    contents = json.dumps([{"kind": "ocr", "request": shown_to_model(original), "answer": '{"seal":true}'}])
    path.write_text(contents)
    recorder = Recorder("old", tmp_path, tmp_path, offline=True)
    assert recorder.lookup("ocr", original) is None
    recorder.finish(keep=False)
    assert path.read_text() == contents


@pytest.mark.parametrize("change", ["prompt", "schema", "budget", "options", "observation"])
def test_identity_covers_request_parameters(tmp_path, change):
    original = request()
    changed = request()
    if change == "prompt":
        changed["input"][0]["content"][0]["text"] = "Different criterion."
    elif change == "schema":
        changed["text"] = {"format": {"strict": True}}
    elif change == "budget":
        changed["max_output_tokens"] = 2048
    elif change == "options":
        changed["extra_body"] = {"reasoning": {"effort": "none"}}
    else:
        changed["observation"] = 2
    recorder = Recorder("parameters", tmp_path, tmp_path)
    recorder.add("ocr", original, '{"seal":true}')
    assert recorder.lookup("ocr", changed) is None
