"""Corpus OCR uses explicit dedicated parameters and never reuses another policy's answer."""

import asyncio
import hashlib
import json

import documents.ai
import pytest
from audit.engine.checkers import ocr_visual_facts
from corpus_contracts.replay import Recorder, configured_model_access, install, ocr_request, replay_model_access
from documents.ai import AIResult
from documents.model_access import ModelAccess, model_access_scope
from documents.ocr.env import ocr_extra_body
from pydantic import SecretStr

_SEAMS = {(ocr_visual_facts, "read_visual_facts"): ocr_visual_facts.read_visual_facts, (documents.ai, "run_json_async"): documents.ai.run_json_async}


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
    for (owner, name), original in _SEAMS.items():
        monkeypatch.setattr(owner, name, original)
    calls = []

    async def run(access, **kwargs):
        calls.append(access.extra_body())
        return AIResult({"present": True})

    monkeypatch.setattr("documents.ai._run", run)
    schema = {"name": "fact", "strict": True, "schema": {"type": "object", "properties": {"present": {"type": "boolean"}}, "required": ["present"], "additionalProperties": False}}
    plain = ModelAccess(SecretStr("k"), model="m", base_url="https://models.example.test/v1")
    explicit = ModelAccess(SecretStr("k"), model="m", base_url="https://models.example.test/v1", extra_body_json='{"reasoning":{"effort":"none"}}')
    with model_access_scope(plain):
        recording = [{"kind": "ocr", "request": ocr_request([b"synthetic"], "Observe.", schema, 4096), "model": "recorded", "answer": '{"present":false}'}]
    (tmp_path / "case.json").write_text(json.dumps(recording))
    recorder = Recorder("case", tmp_path, tmp_path)
    install(monkeypatch, recorder)

    def read():
        return asyncio.run(ocr_visual_facts.read_visual_facts([b"synthetic"], "Observe.", response_schema=schema))

    with model_access_scope(plain):
        assert read() == {"present": False}
    with model_access_scope(explicit):
        assert read() == {"present": True}
        assert read() == {"present": True}
    assert calls == [{"reasoning": {"effort": "none"}}]
    recorder.finish(keep=True)
    saved = json.loads((tmp_path / "case.json").read_text())
    assert saved[0] == recording[0]
    assert saved[-1]["request"]["extra_body"] == {"reasoning": {"effort": "none"}}


def test_offline_replay_keeps_the_explicit_ocr_policy_without_a_provider_credential(monkeypatch):
    monkeypatch.setenv("OCR_OPENAI_EXTRA_BODY_JSON", '{"reasoning":{"effort":"none"}}')
    with model_access_scope(replay_model_access()):
        assert ocr_extra_body() == {"reasoning": {"effort": "none"}}


@pytest.mark.parametrize("tampered", [False, True])
def test_official_guest_holds_no_deployment_options_and_rejects_a_changed_grant(tampered):
    from agent.execution.audit_runtime import run_audit_process
    from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
    from corpus_contracts.official_models import LiveModels
    from pydantic import ValidationError

    # The gateway applies the bound options to each vision request; the guest never sees them.
    access = ModelAccess(SecretStr("host-only-key"), model="controlled", base_url="https://controlled.invalid/v1", revision="provider-revision", extra_body_json='{"reasoning":{"effort":"none"}}')
    code = b"""from audit.contracts import AuditResultItem
from documents.ocr.env import ocr_extra_body
def check(context):
    status = 'PASS' if ocr_extra_body() == {} else 'ERR'
    return AuditResultItem(classification='test', item='a', priority='P0', status=status, suggestions='', clause_id='a', checks_performed=1)
"""
    files = {"checker.py": code}
    digest = checker_digest(files)
    source = FrozenInput("target", hashlib.sha256(b"synthetic").hexdigest(), "target", "synthetic.txt")
    target = FrozenTarget("target", "revision", "suite", "a" * 64, {"rules": [{"id": "a", "rule_to_check": "synthetic"}]}, RuleBinding("suite", digest, {"a": "checker:check"}))
    models = LiveModels(access)
    snapshot = ExecutionSnapshot("tenant", "initiator", (source,), (target,), "runtime", "library", models.identity(), round_id="round")
    with models.session() as grant:
        if tampered:
            grant["extra_body_json"] = '{"reasoning":{"effort":"high"}}'
            with pytest.raises(ValidationError):
                run_audit_process(snapshot, {"target": b"synthetic"}, {digest: files}, model_grant=grant)
        else:
            result = run_audit_process(snapshot, {"target": b"synthetic"}, {digest: files}, model_grant=grant)
            assert result["findings"][0]["status"] == "PASS"
