"""Controlled guest-contract tests. OS isolation is owned by test_deep_isolation."""

import hashlib
import io
import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent.execution.guest import run_entrypoint
from agent.execution.precheck_runtime import run_precheck
from agent.precheck_program import PrecheckJobSpec, execute_precheck, write_precheck_manifest
from agent.protocol import PrecheckEnvelope
from agent.run import Controller, ExecutionContext
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.precheck_contract import validate_result
from audit.precheck_sources import l1_criteria, l1_spec, required_structure, structure_source
from audit.standard_suites import standard_suites
from documents.model_access import ModelAccess, model_access_scope
from openpyxl import Workbook
from pydantic import SecretStr

CODE = b"""def check(context):
    raise AssertionError("Formal audit is not free preparation")
def precheck(context):
    from audit.precheck_contract import fact
    from documents.model_access import current_model_access, ModelUnavailable
    try:
        current_model_access()
    except ModelUnavailable:
        pass
    else:
        raise AssertionError("A free preparation received a model credential")
    assert context.runtime.allow_model_tools is False
    assert context.group_context.by_doc_type["reference-kind"][0].audit_id == "reference"
    return [fact("X", "defect", "frozen enterprise hook", code="B05")]
"""


def request(code=CODE, suite=None):
    programs = {"checker.py": code} if suite is None else suite.program_files
    rules = {"type": "控制计划", "rules": [{"id": "R", "rule_to_check": "required"}]} if suite is None else json.loads(suite.files["rules.json"])
    key = "custom-suite" if suite is None else suite.suite_key
    workbook = Workbook()
    workbook.active.append(["过程特殊特性清单", "零件编号", "工序编号", "特性描述"])
    workbook.active.append(["P-1", "OP10", "尺寸", "CC"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    sources = {"target": buffer.getvalue(), "reference": b"a reference"}
    inputs = tuple(
        FrozenInput(
            identity,
            hashlib.sha256(data).hexdigest(),
            "target" if identity == "target" else "reference",
            identity + ".xlsx",
            "控制计划" if identity == "target" else "reference-kind",
            "file-" + identity,
        )
        for identity, data in sources.items()
    )
    binding = RuleBinding(key, checker_digest(programs), {"R": "checker:check"} if suite is None else suite.entrypoints)
    target = FrozenTarget("target", "frozen-revision", key, "a" * 64, rules, binding)
    snapshot = ExecutionSnapshot("tenant", "initiator", inputs, (target,), "runtime", "library", {})
    payload = {
        "schema_version": 2,
        "task_id": "task",
        "tenant_id": "tenant",
        "initiating_user_id": "initiator",
        "execution_revision": "preparation",
        "precheck_source": structure_source(),
        "files": [],
    }
    entry = rules.get("preset_key") or ""
    for source in inputs:
        execution = {"mode": "checker_core", "suite_revision_id": target.suite_revision_id, "suite_digest": target.suite_digest, "binding": binding.to_dict()} if source.role == "target" else None
        payload["files"].append(
            {
                "file": {"id": source.file_version_id},
                "selection": "selected",
                "role": source.role,
                "rule": {"id": "rule", "body": rules} if source.role == "target" else None,
                "rule_execution": execution,
                "suite_target": target.to_dict() if source.role == "target" else None,
                "precheck_spec": l1_spec(entry),
                "required_structure": list(required_structure(entry)),
                "precheck_criteria": list(l1_criteria(entry)),
            }
        )
    return payload, snapshot, sources, {binding.checker_digest: programs}


class ControlledGuest:
    def run(self, spec, inputs, work, deadline, cancelled):
        assert isinstance(spec, PrecheckJobSpec)
        # Intentionally no credentials directory: the entrypoint must not read it.
        run_entrypoint(inputs, work, work / "no-model-grants")


def test_frozen_enterprise_hook_runs_free_and_formal_check_never_runs():
    payload, snapshot, sources, programs = request()
    with model_access_scope(ModelAccess(SecretStr("host-secret-must-not-be-visible"))):
        result = run_precheck(payload, snapshot, sources, programs)
    assert result["files"][0]["facts"][0]["message"] == "frozen enterprise hook"
    assert result["files"][0]["facts"][0]["status"] == "defect"
    assert all(item["certified"] is False for row in result["files"] for item in row["facts"])
    assert result["files"][1]["facts"][0]["status"] == "handoff"
    assert "host-secret" not in json.dumps(result)


def test_absent_precheck_does_not_fall_back_to_formal_ng_or_a_legacy_class():
    result = run_precheck(*request(b'def check(context): raise AssertionError("must never execute")\n'))
    assert result["files"][0]["facts"][0]["status"] == "not_checked"
    assert not any(item["status"] in {"satisfied", "defect"} for item in result["files"][0]["facts"])


def test_guest_manifest_has_no_grant_and_matches_frozen_program(tmp_path):
    args = request()
    result = execute_precheck(ControlledGuest(), *args, ExecutionContext())
    assert result == run_precheck(*args)
    payload, snapshot, sources, programs = args
    inputs = tmp_path / "inputs"
    write_precheck_manifest(inputs, payload, snapshot, sources, programs)
    manifest = json.loads((inputs / "manifest.json").read_text())
    assert manifest["operation"] == "execute-precheck" and manifest["snapshot"]["model_identity"] == {}
    manifest["payload"]["task_id"] = "another task"
    (inputs / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="identity_mismatch"):
        run_entrypoint(inputs, tmp_path, tmp_path / "absent")


def test_modified_code_or_original_rejected_before_import():
    payload, snapshot, sources, programs = request()
    programs[next(iter(programs))]["checker.py"] += b"\nraise AssertionError('must not import')\n"
    with pytest.raises(ValueError, match="program_digest"):
        run_precheck(payload, snapshot, sources, programs)
    payload, snapshot, sources, programs = request()
    sources["reference"] = b"replaced"
    with pytest.raises(ValueError, match="source_digest"):
        run_precheck(payload, snapshot, sources, programs)


@pytest.mark.parametrize("key", ["UAES:U12", "理想:L11", "蔚来:N03", "蔚来:N04"])
def test_standard_hook_keeps_preparation_facts_and_coverage(key):
    suite = next(suite for suite in standard_suites() if suite.suite_key == key)
    result = run_precheck(*request(suite=suite))
    row = result["files"][0]
    assert row["facts"] and row["coverage"]["expected"] > 0
    assert all(item["certified"] is False for item in row["facts"])
    assert all(item["status"] in {"satisfied", "defect", "unknown", "not_checked", "error", "handoff", "not_applicable"} for item in row["facts"])


def test_guest_cannot_self_certify_or_name_another_result():
    payload, snapshot, sources, programs = request()
    result = run_precheck(payload, snapshot, sources, programs)
    for key, value in [("file_version_id", "foreign"), ("suite_execution", {}), ("rule_version_id", "other-rule")]:
        changed = deepcopy(result)
        changed["files"][0][key] = value
        with pytest.raises(ValueError, match="identity_mismatch"):
            validate_result(payload, changed)
    changed = deepcopy(result)
    changed["files"][0]["facts"][0]["certified"] = True
    with pytest.raises(ValueError, match="uncertified"):
        validate_result(payload, changed)


def test_identity_rpc_rejects_worker_code_and_paths_before_dispatch():
    seen = []
    controller = Controller(SimpleNamespace(), lambda *args: None, precheck_handler=lambda identity, context: seen.append(identity) or {"files": []})
    raw = {"op": "precheck", "job_id": str(uuid4()), "tenant_id": str(uuid4()), "attempt": 1, "lease_owner": "worker"}
    assert controller.handle_raw(raw)["op"] == "precheck_result"
    assert seen == [PrecheckEnvelope.model_validate(raw)]
    for forbidden in ["programs", "sources", "host_path", "payload", "initiating_user_id"]:
        assert controller.handle_raw({**raw, forbidden: "injected"}) == {"op": "error", "code": "deep_protocol_invalid"}
    assert len(seen) == 1


def test_container_never_issues_model_grant_or_paid_receipt_for_precheck(tmp_path, monkeypatch):
    import threading
    import time

    from agent.execution.isolation.container import ContainerRuntime

    runtime = object.__new__(ContainerRuntime)
    runtime.slot = threading.Lock()
    runtime.degraded = False
    runtime.journal, runtime.attempts = tmp_path / "journal", tmp_path / "attempts"
    run = tmp_path / "run"
    run.mkdir()
    calls = []
    monkeypatch.setattr(runtime, "_new_run_dir", lambda *args: run)
    monkeypatch.setattr(runtime, "_execute", lambda *args: calls.append("execute"))
    monkeypatch.setattr(runtime, "_export", lambda root, work, model_operation, *args, **kwargs: calls.append(("export", model_operation)))

    def forbidden(*args, **kwargs):
        raise AssertionError("free preparation must not touch grants or usage")

    monkeypatch.setattr(runtime, "_write_grant", forbidden)
    monkeypatch.setattr(runtime, "_seal_usage", forbidden)
    runtime.run(PrecheckJobSpec.for_payload(request()[0]), tmp_path, tmp_path, time.monotonic() + 30, threading.Event())
    assert calls == ["execute", ("export", False)]
    assert not runtime.attempts.exists() and not runtime.journal.exists()


def test_hook_failure_is_visible_and_never_leaks_exception_data():
    result = run_precheck(*request(b'def precheck(context): raise RuntimeError("private-original-or-secret")\n'))
    assert result["files"][0]["facts"][0]["status"] == "error"
    assert "private-original" not in json.dumps(result)
    assert result["files"][1]["facts"][0]["status"] == "handoff"


def test_unsupported_placeholder_is_never_imported():
    from dataclasses import replace

    payload, snapshot, sources, programs = request(b'raise AssertionError("placeholder must not execute")\n')
    target = replace(snapshot.targets[0], binding=replace(snapshot.targets[0].binding, entrypoints={}))
    snapshot = replace(snapshot, targets=(target,))
    payload["files"][0]["suite_target"] = target.to_dict()
    payload["files"][0]["rule_execution"]["binding"] = target.binding.to_dict()
    result = run_precheck(payload, snapshot, sources, programs)
    assert result["files"][0]["facts"][0]["status"] == "not_checked"


def test_suite_customer_conflict_does_not_depend_on_retired_profile_metadata():
    payload, snapshot, _, _ = request()
    payload["files"][0]["rule"]["body"]["customers"] = ["customer-a"]
    other = deepcopy(payload["files"][0])
    other["file"]["id"] = "another-target"
    other["rule"]["body"]["customers"] = ["customer-b"]
    payload["files"].append(other)
    # No original/program dictionaries are supplied. A conflict must be detected
    # before materializing or pairing originals, even without a legacy profile.
    result = run_precheck(payload, snapshot, None, None)
    assert all(row["facts"][0]["code"] == "B03" for row in result["files"])
    assert not any(item["certified"] for row in result["files"] for item in row["facts"])
