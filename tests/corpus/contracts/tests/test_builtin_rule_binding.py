"""Frozen Suite identity replaces inferred builtin inputs and positional coverage."""

from copy import deepcopy
from dataclasses import replace

import pytest
from audit.audits import RoundInput, RoundWork
from audit.contracts import RuleInput
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
    suite = unsupported_suite(body)
    snapshot, _, _ = freeze_suite_round(RoundWork("round", "tenant", [item]), {"file-v2": b"original"}, suites={"file-v2": suite})
    target = snapshot.targets[0]
    # This is the same typed RuleInput construction made inside run_audit.
    checker_input = RuleInput.model_validate({**target.to_dict()["rules"], "rule_id": target.suite_revision_id, "appendix": target.to_dict()["appendix"]})
    assert checker_input.model_dump(exclude={"rules"}) == {
        "rule_id": "confirmed-rules-v4",
        "rule_profile": expected,
        "preset_key": None,
        "appendix": [],
        "type": "PFMEA",
        "customers": ["客户甲", "客户乙"],
        "models": ["车型甲"],
        "parts": ["P-001", "P-002"],
    }
    assert checker_input.rules == original["rules"]
    assert item.rule_body == original
    assert target.binding.entrypoints == {}


@pytest.mark.parametrize(("revision", "digest"), [("", "a" * 64), ("confirmed-rules-v4", "")])
def test_a_checker_input_requires_both_a_frozen_rule_version_and_its_snapshot(revision, digest):
    body = {"kind": "engine", "type": "PFMEA", "rules": [{"id": "1", "rule_to_check": "检查原件"}]}
    item = RoundInput("target", "file", "file-v2", "PFMEA.xlsx", "ready", rule_version_id="confirmed-rules-v4", rule_body=body)
    snapshot, _, _ = freeze_suite_round(RoundWork("round", "tenant", [item]), {"file-v2": b"original"}, suites={"file-v2": unsupported_suite(body)})
    with pytest.raises(ValueError, match="^invalid_frozen_target$"):
        replace(snapshot.targets[0], suite_revision_id=revision, suite_digest=digest)


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
