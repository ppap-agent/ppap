"""Core composition, trusted input binding, and one whole-audit guest protocol.

The runner here is controlled in-process. ContainerRuntime's separate security
suite owns UID/capability/process testing; this does not claim OS isolation.
"""

import hashlib
import json
import uuid
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


def setup(unbound=False):
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
        checker_snapshot_digest=snapshot.input_digest,
        model_identity=dict(snapshot.model_identity),
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

    def run(self, job, inputs, work, deadline, cancelled, on_activity=None):
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


def test_execution_id_derivation_is_unchanged_for_existing_executions():
    snapshot, *_ = setup()
    for allow_model_calls in (True, False):
        expected = uuid.uuid5(uuid.NAMESPACE_URL, f"ppap-audit:standard:{allow_model_calls}:{snapshot.input_digest}")
        assert AuditJobSpec.from_snapshot(snapshot, allow_model_calls=allow_model_calls).execution_id == expected
    missing = uuid.uuid5(uuid.NAMESPACE_URL, f'ppap-audit:standard:False:{snapshot.input_digest}:["file-a"]')
    assert AuditJobSpec.from_snapshot(snapshot, allow_model_calls=False, unavailable_sources=frozenset({"file-a"})).execution_id == missing


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


def test_authorization_fails_before_reading_or_guest(tmp_path):
    snapshot, _, _, spec, suite = setup()
    guest = ControlledGuest(tmp_path)
    runtime = CoreRuntime(guest, lambda _: snapshot, lambda *_: suite)
    reads = []

    def deny(_):
        raise DeepError("deep_input_unauthorized")

    controller = Controller(runtime, lambda *args: reads.append(args), executor_revision="runtime", protocol_revision="protocol", authorize=deny)
    with pytest.raises(DeepError, match="deep_input_unauthorized"):
        controller.handle(spec)
    assert not reads
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
