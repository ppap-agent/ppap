"""Seal diagnostics distinguish unknown, disagreement and technical failure without source text."""

import json
from unittest.mock import Mock

import pytest
from audit.engine.checkers.ocr_visual_facts import OcrImage, observe_ocr_images
from documents.ocr.observation import current_ocr_observation


@pytest.mark.parametrize("confirmation,expected", [("专用章", "专用章"), (None, None), ("公司", None)])
def test_seal_reading_diagnostics_preserve_two_reads_and_the_original_decision(monkeypatch, caplog, confirmation, expected):
    calls = []
    monkeypatch.setenv("OCR_MODEL_NAME", "controlled")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "private-key")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://private.invalid")
    frames = [OcrImage(b"private-image", {"scope": "private-scope", "page": 2, "source_sha256": "a" * 64, "bbox": [0, 0, 100, 100], "context": True, "located_region": True}, 100, 100)]

    def read(**kwargs):
        calls.append(kwargs)
        value = confirmation if (current_ocr_observation() == 2) else "专用章"
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
    assert detail["observations"][0]["psw_stamp_ending_text"] == "other_text"
    assert detail["observations"][1]["psw_stamp_ending_text"] == ("null" if confirmation is None else "company" if confirmation == "公司" else "other_text")
    assert detail["equal"]["psw_stamp_ending_text"] is (expected is not None)
    assert all(private not in caplog.text for private in ("专用章", "公司", "private-key", "private.invalid", "private-scope", "private-description", "private-image", "private-context"))


def test_second_observation_failure_keeps_the_first_summary_and_does_not_retry(monkeypatch, caplog):
    from documents.ai import AIError

    monkeypatch.setenv("OCR_MODEL_NAME", "controlled")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://controlled.invalid")
    frames = [OcrImage(b"synthetic", {"scope": "pdf:2", "page": 2, "context": True, "located_region": True}, 100, 100)]
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        if current_ocr_observation() == 2:
            raise AIError("invalid_output")
        return {"psw_stamp_ending_text": "公司"}

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    cache = {}
    for _ in range(2):
        with pytest.raises(AIError):
            observe_ocr_images(frames, {"psw_stamp_ending_text": "末尾"}, Mock(), request_cache=cache)
    assert len(calls) == 2
    (record,) = [r for r in caplog.records if r.getMessage().startswith("PSW OCR observation: ")]
    detail = json.loads(record.getMessage().partition(": ")[2])
    assert detail["phase"] == "observation_2_failed"
    assert detail["observations"] == [{"psw_stamp_ending_text": "company"}]
    assert "private-provider-body" not in caplog.text


def test_unknown_observations_are_both_reported_without_inventing_a_known_fact(monkeypatch, caplog):
    monkeypatch.setenv("OCR_MODEL_NAME", "controlled")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://controlled.invalid")
    frames = [OcrImage(b"synthetic", {"scope": "pdf:2", "page": 2, "context": True, "located_region": True}, 100, 100)]
    read = Mock(return_value={"psw_stamp_ending_text": None})
    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    assert observe_ocr_images(frames, {"psw_stamp_ending_text": "末尾"}, Mock()) == {"psw_stamp_ending_text": None}
    assert read.call_count == 2
    (record,) = [r for r in caplog.records if r.getMessage().startswith("PSW OCR observation: ")]
    detail = json.loads(record.getMessage().partition(": ")[2])
    assert detail["observations"] == [{"psw_stamp_ending_text": "null"}, {"psw_stamp_ending_text": "null"}]
    assert detail["phase"] == "completed" and detail["equal"] == {"psw_stamp_ending_text": True}


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


@pytest.mark.parametrize("second_known", [False, True], ids=["shanghai-yada-observation-2-unknown", "same-pages-agreed-absence"])
def test_shanghai_yada_page5_unknown_cannot_be_replaced_by_page3_absence(monkeypatch, caplog, second_known):
    """CI #42510446162: page 3 agrees on absence; page 5's second read is unknown."""
    from audit.engine.checkers.psw_visual_evidence import evaluate_psw_observation

    monkeypatch.setenv("OCR_MODEL_NAME", "controlled")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://controlled.invalid")
    frames = [OcrImage(str(page).encode(), {"scope": f"pdf:{page}", "page": page, "source_sha256": "a" * 64, "context": True, "located_region": True}, 100, 100) for page in (3, 5)]
    fields = {"psw_stamp_present": "actual seal", "psw_stamp_ending_text": "seal ending"}
    calls = []

    def read(**kwargs):
        (image,) = kwargs["images"]
        page = int(image)
        second = current_ocr_observation() == 2
        calls.append((page, second))
        values = {"psw_stamp_present": False, "psw_stamp_ending_text": None if page == 3 else ""}
        if page == 5 and second and not second_known:
            values = dict.fromkeys(fields)
        return {field: values[field] for field in kwargs["response_schema"]["schema"]["properties"]}

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", read)
    cache, witness = {}, Mock()
    facts = observe_ocr_images(frames, fields, witness, request_cache=cache)
    assert observe_ocr_images(frames, fields, Mock(), request_cache=cache) == facts
    assert calls == [(3, False), (3, True), (5, False), (5, True)]
    result = evaluate_psw_observation(3, {**facts, "authorization_table_present": True, "authorization_company_stamp_present": False})
    assert (result.status, result.checks_performed) == (("NG", 1) if second_known else ("UNABLE", 0))
    assert facts["psw_stamp_present"] is (False if second_known else None)
    if not second_known:
        assert {call.args[0]["page"] for call in witness.call_args_list} == {3}
    details = [json.loads(record.getMessage().partition(": ")[2]) for record in caplog.records if record.getMessage().startswith("PSW OCR observation: ")]
    assert [detail["pages"] for detail in details] == [[3], [5]]
    assert details[1]["observations"] == [
        {"psw_stamp_present": False, "psw_stamp_ending_text": "empty"},
        {"psw_stamp_present": False if second_known else None, "psw_stamp_ending_text": "empty" if second_known else "null"},
    ]
    assert details[1]["equal"] == dict.fromkeys(fields, second_known)
