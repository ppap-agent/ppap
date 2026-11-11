"""Controlled guest-contract tests. OS isolation is owned by test_deep_isolation."""

import hashlib
import io
import json
from copy import deepcopy
from dataclasses import replace
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
from PIL import Image
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
    with model_access_scope(ModelAccess(SecretStr("host-secret-must-not-be-visible"), model="test-model", base_url="https://models.example.test")):
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


def test_element_fact_ids_and_shared_source_keep_distinct_states_and_root_coverage():
    code = b"""def precheck(context):
    from audit.precheck_contract import fact
    return [
        fact("R", "unknown", "Whole criterion remains unknown"),
        {**fact("R:E01", "satisfied", "One element observed"), "source_id": "R"},
        {**fact("R:E02", "defect", "Another element has a defect"), "source_id": "R"},
        {**fact("T:E01", "satisfied", "No root result was produced"), "source_id": "T"},
    ]
"""
    payload, snapshot, sources, programs = request(code)
    payload["files"][0]["precheck_criteria"] = [{"id": name, "criterion": name} for name in ("R", "T")]
    result = execute_precheck(ControlledGuest(), payload, snapshot, sources, programs, ExecutionContext())
    row = result["files"][0]
    facts = {item["id"]: item for item in row["facts"]}
    assert {name: (facts[name]["source_id"], facts[name]["status"]) for name in ("R", "R:E01", "R:E02", "T:E01", "T")} == {
        "R": ("R", "unknown"),
        "R:E01": ("R", "satisfied"),
        "R:E02": ("R", "defect"),
        "T:E01": ("T", "satisfied"),
        "T": ("T", "not_checked"),
    }
    assert row["coverage"]["expected"] == 2
    assert {status: count for status, count in row["coverage"]["by_status"].items() if count} == {"unknown": 1, "not_checked": 1}


def test_duplicate_fact_ids_still_rejected_even_with_different_sources():
    code = b"""def precheck(context):
    from audit.precheck_contract import fact
    return [fact("same", "unknown", "first"), {**fact("same", "satisfied", "second"), "source_id": "another"}]
"""
    with pytest.raises(ValueError, match="duplicate_precheck_fact"):
        run_precheck(*request(code))
    payload, snapshot, sources, programs = request()
    result = run_precheck(payload, snapshot, sources, programs)
    result["files"][0]["facts"].append({**result["files"][0]["facts"][0], "source_id": "another"})
    with pytest.raises(ValueError, match="duplicate_precheck_fact"):
        validate_result(payload, result)


@pytest.mark.parametrize(("key", "value"), [("id", ""), ("id", 1), ("source_id", ""), ("source_id", None), ("source_id", 1)])
def test_result_requires_nonempty_fact_and_source_ids(key, value):
    payload, snapshot, sources, programs = request()
    result = run_precheck(payload, snapshot, sources, programs)
    result["files"][0]["facts"][0][key] = value
    with pytest.raises(ValueError, match="invalid_precheck_fact"):
        validate_result(payload, result)


def test_criterion_id_with_a_different_source_cannot_claim_root_coverage():
    payload, snapshot, sources, programs = request()
    payload["files"][0]["precheck_criteria"] = [{"id": "X", "criterion": "Required structure"}]
    result = run_precheck(payload, snapshot, sources, programs)
    result["files"][0]["facts"][0]["source_id"] = "different-criterion"
    with pytest.raises(ValueError, match="coverage_mismatch"):
        validate_result(payload, result)


def test_all_19_uaes_sources_precheck_together_without_collapsing_measurement_elements(monkeypatch):
    """Controlled originals exercise every UAES hook, not engineering certification."""
    import httpx

    def forbidden(*args, **kwargs):
        raise AssertionError("Free preparation must not make external model requests")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr("httpx2.AsyncClient.send", forbidden)
    payload, snapshot, _, _ = request()
    payload["files"] = []
    inputs, targets, sources, programs = [], [], {}, {}
    suites = [suite for suite in standard_suites() if suite.customer == "UAES"]
    assert len(suites) == 19
    for suite in suites:
        entry = suite.source_entry
        buffer = io.BytesIO()
        if entry == "U13":
            Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
            suffix = ".png"
        else:
            book = Workbook()
            book.active.title = "报告"
            for row in [[entry, suite.document_type], ["客户", "UAES"], ["零件号", "P-1"], ["版本", "A"]]:
                book.active.append(row)
            book.save(buffer)
            book.close()
            suffix = ".xlsx"
        sources[entry] = buffer.getvalue()
        inputs.append(FrozenInput(entry, hashlib.sha256(sources[entry]).hexdigest(), "target", entry + suffix, suite.document_type, "file-" + entry, "UAES"))
        one_payload, one_snapshot, _, one_programs = request(suite=suite)
        target = replace(one_snapshot.targets[0], file_version_id=entry, suite_revision_id="frozen-" + entry, suite_digest=suite.revision_digest)
        source = one_payload["files"][0]
        source["file"]["id"] = entry
        source["suite_target"] = target.to_dict()
        source["rule_execution"].update(suite_revision_id=target.suite_revision_id, suite_digest=target.suite_digest)
        payload["files"].append(source)
        targets.append(target)
        programs.update(one_programs)
    snapshot = replace(snapshot, inputs=tuple(inputs), targets=tuple(targets))
    with model_access_scope(ModelAccess(SecretStr("host-secret-must-not-be-visible"), model="test-model", base_url="https://models.example.test")):
        result = execute_precheck(ControlledGuest(), payload, snapshot, sources, programs, ExecutionContext())
    assert len(result["files"]) == 19
    for source, row in zip(payload["files"], result["files"]):
        facts = {item["id"]: item for item in row["facts"]}
        assert len(facts) == len(row["facts"])
        assert "execution" not in facts, row
        assert all(item["certified"] is False for item in facts.values())
        criteria = [*source["required_structure"], *source["precheck_criteria"]]
        assert row["coverage"]["expected"] == len(criteria)
        assert sum(row["coverage"]["by_status"].values()) == len(criteria)
    measurement = next(row for row in result["files"] if row["file_version_id"] == "U02")
    elements = {item["id"]: item for item in measurement["facts"] if item["source_id"] == "U02:M-02"}
    assert set(elements) == {"U02:M-02", *(f"U02:M-02:E{index:02d}" for index in range(1, 24))}
    assert elements["U02:M-02"]["status"] == "unknown"
    assert elements["U02:M-02:E03"]["status"] == "satisfied"
    assert elements["U02:M-02:E11"]["status"] == "not_checked"
    assert any(item["excerpt"] == "P-1" for item in elements["U02:M-02:E03"]["evidence"]["sources"])
    assert "host-secret" not in json.dumps(result)


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
