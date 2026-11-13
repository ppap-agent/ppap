"""Official journal/attempt fencing with controlled guests and zero provider calls."""

import hashlib
import json
import shutil
import threading
import time
from dataclasses import replace

import pytest
from agent.core_program import write_audit_manifest
from agent.execution.isolation.container import ContainerRuntime
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.protocol import ControllerFault


class ControlledContainer:
    """Use real run/export/journal/fences; replace only guest and grant transport."""

    def __init__(self, root, monkeypatch):
        self.root, self.calls, self.grants = root, [], []
        # An interrupted attempt has an unknown outcome.
        self.interrupted = False
        self.runtime = object.__new__(ContainerRuntime)
        self.runtime.slot = threading.Lock()
        self.runtime.degraded = False
        self.runtime.journal = root / "journal"
        self.runtime.attempts = root / "attempts"
        monkeypatch.setattr(self.runtime, "_new_run_dir", self.new_run)
        monkeypatch.setattr(self.runtime, "_execute", self.execute)
        monkeypatch.setattr(self.runtime, "_write_grant", lambda identity, *_: self.grants.append(identity))
        monkeypatch.setattr(self.runtime, "_seal_usage", lambda *_args, **_kwargs: {"completed_requests": 1, "input_tokens": 3, "output_tokens": 5, "usage_known": True, "in_flight": False})

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
        payload = {"round_id": manifest["snapshot"]["round_id"]}
        (run / "work" / "result" / "bundle.json").write_text(json.dumps(payload))

    def run(self, spec, inputs):
        work = self.root / f"work-{time.monotonic_ns()}"
        self.runtime.run(spec, inputs, work, time.monotonic() + 5, threading.Event())
        return json.loads((work / "result" / "bundle.json").read_text())


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
