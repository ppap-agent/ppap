"""Corpus OCR sends its configured effort and never reuses an answer recorded under another effort."""

import asyncio
import hashlib
import json

import documents.ai
import pytest
from audit.engine.checkers import ocr_visual_facts
from corpus_contracts.replay import Recorder, install, ocr_request, recording, replay_model_access
from documents.ai import AIResult
from documents.model_access import ModelAccess, model_access_scope
from documents.ocr.env import ocr_request_options
from pydantic import SecretStr

_SEAMS = {(ocr_visual_facts, "read_visual_facts"): ocr_visual_facts.read_visual_facts, (documents.ai, "run_json_async"): documents.ai.run_json_async}


def test_replay_separates_answers_recorded_under_another_effort(tmp_path, monkeypatch):
    for (owner, name), original in _SEAMS.items():
        monkeypatch.setattr(owner, name, original)
    calls = []

    async def run(access, **kwargs):
        calls.append(access.reasoning_effort)
        return AIResult({"present": True})

    monkeypatch.setattr("documents.ai._run", run)
    schema = {"name": "fact", "strict": True, "schema": {"type": "object", "properties": {"present": {"type": "boolean"}}, "required": ["present"], "additionalProperties": False}}
    plain = ModelAccess(SecretStr("k"), model="m", base_url="https://models.example.test/v1")
    explicit = ModelAccess(SecretStr("k"), model="m", base_url="https://models.example.test/v1", reasoning_effort="none")
    with model_access_scope(plain):
        recorded = [recording("ocr", ocr_request([b"synthetic"], "Observe.", schema, 4096), '{"present":false}', "recorded")]
    (tmp_path / "case.json").write_text(json.dumps(recorded))
    recorder = Recorder("case", tmp_path, tmp_path)
    install(monkeypatch, recorder)

    def read():
        return asyncio.run(ocr_visual_facts.read_visual_facts([b"synthetic"], "Observe.", response_schema=schema))

    with model_access_scope(plain):
        assert read() == {"present": False}
    with model_access_scope(explicit):
        assert read() == {"present": True}
        assert read() == {"present": True}
    assert calls == ["none"]
    recorder.finish(keep=True)
    saved = json.loads((tmp_path / "case.json").read_text())
    assert saved[0] == recorded[0]
    assert saved[-1]["request"]["extra_body"] == {"reasoning": {"effort": "none"}}


def test_offline_replay_keeps_the_configured_ocr_effort_without_a_provider_credential(monkeypatch):
    monkeypatch.setenv("PPAP_MODEL_VISION_REASONING_EFFORT", "none")
    with model_access_scope(replay_model_access()):
        assert ocr_request_options() == {"reasoning": {"effort": "none"}}


@pytest.mark.parametrize("tampered", [False, True])
def test_official_guest_sends_the_granted_ocr_effort_and_rejects_a_changed_grant(tampered):
    from agent.execution.audit_runtime import run_audit_process
    from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
    from corpus_contracts.official_models import LiveModels
    from pydantic import ValidationError

    # The guest writes each request itself: the grant gives it the configured effort, never the key.
    access = ModelAccess(SecretStr("host-only-key"), model="controlled", base_url="https://controlled.invalid/v1", revision="provider-revision", reasoning_effort="none")
    code = b"""from audit.contracts import AuditResultItem
from documents.ocr.env import ocr_request_options
def check(context):
    status = 'PASS' if ocr_request_options() == {'reasoning': {'effort': 'none'}} else 'ERR'
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
            grant["api_key"] = "smuggled"
            with pytest.raises(ValidationError):
                run_audit_process(snapshot, {"target": b"synthetic"}, {digest: files}, model_grant=grant)
        else:
            result = run_audit_process(snapshot, {"target": b"synthetic"}, {digest: files}, model_grant=grant)
            assert result["findings"][0]["status"] == "PASS"
