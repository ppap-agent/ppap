"""Seal diagnostics distinguish unknown, disagreement and technical failure without source text."""

import json
from unittest.mock import Mock

import pytest
from audit.engine.checkers.ocr_visual_facts import CONFIRMATION_PROMPT, OcrImage, observe_ocr_images


@pytest.mark.parametrize("confirmation,expected", [("专用章", "专用章"), (None, None), ("公司", None)])
def test_seal_reading_diagnostics_preserve_two_reads_and_the_original_decision(monkeypatch, caplog, confirmation, expected):
    calls = []
    monkeypatch.setenv("OCR_MODEL_NAME", "controlled")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "private-key")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://private.invalid")
    frames = [OcrImage(b"private-image", {"scope": "private-scope", "page": 2, "source_sha256": "a" * 64, "bbox": [0, 0, 100, 100], "context": True, "located_region": True}, 100, 100)]

    def read(**kwargs):
        calls.append(kwargs)
        value = confirmation if kwargs["prompt"].startswith(CONFIRMATION_PROMPT) else "专用章"
        return {"psw_stamp_present": True, "psw_stamp_ending_text": value}

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    cache, sources = {}, Mock()
    fields = {"psw_stamp_present": "private-description", "psw_stamp_ending_text": "private-description"}
    result = observe_ocr_images(frames, fields, sources, context="private-context", request_cache=cache)
    assert result == {"psw_stamp_present": True, "psw_stamp_ending_text": expected}
    assert observe_ocr_images(frames, fields, Mock(), request_cache=cache, context="private-context") == result
    assert len(calls) == 2
    if expected is None:
        assert all(args.args[0]["fact"] != "psw_stamp_ending_text" for args in sources.call_args_list)
    (record,) = [r for r in caplog.records if r.getMessage().startswith("PSW OCR observation: ")]
    detail = json.loads(record.getMessage().partition(": ")[2])
    assert detail["pages"] == [2] and detail["source_sha256"] == ["a" * 64]
    assert detail["first"]["psw_stamp_ending_text"] == "other_text"
    assert detail["confirmation"]["psw_stamp_ending_text"] == ("null" if confirmation is None else "company" if confirmation == "公司" else "other_text")
    assert detail["equal"]["psw_stamp_ending_text"] is (expected is not None)
    assert all(private not in caplog.text for private in ("专用章", "公司", "private-key", "private.invalid", "private-scope", "private-description", "private-image", "private-context"))


def test_confirmation_failure_keeps_the_first_read_summary_and_does_not_retry(monkeypatch, caplog):
    from documents.ocr.client import ResponsesEvidenceError

    monkeypatch.setenv("OCR_MODEL_NAME", "controlled")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://controlled.invalid")
    frames = [OcrImage(b"synthetic", {"scope": "pdf:2", "page": 2, "context": True, "located_region": True}, 100, 100)]
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        if kwargs["prompt"].startswith(CONFIRMATION_PROMPT):
            raise ResponsesEvidenceError("responses_incomplete", "private-provider-body")
        return {"psw_stamp_ending_text": "公司"}

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    cache = {}
    for _ in range(2):
        with pytest.raises(ResponsesEvidenceError):
            observe_ocr_images(frames, {"psw_stamp_ending_text": "末尾"}, Mock(), request_cache=cache)
    assert len(calls) == 2
    (record,) = [r for r in caplog.records if r.getMessage().startswith("PSW OCR observation: ")]
    detail = json.loads(record.getMessage().partition(": ")[2])
    assert detail["phase"] == "confirmation_failed"
    assert detail["first"]["psw_stamp_ending_text"] == "company"
    assert "private-provider-body" not in caplog.text


def test_unknown_first_read_is_reported_without_inventing_a_confirmation(monkeypatch, caplog):
    monkeypatch.setenv("OCR_MODEL_NAME", "controlled")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://controlled.invalid")
    frames = [OcrImage(b"synthetic", {"scope": "pdf:2", "page": 2, "context": True, "located_region": True}, 100, 100)]
    read = Mock(return_value={"psw_stamp_ending_text": None})
    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    assert observe_ocr_images(frames, {"psw_stamp_ending_text": "末尾"}, Mock()) == {"psw_stamp_ending_text": None}
    assert read.call_count == 1
    (record,) = [r for r in caplog.records if r.getMessage().startswith("PSW OCR observation: ")]
    detail = json.loads(record.getMessage().partition(": ")[2])
    assert detail["first"] == {"psw_stamp_ending_text": "null"}
    assert detail["phase"] == "confirmation_not_requested" and detail["confirmation"] == {} and detail["equal"] == {}


def test_seal_decision_reports_native_authorization_facts_without_changing_unknown(monkeypatch, caplog):
    from types import SimpleNamespace

    from audit.contracts import AuditInput
    from audit.engine.checkers.psw_pdf_evidence import PswPdfEvidenceMixin

    facts = {"psw_stamp_present": True, "psw_stamp_ending_text": None, "authorization_table_present": True, "authorization_company_stamp_present": False}
    monkeypatch.setattr("audit.checkers.v4.visual_facts.read_original_pdf_visual_facts", lambda *args, **kwargs: facts)
    host = SimpleNamespace(
        audit_file=AuditInput(audit_id="frozen", file_url="private-file.pdf", file_metadata={"sha256": "a" * 64}),
        get_rule_by_index=lambda index: True,
        rule=SimpleNamespace(rules=[{}, {}, {}]),
    )

    class Agent(PswPdfEvidenceMixin):
        def _host(self):
            return host

    result = Agent()._run_fact_rule(2, 3)
    assert result.status == "UNABLE" and result.evidence.diagnostics[0].code == "psw.paddle.fact_unconfirmed"
    (record,) = [r for r in caplog.records if r.getMessage().startswith("PSW seal decision: ")]
    detail = json.loads(record.getMessage().partition(": ")[2])
    assert detail["facts"]["authorization_table_present"] is True and detail["facts"]["authorization_company_stamp_present"] is False
    assert detail["source_sha256"] == "a" * 64 and detail["evidence_used"] == "none"
    assert "private-file" not in caplog.text
