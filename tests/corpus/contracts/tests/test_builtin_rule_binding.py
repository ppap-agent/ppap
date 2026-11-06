"""Frozen Suite identity replaces inferred builtin inputs and positional coverage."""

from copy import deepcopy
from dataclasses import replace

import pytest
from agent.execution.audit_runtime import run_audit
from audit.audits import RoundInput, RoundWork
from audit.suite_migration import migrated_builtin_suite
from corpus_contracts.core_round import bind_suite, freeze_suite_round, run_suite_round, unsupported_suite


@pytest.mark.parametrize(("profile", "expected"), [(None, "v2"), ("v2", "v2"), ("v3", "v3")])
def test_frozen_rule_identity_profile_and_scope_reach_the_checker_without_changing_the_snapshot(profile, expected):
    body = {
        "kind": "engine",
        "type": "PFMEA",
        "rules": [{"id": "customer-rule", "category": "客户要求", "rule_to_check": "检查原件"}],
        "customers": ["客户甲", "客户乙"],
        "models": ["车型甲"],
        "parts": ["P-001", "P-002"],
    }
    if profile is not None:
        body["rule_profile"] = profile
    original = deepcopy(body)
    item = RoundInput("target", "file", "file-v2", "PFMEA.xlsx", "ready", rule_version_id="confirmed-rules-v4", rule_body=body)
    expected_metadata = {
        "rule_id": "confirmed-rules-v4",
        "rule_profile": expected,
        "preset_key": None,
        "appendix": [],
        "type": "PFMEA",
        "customers": ["客户甲", "客户乙"],
        "models": ["车型甲"],
        "parts": ["P-001", "P-002"],
    }
    program = (
        "def check(context):\n"
        f"    assert context.rule.model_dump(exclude={{'rules'}}) == {expected_metadata!r}\n"
        f"    assert context.rule.rules == {original['rules']!r}\n"
        "    return {'classification': 'metadata', 'item': 'frozen identity', 'priority': 'P0', 'status': 'PASS', "
        "'suggestions': '', 'clause_id': context.clause_id, 'checks_performed': 2}\n"
    ).encode()
    source = unsupported_suite(body)
    suite = replace(source, files={**source.files, "checker.py": program}, entrypoints={"customer-rule": "checker:check"})
    snapshot, programs, _ = freeze_suite_round(RoundWork("round", "tenant", [item]), {"file-v2": b"original"}, suites={"file-v2": suite})
    result = run_audit(snapshot, {"file-v2": b"original"}, programs)
    assert [(row["clause_id"], row["status"], row["checks_performed"]) for row in result["findings"]] == [("customer-rule", "PASS", 2)]
    assert item.rule_body == original
    assert dict(snapshot.targets[0].binding.entrypoints) == {"customer-rule": "checker:check"}


@pytest.mark.parametrize(("field", "value", "error"), [("suite_revision_id", "", "invalid_frozen_target"), ("suite_digest", "", "invalid_frozen_target"), ("rules", {}, "invalid_rule_items")])
def test_a_checker_input_requires_both_a_frozen_rule_version_and_its_snapshot(field, value, error):
    body = {"kind": "engine", "type": "PFMEA", "rules": [{"id": "1", "rule_to_check": "检查原件"}]}
    item = RoundInput("target", "file", "file-v2", "PFMEA.xlsx", "ready", rule_version_id="confirmed-rules-v4", rule_body=body)
    snapshot, _, _ = freeze_suite_round(RoundWork("round", "tenant", [item]), {"file-v2": b"original"}, suites={"file-v2": unsupported_suite(body)})
    with pytest.raises(ValueError, match=f"^{error}$"):
        replace(snapshot.targets[0], **{field: value})


def test_uploaded_text_does_not_change_explicit_checker_selection():
    source = migrated_builtin_suite("v3", "潜在过程失效模式及后果分析")
    body = {"kind": "engine", "type": "PFMEA", "rules": [{"id": "99", "category": "customer-only", "rule_to_check": "arbitrary customer requirement"}]}
    suite = bind_suite(source, body, {"99": "1"})
    before = dict(suite.program_files)
    original_digest = suite.revision_digest
    body["rules"][0]["rule_to_check"] = "A changed customer requirement"
    changed = bind_suite(source, body, {"99": "1"})
    assert changed.program_files == before
    assert changed.revision_digest != original_digest
    for filename in ("PFMEA.xlsx", "PFMEA.pdf", "renamed.bin"):
        target = RoundInput("target", "file", "v1", filename, "ready", rule_version_id="rule1", rule_body=body)
        snapshot, programs, _ = freeze_suite_round(RoundWork("round", "tenant", [target]), {"v1": b"original"}, suites={"v1": changed})
        assert programs[snapshot.targets[0].binding.checker_digest] == before
        assert dict(snapshot.targets[0].binding.entrypoints) == {"99": "checker:check"}
        assert snapshot.targets[0].verification == "unverified"


def test_unsupported_document_type_does_not_execute_customer_rules():
    body = {"kind": "engine", "type": "unknown", "rules": [{"id": "1", "category": "custom", "priority": None, "rule_to_check": "custom", "type_of_rule": "text"}]}
    target = RoundInput("target", "f1", "v1", "unknown.bin", "ready", rule_version_id="unknown-suite", rule_body=body)
    result = run_suite_round(RoundWork("round", "tenant", [target]), {"v1": b"original"}, suites={"v1": unsupported_suite(body)})
    assert result["findings"] == []
    assert result["coverage"]["rule_items"][0]["coverage"] == "unsupported"
    assert result["coverage"]["rule_items"][0]["rule_to_check"] == "custom"
    assert result["coverage"]["rule_items"][0]["reason"]
