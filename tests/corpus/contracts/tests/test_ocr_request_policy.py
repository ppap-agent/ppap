"""Corpus OCR uses explicit dedicated parameters and never reuses another policy's answer."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from corpus_contracts.replay import Recorder, configured_model_access, install, replay_model_access
from documents.model_access import model_access_scope
from documents.ocr import client
from documents.ocr.env import ocr_extra_body
from pydantic import SecretStr


def test_only_a_complete_dedicated_ocr_grant_takes_the_explicit_policy(monkeypatch):
    for name, value in {
        "OCR_OPENAI_API_KEY": "controlled",
        "OCR_OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
        "OCR_MODEL_NAME": "deepseek/deepseek-v4.1-flash-1017",
        "OCR_OPENAI_EXTRA_BODY_JSON": '{"reasoning":{"effort":"none"}}',
        "OPENAI_API_KEY": "other",
        "OPENAI_BASE_URL": "https://other.invalid/v1",
        "MODEL_NAME": "another-model",
    }.items():
        monkeypatch.setenv(name, value)
    with model_access_scope(configured_model_access()):
        assert ocr_extra_body() == {"reasoning": {"effort": "none"}}
    for name in ("OCR_OPENAI_API_KEY", "OCR_OPENAI_BASE_URL", "OCR_MODEL_NAME"):
        monkeypatch.delenv(name)
    with model_access_scope(configured_model_access()):
        assert ocr_extra_body() == {}


def test_replay_separates_actual_ocr_options_from_default_policy(tmp_path, monkeypatch):
    calls = []

    async def request(kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status="completed", output_text='{"present":true}')

    monkeypatch.setattr(client, "_request_response", request)
    base = {"input": "synthetic", "text": {"format": {}}, "max_output_tokens": 4096}
    recording = [{"kind": "ocr", "request": base, "model": "recorded", "answer": '{"present":false}'}]
    (tmp_path / "case.json").write_text(json.dumps(recording))
    recorder = Recorder("case", tmp_path, tmp_path)
    install(monkeypatch, recorder)
    assert asyncio.run(client._request_response(base)).output_text == '{"present":false}'
    changed = {**base, "extra_body": {"reasoning": {"effort": "none"}}}
    assert asyncio.run(client._request_response(changed)).output_text == '{"present":true}'
    assert asyncio.run(client._request_response(changed)).output_text == '{"present":true}'
    assert len(calls) == 1
    recorder.finish(keep=True)
    saved = json.loads((tmp_path / "case.json").read_text())
    assert saved[0] == recording[0]
    assert saved[-1]["request"]["extra_body"] == changed["extra_body"]


def test_offline_replay_keeps_the_explicit_ocr_policy_without_a_provider_credential(monkeypatch):
    monkeypatch.setenv("OCR_OPENAI_EXTRA_BODY_JSON", '{"reasoning":{"effort":"none"}}')
    with model_access_scope(replay_model_access()):
        assert ocr_extra_body() == {"reasoning": {"effort": "none"}}


@pytest.mark.parametrize("tampered", [False, True])
def test_official_guest_receives_the_bound_ocr_policy_and_rejects_a_changed_one(tampered):
    from agent.execution.audit_runtime import AuditRunError, run_audit_process
    from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
    from corpus_contracts.official_models import LiveModels
    from documents.model_access import ModelAccess

    access = ModelAccess(SecretStr("host-only-key"), model="controlled", base_url="https://controlled.invalid/v1", revision="provider-revision", ocr_extra_body_json='{"reasoning":{"effort":"none"}}')
    code = b"""from audit.contracts import AuditResultItem
from documents.ocr.env import ocr_extra_body
def check(context):
    status = 'PASS' if ocr_extra_body() == {'reasoning': {'effort': 'none'}} else 'ERR'
    return AuditResultItem(classification='test', item='a', priority='P0', status=status, suggestions='', clause_id='a', checks_performed=1)
"""
    files = {"checker.py": code}
    digest = checker_digest(files)
    source = FrozenInput("target", hashlib.sha256(b"synthetic").hexdigest(), "target", "synthetic.txt")
    target = FrozenTarget("target", "revision", "suite", "a" * 64, {"rules": [{"id": "a", "rule_to_check": "synthetic"}]}, RuleBinding("suite", digest, {"a": "checker:check"}))
    snapshot = ExecutionSnapshot("tenant", "initiator", (source,), (target,), "runtime", "library", access.identity(), round_id="round")
    with LiveModels(access).session() as grant:
        if tampered:
            grant["ocr_extra_body_json"] = '{"reasoning":{"effort":"high"}}'
            with pytest.raises(AuditRunError):
                run_audit_process(snapshot, {"target": b"synthetic"}, {digest: files}, model_grant=grant)
        else:
            result = run_audit_process(snapshot, {"target": b"synthetic"}, {digest: files}, model_grant=grant)
            assert result["findings"][0]["status"] == "PASS"
