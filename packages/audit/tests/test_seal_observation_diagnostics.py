"""Generic PDF seal observations expose only bounded, source-linked fact diagnostics."""

import json
from unittest.mock import Mock

import pytest
from audit.engine.checkers.ocr_visual_facts import OcrImage, observe_ocr_images
from documents.ai import AIError
from documents.ocr.observation import current_ocr_observation


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("PPAP_MODEL_VISION_MODEL", "controlled")
    monkeypatch.setenv("PPAP_MODEL_VISION_API_KEY", "private-key")
    monkeypatch.setenv("PPAP_MODEL_VISION_BASE_URL", "https://private.invalid")


def frames():
    return [OcrImage(b"private-image", {"scope": "private-scope", "page": 4, "source_sha256": "a" * 64, "context": True, "located_region": True}, 100, 100)]


def diagnostic(caplog):
    (record,) = [r for r in caplog.records if r.getMessage().startswith("OCR seal observation: ")]
    return json.loads(record.getMessage().partition(": ")[2])


@pytest.mark.parametrize("first", [True, False, None])
@pytest.mark.parametrize("second", [True, False, None])
def test_generic_seal_diagnostic_preserves_peer_observations_and_unknown(monkeypatch, caplog, first, second):
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        return {"seal": second if (current_ocr_observation() == 2) else first}

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    cache, witness = {}, Mock()
    expected = first if first == second else None
    for _ in range(2):
        assert observe_ocr_images(frames(), {"seal": "private-description"}, witness, context="private-context", any_fields=frozenset({"seal"}), request_cache=cache) == {"seal": expected}
    assert len(calls) == 2
    if expected is None:
        witness.assert_not_called()
    detail = diagnostic(caplog)
    assert detail["backend"] == "ocr_llm" and detail["pages"] == [4] and detail["source_sha256"] == ["a" * 64]
    assert detail["observations"] == [{"seal": first}, {"seal": second}]
    assert detail["equal"] == {"seal": first == second}
    assert detail["phase"] == "completed"
    assert len(detail["inputs"]) == 2 and detail["inputs"][0] == detail["inputs"][1]
    assert len(detail["inputs"][0]["sha256"]) == 64
    assert detail["inputs"][0]["image_sizes"] == [[100, 100]]
    assert all(private not in caplog.text for private in ("private-key", "private.invalid", "private-image", "private-scope", "private-description", "private-context"))


@pytest.mark.parametrize("failed_read", [1, 2])
def test_generic_seal_failure_records_the_stage_without_provider_text_or_retry(monkeypatch, caplog, failed_read):
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        if len(calls) == failed_read:
            raise AIError("invalid_output")
        return {"seal": True}

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    cache = {}
    for _ in range(2):
        with pytest.raises(AIError):
            observe_ocr_images(frames(), {"seal": "actual seal"}, Mock(), request_cache=cache)
    assert len(calls) == failed_read
    detail = diagnostic(caplog)
    assert detail["phase"] == f"observation_{failed_read}_failed"
    assert detail["observations"] == ([] if failed_read == 1 else [{"seal": True}])
    assert detail["equal"] == {}
    assert "private-provider-body" not in caplog.text


def test_generic_seal_diagnostic_does_not_serialize_other_requested_fields(monkeypatch, caplog):
    read = Mock(return_value={"seal": True, "private-field-name": False})
    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    assert observe_ocr_images(frames(), {"seal": "actual seal", "private-field-name": "private-description"}, Mock()) == {"seal": True, "private-field-name": False}
    detail = diagnostic(caplog)
    assert detail["observations"] == [{"seal": True}, {"seal": True}]
    assert detail["equal"] == {"seal": True}
    assert "private-field-name" not in caplog.text and "private-description" not in caplog.text
