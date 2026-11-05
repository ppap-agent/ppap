"""Core composition, trusted input binding, and one whole-audit guest protocol.

The runner here is controlled in-process. ContainerRuntime's separate security
suite owns UID/capability/process testing; this does not claim OS isolation.
"""

import hashlib
import json
from types import SimpleNamespace

import pytest
from agent.client import DeepError
from agent.core_program import AuditJobSpec, CoreRuntime, generated_snapshot, run_audit_manifest, snapshot_development_inputs, write_audit_manifest
from agent.protocol import ExecuteRequest, FrozenFile
from agent.run import Controller, ExecutionContext
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from documents.model_access import DEEPSEEK_MODEL

CODE = b"""from audit.contracts import AuditResultItem

def check(context):
    return AuditResultItem(classification="rule", item=context.clause_id, priority="P0", status="UNABLE",
                           suggestions="No evidence", clause_id=context.clause_id)
"""
FACTORY = b"""from types import SimpleNamespace
from audit.contracts import AuditResultItem

def create_audit(audit_file, rule, group_context):
    def check(clause):
        return AuditResultItem(classification="rule", item=clause, priority="P0", status="UNABLE", suggestions="Unknown", clause_id=clause)
    return SimpleNamespace(checks=[SimpleNamespace(rule_item=SimpleNamespace(id=row["id"]), check=lambda clause=row["id"]: check(clause)) for row in rule.rules])
"""


def setup(unbound=False, *, mode="standard"):
    programs = {"checker.py": CODE}
    digest = checker_digest(programs)
    sources = {"file-a": b"a", "file-b": b"b", "reference": b"reference"}
    inputs = tuple(FrozenInput(key, hashlib.sha256(raw).hexdigest(), "reference" if key == "reference" else "target", key + ".txt") for key, raw in sources.items())
    rules = {"type": "控制计划", "rules": [{"id": "A", "rule_to_check": "A"}, {"id": "B", "rule_to_check": "B"}]}
    targets = tuple(
        FrozenTarget(row.file_version_id, "revision", "UAES:U08", "a" * 64, rules, RuleBinding("UAES:U08", digest, {"A": "checker:check", **({} if unbound else {"B": "checker:check"})}))
        for row in inputs
        if row.role == "target"
    )
    snapshot = ExecutionSnapshot("tenant", "initiator", inputs, targets, "runtime", "library", {"provider": "deepseek", "model": DEEPSEEK_MODEL}, round_id="round")
    spec = ExecuteRequest(
        protocol_revision="protocol",
        executor="ppap-checker-core",
        executor_revision="runtime",
        round_id="round",
        tenant_id="tenant",
        initiating_user_id="initiator",
        job_id="job",
        analysis_mode=mode,
        checker_snapshot_digest=snapshot.input_digest,
        files=[FrozenFile(file_version_id=row.file_version_id, role=row.role, filename=row.filename, sha256=row.sha256, rule_body=rules if row.role == "target" else None) for row in inputs],
    )
    suite = SimpleNamespace(revision=SimpleNamespace(resolved_digest="a" * 64), files=programs)
    return snapshot, sources, {digest: programs}, spec, suite


class ControlledGuest:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.calls = 0

    def ready(self, operation="audit"):
        return True

    def run(self, job, inputs, work, deadline, cancelled):
        self.calls += 1
        grant = self.root / f"grant-{self.calls}"
        grant.mkdir()
        (grant / "model.json").write_text(
            json.dumps({"base_url": "http://127.0.0.1:1/v1", "token": "local-short-lived-capability-1234567890", "model": DEEPSEEK_MODEL, "wall_seconds": 100, "max_turns": 20})
        )
        run_audit_manifest(inputs, work, grant)


def test_standard_whole_audit_once_without_provider_and_explicit_unsupported(tmp_path):
    snapshot, sources, _, spec, suite = setup(unbound=True)
    runner = ControlledGuest(tmp_path)
    runtime = CoreRuntime(runner, lambda _spec: snapshot, lambda *_: suite)
    controller = Controller(runtime, lambda identity, limit: sources[identity], executor_revision="runtime", protocol_revision="protocol")
    result = controller.handle(spec)
    assert runner.calls == 1
    assert [row.status for row in result.findings] == ["UNABLE", "UNABLE"]
    assert len([row for row in result.coverage.rule_items if row["coverage"] == "unsupported"]) == 2
    assert result.coverage.checker_execution["input_digest"] == snapshot.input_digest
    assert all(row.locator["verification_status"] == "unverified" for row in result.findings)
    assert "checker.py" not in result.model_dump_json()
    assert "local-short-lived" not in result.model_dump_json()


@pytest.mark.parametrize("changed", ["tenant_id", "round_id", "initiating_user_id", "checker_snapshot_digest", "executor_revision"])
def test_runtime_rejects_authority_mismatch_before_execution(tmp_path, changed):
    snapshot, sources, _, spec, suite = setup()
    runtime = CoreRuntime(ControlledGuest(tmp_path), lambda _spec: snapshot, lambda *_: suite)
    forged = spec.model_copy(update={changed: "different"})
    with pytest.raises(DeepError, match="deep_input_unauthorized"):
        runtime.execute(forged, sources, context=ExecutionContext())
    assert runtime.runtime.calls == 0


def test_host_refuses_changed_suite_bytes(tmp_path):
    snapshot, sources, _, spec, suite = setup()
    suite.files = {"checker.py": CODE + b"# changed"}
    runtime = CoreRuntime(ControlledGuest(tmp_path), lambda _spec: snapshot, lambda *_: suite)
    with pytest.raises(DeepError, match="deep_integrity_mismatch"):
        runtime.execute(spec, sources, context=ExecutionContext())
    assert runtime.runtime.calls == 0


def test_deep_generates_and_executes_all_targets_in_one_guest(tmp_path, monkeypatch):
    from agent.checker_spec import CheckerArtifact

    snapshot, sources, _, spec, suite = setup(mode="deep")
    generated = []

    def develop(**kwargs):
        generated.append(kwargs["audit_file"].audit_id)
        assert kwargs["prior_files"]["checker.py"] == CODE.decode()
        return CheckerArtifact(input_digest=kwargs["digest"], executor_revision=kwargs["executor_revision"], entrypoint="checker:create_audit", files={"checker.py": FACTORY})

    monkeypatch.setattr("agent.execution.checker_guest.develop_checker", develop)
    monkeypatch.setattr("agent.execution.guest_model.PiRunner", lambda grant: SimpleNamespace(wall_seconds=100))
    saved = []

    def persist(passed_spec, original, artifacts):
        saved.append((passed_spec, original, artifacts))
        return {key: {"suite_revision_id": "generated-" + key, "suite_digest": "b" * 64} for key in artifacts}

    runner = ControlledGuest(tmp_path)
    runtime = CoreRuntime(runner, lambda _spec: snapshot, lambda *_: suite, save_round_artifacts=persist)
    controller = Controller(runtime, lambda identity, limit: sources[identity], executor_revision="runtime", protocol_revision="protocol")
    result = controller.handle(spec)
    assert runner.calls == 1
    assert generated == ["file-a", "file-b"]
    assert saved[0][1] == snapshot
    assert len(result.findings) == 4
    assert {row["suite_revision_id"] for row in result.coverage.rule_items} == {"generated-file-a", "generated-file-b"}
    assert {row["verification_status"] for row in result.coverage.rule_items} == {"unverified"}
    assert result.coverage.checker_execution["input_digest"] == snapshot.input_digest
    assert "create_audit" not in result.model_dump_json()


def test_generated_artifact_must_bind_exact_frozen_inputs():
    from agent.checker_spec import CheckerArtifact

    snapshot, sources, *_ = setup()
    inputs = snapshot_development_inputs(snapshot, sources)
    artifacts = {key: CheckerArtifact(input_digest=item.digest, executor_revision="runtime", entrypoint="checker:create_audit", files={"checker.py": FACTORY}) for key, item in inputs.items()}
    effective = generated_snapshot(snapshot, sources, artifacts)
    assert {target.verification for target in effective.targets} == {"unverified"}
    artifacts["file-a"] = artifacts["file-a"].model_copy(update={"input_digest": "f" * 64})
    with pytest.raises(DeepError, match="deep_output_identity_mismatch"):
        generated_snapshot(snapshot, sources, artifacts)


def test_manifest_rejects_paths_and_hash_changes(tmp_path):
    snapshot, sources, programs, *_ = setup()
    inputs = tmp_path / "inputs"
    write_audit_manifest(inputs, snapshot, sources, programs)
    manifest = json.loads((inputs / "manifest.json").read_text())
    manifest["source_paths"]["file-a"] = "../secret"
    (inputs / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="audit_manifest_path_invalid"):
        run_audit_manifest(inputs, tmp_path / "work", tmp_path / "grant")
    with pytest.raises(ValueError, match="snapshot_source_digest_mismatch"):
        write_audit_manifest(tmp_path / "other", snapshot, {**sources, "file-a": b"tampered"}, programs)


def test_offline_manifest_needs_no_grant_and_cannot_inherit_personal_key(tmp_path):
    from dataclasses import replace

    from documents.model_access import ModelAccess, model_access_scope
    from pydantic import SecretStr

    code = b"""from audit.contracts import AuditResultItem
from documents.model_access import current_model_access, ModelUnavailable

def check(context):
    try:
        current_model_access()
    except ModelUnavailable:
        return AuditResultItem(classification="rule", item=context.clause_id, priority="P0", status="UNABLE",
                               suggestions="No model access", clause_id=context.clause_id)
    raise AssertionError("Hidden evaluation inherited a personal model key")
"""
    snapshot, sources, *_ = setup()
    digest = checker_digest({"checker.py": code})
    snapshot = replace(snapshot, targets=tuple(replace(target, binding=replace(target.binding, checker_digest=digest)) for target in snapshot.targets))
    write_audit_manifest(tmp_path / "inputs", snapshot, sources, {digest: {"checker.py": code}}, allow_model_calls=False)
    with model_access_scope(ModelAccess(api_key=SecretStr("outer-key-never-for-the-hidden-trial"))):
        run_audit_manifest(tmp_path / "inputs", tmp_path / "work", tmp_path / "nonexistent-grants")
    result = json.loads((tmp_path / "work" / "result" / "bundle.json").read_text())
    assert len(result["findings"]) == 4
    assert {row["suggestions"] for row in result["findings"]} == {"No model access"}
    assert "outer-key" not in json.dumps(result)


def test_offline_and_model_jobs_have_separate_replay_identities(tmp_path):
    snapshot, sources, programs, *_ = setup()
    assert AuditJobSpec.from_snapshot(snapshot).execution_id != AuditJobSpec.from_snapshot(snapshot, allow_model_calls=False).execution_id
    write_audit_manifest(tmp_path / "inputs", snapshot, sources, programs, analysis_mode="deep", allow_model_calls=False)
    with pytest.raises(ValueError, match="audit_generation_requires_model"):
        run_audit_manifest(tmp_path / "inputs", tmp_path / "work", tmp_path / "nonexistent-grants")


@pytest.mark.parametrize("oracle,allow_model_calls,expected", [((), True, True), ((), False, False), ((object(),), True, False)])
def test_fixed_development_trials_do_not_give_models_oracle_inputs(tmp_path, monkeypatch, oracle, allow_model_calls, expected):
    from agent.auditor import SemanticRuntime
    from agent.checker_spec import CheckerArtifact

    snapshot, sources, _, spec, suite = setup()
    inputs = snapshot_development_inputs(snapshot, sources)["file-a"]
    artifact = CheckerArtifact(input_digest=inputs.digest, executor_revision="runtime", entrypoint="checker:create_audit", files={"checker.py": FACTORY})
    runtime = CoreRuntime(ControlledGuest(tmp_path), lambda _spec: snapshot, lambda *_: suite)
    observed = []

    def execute_snapshot(*args, **kwargs):
        observed.append(kwargs["allow_model_calls"])
        return {"findings": []}

    def fixed_job():
        return runtime._checker_job(
            spec,
            inputs.audit_file,
            inputs.rule,
            inputs.file_bytes,
            role=inputs.role,
            context=ExecutionContext(),
            group_context=inputs.group_context,
            related_bytes=inputs.related_bytes,
            operation="execute-checker",
            artifact=artifact,
        )

    monkeypatch.setattr(runtime, "execute_snapshot", execute_snapshot)
    monkeypatch.setattr(SemanticRuntime, "develop_and_run", lambda self, request, **kwargs: fixed_job())
    runtime.develop_and_run(SimpleNamespace(oracle=oracle, allow_model_calls=allow_model_calls), spec=spec, context=ExecutionContext())
    fixed_job()
    assert observed == [expected, True]  # The request's scope never leaks into another execution.


def test_container_offline_audit_never_issues_a_model_grant(tmp_path, monkeypatch):
    import threading
    import time

    from agent.execution.isolation.container import ContainerRuntime

    snapshot, *_ = setup()
    runtime = object.__new__(ContainerRuntime)
    runtime.slot = threading.Semaphore(1)
    runtime.degraded = False
    runtime.journal, runtime.attempts = tmp_path / "journal", tmp_path / "attempts"
    run = tmp_path / "run"
    run.mkdir()
    exported = []
    monkeypatch.setattr(runtime, "_new_run_dir", lambda *args: run)
    monkeypatch.setattr(runtime, "_execute", lambda *args: None)
    monkeypatch.setattr(runtime, "_export", lambda run, work, model_operation, *args, **kwargs: exported.append(model_operation))

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline audit must not issue or settle a model grant")

    monkeypatch.setattr(runtime, "_write_grant", forbidden)
    monkeypatch.setattr(runtime, "_seal_usage", forbidden)
    runtime.run(AuditJobSpec.from_snapshot(snapshot, allow_model_calls=False), tmp_path / "inputs", tmp_path / "work", time.monotonic() + 10, threading.Event())
    assert exported == [False]
    assert not run.exists()
    assert not runtime.attempts.exists()
