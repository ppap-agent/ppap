"""Official journal/attempt fencing with controlled guests and zero provider calls."""

import hashlib
import json
import shutil
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

import pytest
from agent.checker_development import FrozenDevelopmentInput
from agent.checker_program import CheckerManifest
from agent.checker_spec import CheckerArtifact, CheckerJobSpec
from agent.client import DeepError
from agent.core_program import CoreRuntime, development_snapshot, write_audit_manifest
from agent.execution.isolation.container import ContainerRuntime
from agent.protocol import ExecuteRequest
from agent.run import ExecutionContext
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.contracts import AuditInput, RuleInput
from audit.protocol import ControllerFault


class ControlledContainer:
    """Use real run/export/journal/fences; replace only guest and grant transport."""

    def __init__(self, root, monkeypatch):
        self.root, self.calls, self.grants = root, [], []
        # An interrupted attempt has an unknown outcome; unknown token counts alone do not.
        self.interrupted = False
        self.usage_known = True
        self.runtime = object.__new__(ContainerRuntime)
        self.runtime.slot = threading.Lock()
        self.runtime.degraded = False
        self.runtime.journal = root / "journal"
        self.runtime.attempts = root / "attempts"
        monkeypatch.setattr(self.runtime, "_new_run_dir", self.new_run)
        monkeypatch.setattr(self.runtime, "_execute", self.execute)
        monkeypatch.setattr(self.runtime, "_write_grant", lambda identity, *_: self.grants.append(identity))
        monkeypatch.setattr(
            self.runtime, "_seal_usage", lambda *_args, **_kwargs: {"completed_requests": 1, "input_tokens": 3, "output_tokens": 5, "usage_known": self.usage_known, "in_flight": False}
        )

    def new_run(self, identity, inputs):
        run = self.root / ("run-" + identity.hex)
        shutil.copytree(inputs, run / "input")
        (run / "work" / "result").mkdir(parents=True)
        return run

    def execute(self, run, *_args):
        manifest = json.loads((run / "input" / "manifest.json").read_text())
        self.calls.append(manifest)
        if self.interrupted:
            raise ControllerFault("MODEL_TIMEOUT")
        payload = {"prior_files": manifest.get("prior_files"), "feedback": manifest.get("development_feedback"), "round_id": manifest.get("snapshot", {}).get("round_id")}
        (run / "work" / "result" / "bundle.json").write_text(json.dumps(payload))

    def run(self, spec, inputs):
        work = self.root / f"work-{time.monotonic_ns()}"
        self.runtime.run(spec, inputs, work, time.monotonic() + 5, threading.Event())
        return json.loads((work / "result" / "bundle.json").read_text())


def adaptation(root, *, job_id="operation-one", prior="first", feedback="first"):
    spec = CheckerJobSpec(execution_id=UUID(int=0), operation="adapt-checker", input_digest="a" * 64, executor_revision="revision")
    spec = spec.model_copy(update={"execution_id": spec.expected_execution_id()})
    manifest = CheckerManifest(operation=spec.operation, spec=spec, audit_id="file", rule={}, file_sha256="b" * 64, role="target", prior_files={"checker.py": prior}, development_feedback=feedback)
    raw = {**manifest.model_dump(mode="json"), "runtime_job_id": job_id}
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps(raw))
    return spec, root


@pytest.mark.parametrize("change", ["feedback", "prior", "neither"])
def test_new_adaptation_operation_never_reads_previous_job_bundle(tmp_path, monkeypatch, change):
    container = ControlledContainer(tmp_path, monkeypatch)
    first = adaptation(tmp_path / "first")
    original = container.run(*first)
    assert container.run(*first) == original
    second = adaptation(tmp_path / "second", job_id="operation-two", prior="second" if change == "prior" else "first", feedback="second" if change == "feedback" else "first")
    assert second[0] == first[0]  # Same document/rules must not mean the same paid operation.
    latest = container.run(*second)
    assert len(container.calls) == 2
    assert latest["feedback"] == ("second" if change == "feedback" else "first")
    assert latest["prior_files"] == {"checker.py": "second" if change == "prior" else "first"}
    assert container.run(*second) == latest
    assert container.run(*first) == original
    assert len(container.grants) == len(set(container.grants)) == 2
    assert len(list(container.runtime.journal.iterdir())) == 2
    assert len(list(container.runtime.attempts.iterdir())) == 2
    assert original["model_requests"] == latest["model_requests"] == 1


def test_unknown_provider_usage_keeps_a_valid_result_without_counts(tmp_path, monkeypatch):
    container = ControlledContainer(tmp_path, monkeypatch)
    container.usage_known = False
    first = adaptation(tmp_path / "first")
    result = container.run(*first)
    assert result["prior_files"] == {"checker.py": "first"}
    assert not {"input_tokens", "output_tokens", "model_requests"} & set(result)
    assert container.run(*first) == result
    assert len(container.calls) == 1


def test_unknown_adaptation_fence_survives_replay_and_changed_feedback(tmp_path, monkeypatch):
    container = ControlledContainer(tmp_path, monkeypatch)
    first = adaptation(tmp_path / "first")
    container.interrupted = True
    with pytest.raises(ControllerFault, match="MODEL_TIMEOUT"):
        container.run(*first)
    container.interrupted = False
    # Same job is not made safe by a new automatic attempt or edited feedback.
    replay = adaptation(tmp_path / "replay", prior="changed", feedback="changed")
    with pytest.raises(ControllerFault, match="MODEL_PROTOCOL_ERROR"):
        container.run(*replay)
    assert len(container.calls) == len(container.grants) == 1
    second = adaptation(tmp_path / "second", job_id="explicit-new-operation", prior="changed", feedback="changed")
    assert container.run(*second)["feedback"] == "changed"
    with pytest.raises(ControllerFault, match="MODEL_PROTOCOL_ERROR"):
        container.run(*first)
    assert len(container.calls) == len(container.grants) == 2
    assert len(list(container.runtime.journal.iterdir())) == 1
    assert len(list(container.runtime.attempts.iterdir())) == 2


@pytest.mark.parametrize("change", ["absent_job", "empty_job", "spec", "operation"])
def test_adaptation_identity_is_checked_before_journal_replay(tmp_path, monkeypatch, change):
    container = ControlledContainer(tmp_path, monkeypatch)
    spec, inputs = adaptation(tmp_path / "first")
    container.run(spec, inputs)
    raw = json.loads((inputs / "manifest.json").read_text())
    if change == "absent_job":
        raw.pop("runtime_job_id")
    elif change == "empty_job":
        raw["runtime_job_id"] = ""
    elif change == "spec":
        raw["spec"]["input_digest"] = "c" * 64
    else:
        raw["operation"] = "execute-checker"
    (inputs / "manifest.json").write_text(json.dumps(raw))
    with pytest.raises(ControllerFault, match="INPUT_INTEGRITY_ERROR"):
        container.run(spec, inputs)
    assert len(container.calls) == len(container.grants) == 1


def frozen_trial():
    files = {"checker.py": b"def check(context): return None\n"}
    digest = checker_digest(files)
    raw = b"original"
    source = FrozenInput("file", hashlib.sha256(raw).hexdigest(), "target", "file.txt")
    rules = {"type": "自定义", "rules": [{"id": "R1", "rule_to_check": "Check"}]}
    target = FrozenTarget("file", "revision", "suite", digest, rules, RuleBinding("suite", digest, {"R1": "checker:check"}))
    snapshot = ExecutionSnapshot("tenant", "user", (source,), (target,), "revision", "library", {}, round_id="operation-one")
    return snapshot, {"file": raw}, {digest: files}


@pytest.mark.parametrize("unknown", [False, True])
def test_model_enabled_core_fixed_trials_already_bind_the_frozen_operation(tmp_path, monkeypatch, unknown):
    container = ControlledContainer(tmp_path, monkeypatch)
    snapshot, sources, programs = frozen_trial()
    first_inputs = tmp_path / "first"
    first = write_audit_manifest(first_inputs, snapshot, sources, programs)
    container.interrupted = unknown
    if unknown:
        with pytest.raises(ControllerFault, match="MODEL_TIMEOUT"):
            container.run(first, first_inputs)
    else:
        original = container.run(first, first_inputs)
        assert container.run(first, first_inputs) == original
    container.interrupted = False
    second_inputs = tmp_path / "second"
    second = write_audit_manifest(second_inputs, replace(snapshot, round_id="explicit-new-operation"), sources, programs)
    assert first.execution_id != second.execution_id
    result = container.run(second, second_inputs)
    assert result["round_id"] == "explicit-new-operation"
    assert container.run(second, second_inputs) == result
    if unknown:
        with pytest.raises(ControllerFault, match="MODEL_PROTOCOL_ERROR"):
            container.run(first, first_inputs)
    else:
        assert container.run(first, first_inputs) == original
    assert len(container.calls) == len(container.grants) == 2


def test_trusted_host_keeps_job_identity_across_attempts_not_across_new_operations(tmp_path, monkeypatch):
    snapshot, sources, _ = frozen_trial()
    target = snapshot.targets[0]
    audit = AuditInput(audit_id="file", file_url="file.txt", file_metadata={"file_version_id": "file", "original_filename": "file.txt"})
    inputs = FrozenDevelopmentInput(audit, RuleInput.model_validate({**target.rules, "rule_id": target.suite_revision_id}), sources["file"], related_bytes=dict(sources))
    captured = []

    def capture(job, staged, *_args, **_kwargs):
        captured.append(CheckerManifest.model_validate_json((staged / "manifest.json").read_bytes()))
        raise DeepError("deep_cancelled")

    runtime = CoreRuntime(SimpleNamespace(run=capture), None, None)
    monkeypatch.setattr(runtime, "execution_revision", lambda: "revision")
    monkeypatch.setattr(runtime, "_put", lambda *_: None)
    base = ExecuteRequest(
        protocol_revision="protocol", round_id="operation-one", executor="ppap-checker-core", executor_revision="revision", job_id="job-one", tenant_id="tenant", initiating_user_id="user", files=[]
    )
    artifact = CheckerArtifact(input_digest=inputs.digest, executor_revision="revision", entrypoint="checker:create_audit", files={"checker.py": b"fixed"})
    fixed = []
    for spec in (base, base.model_copy(update={"attempt": 2}), base.model_copy(update={"job_id": "job-two", "round_id": "operation-two"})):
        with pytest.raises(DeepError, match="deep_cancelled"):
            runtime._checker_job(spec, inputs.audit_file, inputs.rule, inputs.file_bytes, role=inputs.role, context=ExecutionContext(), group_context=inputs.group_context, operation="adapt-checker")
        fixed.append(development_snapshot(inputs, artifact, spec)[0].input_digest)
    assert [row.runtime_job_id for row in captured] == ["job-one", "job-one", "job-two"]
    assert captured[0].spec == captured[1].spec == captured[2].spec
    assert fixed[0] == fixed[1] != fixed[2]
