"""Generic PDF seal observations expose only bounded, source-linked fact diagnostics."""

import json
from unittest.mock import Mock

import pytest
from audit.engine.checkers.ocr_visual_facts import CONFIRMATION_PROMPT, OcrImage, observe_ocr_images
from documents.ocr.client import ResponsesEvidenceError


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("OCR_MODEL_NAME", "controlled")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "private-key")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://private.invalid")


def frames():
    return [OcrImage(b"private-image", {"scope": "private-scope", "page": 4, "source_sha256": "a" * 64, "context": True, "located_region": True}, 100, 100)]


def diagnostic(caplog):
    (record,) = [r for r in caplog.records if r.getMessage().startswith("OCR seal observation: ")]
    return json.loads(record.getMessage().partition(": ")[2])


@pytest.mark.parametrize("first,confirmation", [(True, True), (False, False), (True, None), (False, True), (None, None)])
def test_generic_seal_diagnostic_preserves_independent_confirmation_and_unknown(monkeypatch, caplog, first, confirmation):
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        return {"seal": confirmation if kwargs["prompt"].startswith(CONFIRMATION_PROMPT) else first}

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    cache, witness = {}, Mock()
    expected = first if first == confirmation else None
    for _ in range(2):
        assert observe_ocr_images(frames(), {"seal": "private-description"}, witness, context="private-context", any_fields=frozenset({"seal"}), request_cache=cache) == {"seal": expected}
    assert len(calls) == (1 if first is None else 2)
    if expected is None:
        witness.assert_not_called()
    detail = diagnostic(caplog)
    assert detail["backend"] == "ocr_llm" and detail["pages"] == [4] and detail["source_sha256"] == ["a" * 64]
    assert detail["first"] == {"seal": first}
    assert detail["confirmation"] == ({} if first is None else {"seal": confirmation})
    assert detail["equal"] == ({} if first is None else {"seal": first == confirmation})
    assert detail["phase"] == ("confirmation_not_requested" if first is None else "completed")
    assert all(private not in caplog.text for private in ("private-key", "private.invalid", "private-image", "private-scope", "private-description", "private-context"))


@pytest.mark.parametrize("failed_read", ["first", "confirmation"])
def test_generic_seal_failure_records_the_stage_without_provider_text_or_retry(monkeypatch, caplog, failed_read):
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        phase = "confirmation" if kwargs["prompt"].startswith(CONFIRMATION_PROMPT) else "first"
        if phase == failed_read:
            raise ResponsesEvidenceError("responses_incomplete", "private-provider-body")
        return {"seal": True}

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    cache = {}
    for _ in range(2):
        with pytest.raises(ResponsesEvidenceError):
            observe_ocr_images(frames(), {"seal": "actual seal"}, Mock(), request_cache=cache)
    assert len(calls) == (1 if failed_read == "first" else 2)
    detail = diagnostic(caplog)
    assert detail["phase"] == failed_read + "_failed"
    assert detail["first"] == ({} if failed_read == "first" else {"seal": True})
    assert detail["confirmation"] == {} and detail["equal"] == {}
    assert "private-provider-body" not in caplog.text


def test_generic_seal_diagnostic_does_not_serialize_other_requested_fields(monkeypatch, caplog):
    read = Mock(return_value={"seal": True, "private-field-name": False})
    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    assert observe_ocr_images(frames(), {"seal": "actual seal", "private-field-name": "private-description"}, Mock()) == {"seal": True, "private-field-name": False}
    detail = diagnostic(caplog)
    assert detail["first"] == detail["confirmation"] == {"seal": True}
    assert detail["equal"] == {"seal": True}
    assert "private-field-name" not in caplog.text and "private-description" not in caplog.text
