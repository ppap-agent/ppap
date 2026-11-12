"""Core composition, trusted input binding, and one whole-audit guest protocol.

The runner here is controlled in-process. ContainerRuntime's separate security
suite owns UID/capability/process testing; this does not claim OS isolation.
"""

import hashlib
import json
from types import SimpleNamespace

import pytest
from agent.client import DeepError
from agent.core_program import AuditJobSpec, CoreRuntime, run_audit_manifest, write_audit_manifest
from agent.protocol import ExecuteRequest, FrozenFile
from agent.run import Controller, ExecutionContext
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest

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
    snapshot = ExecutionSnapshot("tenant", "initiator", inputs, targets, "runtime", "library", {"provider": "responses-compatible", "model": "test-model"}, round_id="round")
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
        model_identity=dict(snapshot.model_identity),
        files=[FrozenFile(file_version_id=row.file_version_id, role=row.role, filename=row.filename, sha256=row.sha256, rule_body=rules if row.role == "target" else None) for row in inputs],
    )
    suite = SimpleNamespace(revision=SimpleNamespace(resolved_digest="a" * 64), files=programs)
    return snapshot, sources, {digest: programs}, spec, suite


def development_input(snapshot, sources, target="file-a", *, rules_by_id=None):
    """Trial inputs as development freezes them: one target plus its authorized group."""
    from agent.checker_development import FrozenDevelopmentInput
    from audit.contracts import AuditInput, GroupContextInput, RuleInput

    audits = {
        row.file_version_id: AuditInput(audit_id=row.file_version_id, file_url=row.filename, file_metadata={"file_version_id": row.file_version_id, "original_filename": row.filename})
        for row in snapshot.inputs
    }
    by_type: dict = {}
    for row in snapshot.inputs:
        if row.document_type:
            by_type.setdefault(row.document_type, []).append(audits[row.file_version_id])
    frozen = next(row for row in snapshot.targets if row.file_version_id == target)
    rule = RuleInput.model_validate({**frozen.rules, "rule_id": frozen.suite_revision_id})
    group = GroupContextInput(task_group_id=snapshot.round_id, by_audit_id=audits, by_doc_type=by_type, rules_by_id=rules_by_id or {})
    return FrozenDevelopmentInput(audits[target], rule, sources[target], "target", group, dict(sources))


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
        (grant / "model.json").write_text(json.dumps({"base_url": "http://127.0.0.1:1/v1", "token": "local-short-lived-capability-1234567890", "model": "test-model", "wall_seconds": 100}))
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
    with model_access_scope(ModelAccess(api_key=SecretStr("outer-key-never-for-the-hidden-trial"), model="test-model", base_url="https://models.example.test")):
        run_audit_manifest(tmp_path / "inputs", tmp_path / "work", tmp_path / "nonexistent-grants")
    result = json.loads((tmp_path / "work" / "result" / "bundle.json").read_text())
    assert len(result["findings"]) == 4
    assert {row["suggestions"] for row in result["findings"]} == {"No model access"}
    assert "outer-key" not in json.dumps(result)


def test_offline_and_model_jobs_have_separate_replay_identities():
    snapshot, *_ = setup()
    assert AuditJobSpec.from_snapshot(snapshot).execution_id != AuditJobSpec.from_snapshot(snapshot, allow_model_calls=False).execution_id


def test_audit_manifest_never_generates_checkers(tmp_path):
    # Audit-time checker generation was retired (#322); a late Deep job cannot run.
    snapshot, sources, programs, *_ = setup()
    with pytest.raises(DeepError, match="deep_audit_retired"):
        write_audit_manifest(tmp_path / "rejected", snapshot, sources, programs, analysis_mode="deep")
    assert not (tmp_path / "rejected").exists()
    write_audit_manifest(tmp_path / "inputs", snapshot, sources, programs, allow_model_calls=False)
    manifest = json.loads((tmp_path / "inputs" / "manifest.json").read_text())
    manifest["spec"] = AuditJobSpec.from_snapshot(snapshot, "deep", allow_model_calls=False).model_dump(mode="json")
    (tmp_path / "inputs" / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="deep_audit_retired"):
        run_audit_manifest(tmp_path / "inputs", tmp_path / "work", tmp_path / "nonexistent-grants")
    assert not (tmp_path / "work" / "result").exists()


@pytest.mark.parametrize("allow_model_calls", [False, True])
def test_manifest_model_permission_controls_visual_runtime_even_with_inherited_paddle_environment(tmp_path, monkeypatch, allow_model_calls):
    from dataclasses import replace

    calls = []
    monkeypatch.setenv("PADDLE_OCR_URL", "http://127.0.0.1:1/forbidden-upstream")
    monkeypatch.setenv("PADDLE_TOKEN", "inherited-token")
    monkeypatch.setenv("PPAP_PADDLE_BRIDGE_URL", "http://127.0.0.1:1/paddle-page")
    monkeypatch.setenv("PPAP_PADDLE_BRIDGE_TOKEN", "forged-capability")

    def paddle(*args):
        calls.append("paddle")
        return {}

    def ocr(**kwargs):
        calls.append("ocr")
        return {}

    monkeypatch.setattr("documents.ocr.paddle.reader.read_pdf_page_evidence", paddle)
    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", ocr)
    code = b"""from audit.contracts import AuditResultItem

def check(context):
    if context.clause_id == 'A':
        context.runtime.read_paddle_page(context.audit_file.file_url, 1)
    else:
        context.runtime.read_ocr_visual_facts(images=[b'original'], prompt='Read', response_schema={})
    return AuditResultItem(classification='rule', item=context.clause_id, priority='P0', status='PASS',
                           suggestions='Controlled visual observation', clause_id=context.clause_id, checks_performed=1)
"""
    snapshot, sources, *_ = setup()
    digest = checker_digest({"checker.py": code})
    snapshot = replace(snapshot, targets=tuple(replace(target, binding=replace(target.binding, checker_digest=digest)) for target in snapshot.targets))
    job = write_audit_manifest(tmp_path / "inputs", snapshot, sources, {digest: {"checker.py": code}}, allow_model_calls=allow_model_calls)
    if allow_model_calls:
        ControlledGuest(tmp_path).run(job, tmp_path / "inputs", tmp_path / "work", None, None)
    else:
        run_audit_manifest(tmp_path / "inputs", tmp_path / "work", tmp_path / "nonexistent-grants")
    result = json.loads((tmp_path / "work" / "result" / "bundle.json").read_text())
    assert len(result["findings"]) == 4
    if allow_model_calls:
        assert {row["status"] for row in result["findings"]} == {"PASS"}
        assert calls == ["paddle", "ocr", "paddle"]
    else:
        assert {row["status"] for row in result["findings"]} == {"UNABLE"}
        assert calls == []


@pytest.mark.parametrize("oracle,allow_model_calls,expected", [((), True, True), ((), False, False), ((object(),), True, False)])
def test_fixed_development_trials_do_not_give_models_oracle_inputs(tmp_path, monkeypatch, oracle, allow_model_calls, expected):
    from agent.auditor import SemanticRuntime
    from agent.checker_spec import CheckerArtifact

    snapshot, sources, _, spec, suite = setup()
    inputs = development_input(snapshot, sources)
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


def test_development_freezes_reference_type_and_rules_without_requiring_a_rule():
    from dataclasses import replace

    from agent.checker_spec import CheckerArtifact
    from agent.core_program import development_snapshot
    from audit.contracts import RuleInput

    snapshot, sources, _, spec, _ = setup()
    reference = replace(snapshot.inputs[-1], document_type="过程流程图")
    snapshot = replace(snapshot, inputs=(*snapshot.inputs[:-1], reference))
    prepared = development_input(snapshot, sources)
    artifact = CheckerArtifact(input_digest=prepared.digest, executor_revision="runtime", entrypoint="checker:create_audit", files={"checker.py": FACTORY})
    frozen, _ = development_snapshot(prepared, artifact, spec)
    row = next(row for row in frozen.inputs if row.file_version_id == "reference")
    assert row.document_type == "过程流程图"
    assert row.rules is None
    rules = RuleInput(rule_id="reference-rules", type="过程流程图", rules=[{"id": "P1", "rule_to_check": "工序顺序"}], appendix=[{"text": "已确认附录"}])
    with_rules = development_input(snapshot, sources, rules_by_id={"reference": rules})
    assert with_rules.digest != prepared.digest
    frozen_with_rules, _ = development_snapshot(with_rules, artifact, spec)
    assert frozen_with_rules.input_digest != frozen.input_digest
    restored = next(row for row in frozen_with_rules.inputs if row.file_version_id == "reference")
    assert restored.rule_revision_id == "reference-rules"
    assert restored.rules["rules"][0]["id"] == "P1"
    assert restored.appendix == ({"text": "已确认附录"},)


def test_standard_missing_target_never_imports_its_program_and_keeps_other_results(tmp_path):
    from dataclasses import replace

    from audit.storage_errors import ObjectMissing

    snapshot, sources, _, spec, suite = setup(unbound=True)
    broken_code = {"checker.py": b"raise AssertionError('Missing original must never import its checker')\n"}
    missing = snapshot.targets[0]
    snapshot = replace(snapshot, targets=(replace(missing, suite_revision_id="missing-revision", binding=replace(missing.binding, checker_digest=checker_digest(broken_code))), snapshot.targets[1]))
    spec = spec.model_copy(update={"checker_snapshot_digest": snapshot.input_digest})
    broken_suite = SimpleNamespace(revision=SimpleNamespace(resolved_digest="a" * 64), files=broken_code)
    reads, authorization = [], []

    def source(identity, limit):
        assert authorization == [spec]
        reads.append(identity)
        if identity == "file-a":
            raise ObjectMissing("private storage details")
        return sources[identity]

    guest = ControlledGuest(tmp_path)
    runtime = CoreRuntime(guest, lambda _: snapshot, lambda revision, _: broken_suite if revision == "missing-revision" else suite)
    controller = Controller(runtime, source, executor_revision="runtime", protocol_revision="protocol", authorize=authorization.append)
    context = ExecutionContext()
    result = controller.handle(spec, context)
    assert reads == ["file-a", "file-b", "reference"] and guest.calls == 1
    assert [(item.locator["file_version_id"], item.status) for item in result.findings] == [("file-a", "ERR"), ("file-b", "UNABLE")]
    assert len([row for row in result.coverage.rule_items if row["coverage"] == "unsupported"]) == 2
    assert result.coverage.checker_execution["input_digest"] == snapshot.input_digest
    assert result.coverage.checker_execution["unavailable_file_version_ids"] == ["file-a"]
    assert context.unavailable_sources == frozenset()
    assert "private storage" not in result.model_dump_json()


@pytest.mark.parametrize("kind", ["reference-kind", None])
def test_missing_reference_blocks_dependent_clause_but_not_independent_clause(tmp_path, kind):
    from dataclasses import replace

    snapshot, sources, _, spec, _ = setup()
    code = b"""from audit.contracts import AuditResultItem

def check(context):
    assert 'reference' not in context.group_context.by_audit_id
    assert 'reference' in context.group_context.unavailable_files
    if context.clause_id == 'A':
        context.group_context.get_files_by_type('reference-kind')
    assert context.sources[context.audit_file.audit_id] in {b'a', b'b'}
    return AuditResultItem(classification='test', item=context.clause_id, priority='P0', status='PASS', suggestions='Independent native check', clause_id=context.clause_id, checks_performed=1)
"""
    digest = checker_digest({"checker.py": code})
    snapshot = replace(
        snapshot,
        inputs=tuple(replace(row, document_type=kind) if row.role == "reference" else row for row in snapshot.inputs),
        targets=tuple(replace(row, binding=replace(row.binding, checker_digest=digest)) for row in snapshot.targets),
    )
    spec = spec.model_copy(update={"checker_snapshot_digest": snapshot.input_digest})
    suite = SimpleNamespace(revision=SimpleNamespace(resolved_digest="a" * 64), files={"checker.py": code})

    def source(identity, limit):
        if identity == "reference":
            raise DeepError("deep_input_missing")
        return sources[identity]

    guest = ControlledGuest(tmp_path)
    runtime = CoreRuntime(guest, lambda _: snapshot, lambda *_: suite)
    result = Controller(runtime, source, executor_revision="runtime", protocol_revision="protocol").handle(spec)
    assert [item.status for item in result.findings] == ["UNABLE", "PASS", "UNABLE", "PASS"]
    assert guest.calls == 1
    assert result.coverage.checker_execution["unavailable_file_version_ids"] == ["reference"]


@pytest.mark.parametrize("failure", ["deep_input_unauthorized", "deep_integrity_mismatch", "deep_budget_exhausted", "permission", "tampered", "unknown"])
def test_missing_input_tolerance_never_downgrades_other_failures(tmp_path, failure):
    snapshot, sources, _, spec, suite = setup()
    guest = ControlledGuest(tmp_path)
    runtime = CoreRuntime(guest, lambda _: snapshot, lambda *_: suite)

    def source(identity, limit):
        if identity == "file-a":
            raise DeepError("deep_input_missing")
        if failure == "permission":
            raise PermissionError("storage permission denied")
        if failure == "unknown":
            raise RuntimeError("unknown source failure")
        if failure == "tampered":
            return b"tampered"
        raise DeepError(failure)

    with pytest.raises(DeepError):
        Controller(runtime, source, executor_revision="runtime", protocol_revision="protocol").handle(spec)
    assert guest.calls == 0


def test_authorization_and_deep_missing_inputs_still_fail_before_guest(tmp_path):
    snapshot, sources, _, spec, suite = setup(mode="deep")
    guest = ControlledGuest(tmp_path)
    runtime = CoreRuntime(guest, lambda _: snapshot, lambda *_: suite)
    reads = []

    def deny(_):
        raise DeepError("deep_input_unauthorized")

    controller = Controller(runtime, lambda *args: reads.append(args), executor_revision="runtime", protocol_revision="protocol", authorize=deny)
    with pytest.raises(DeepError, match="deep_input_unauthorized"):
        controller.handle(spec)
    assert not reads

    def source(*args):
        raise DeepError("deep_input_missing")

    controller = Controller(runtime, source, executor_revision="runtime", protocol_revision="protocol")
    with pytest.raises(DeepError, match="deep_input_missing"):
        controller.handle(spec)
    controller = Controller(runtime, lambda identity, limit: sources[identity], executor_revision="runtime", protocol_revision="protocol")
    with pytest.raises(DeepError, match="deep_audit_retired"):
        controller.handle(spec)
    assert guest.calls == 0


def test_unavailable_sources_are_explicit_disjoint_and_change_attempt_identity(tmp_path):
    snapshot, sources, programs, *_ = setup()
    missing = frozenset({"file-a"})
    reduced = {key: value for key, value in sources.items() if key not in missing}
    snapshot.validate_sources(reduced, unavailable=missing)
    complete = AuditJobSpec.from_snapshot(snapshot)
    incomplete = write_audit_manifest(tmp_path / "inputs", snapshot, reduced, programs, unavailable_sources=missing, allow_model_calls=False)
    assert incomplete.input_digest == complete.input_digest == snapshot.input_digest
    assert incomplete.execution_id != AuditJobSpec.from_snapshot(snapshot, allow_model_calls=False).execution_id
    for data, unavailable in [(reduced, frozenset()), (sources, missing), (reduced, frozenset({"foreign"}))]:
        with pytest.raises(ValueError, match="snapshot_sources_mismatch"):
            snapshot.validate_sources(data, unavailable=unavailable)
    with pytest.raises(ValueError, match="snapshot_source_digest_mismatch"):
        snapshot.validate_sources({**reduced, "file-b": b"changed"}, unavailable=missing)
