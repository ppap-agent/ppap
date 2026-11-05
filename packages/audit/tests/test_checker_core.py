import hashlib
from dataclasses import replace

import pytest
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, ValidationRecord, rule_digest, validate_results


def target(file="file", rules=None):
    rules = rules or {"rules": [{"id": "second", "rule_to_check": "Same text"}, {"id": "first", "rule_to_check": "Same text"}, {"id": "unsupported"}]}
    return FrozenTarget(file, "revision", "cp", "a" * 64, rules, RuleBinding("cp", "b" * 64, {"first": "checker:check", "second": "checker:check"}, rule_digest(rules)), verification="verified")


def inputs():
    return (FrozenInput("file", hashlib.sha256(b"file").hexdigest(), "target", "file.xlsx"), FrozenInput("peer", "c" * 64, "reference", "peer.xlsx"))


def result(clause="first", **kwargs):
    return {"clause_id": clause, "classification": "test", "item": "same text", "priority": "P0", "status": "PASS", "suggestions": "", **kwargs}


def test_reordered_results_and_reused_entrypoint_attribute_by_clause_id():
    output = validate_results([result(), result("second", status="UNABLE", evidence_file_version_ids=["file", "peer"])], target=target(), inputs=inputs())
    assert [(row["clause_id"], row["status"]) for row in output["findings"]] == [("second", "UNABLE"), ("first", "PASS")]
    assert output["coverage"][-1] == {"file_version_id": "file", "clause_id": "unsupported", "coverage": "unsupported"}
    assert output["findings"][0]["evidence_file_version_ids"] == ["file", "peer"]


@pytest.mark.parametrize("rows", [[result("unknown")], [result(), result()], [result(file_version_id="peer")], [result(evidence_file_version_ids=["foreign"])], [result(status="UNKNOWN")]])
def test_invalid_result_cannot_become_a_pass(rows):
    with pytest.raises(ValueError):
        validate_results(rows, target=target(), inputs=inputs())


def test_missing_result_is_err_but_unbound_rule_is_unsupported():
    output = validate_results([], target=target(), inputs=inputs())
    assert [row["status"] for row in output["findings"]] == ["ERR", "ERR"]
    assert len(output["coverage"]) == 3


def test_same_clause_on_two_targets_remains_separate():
    ins = (*inputs(), FrozenInput("another", "d" * 64, "target", "another.xlsx"))
    a = validate_results([result()], target=target(), inputs=ins)
    b = validate_results([result(status="NG")], target=target("another"), inputs=ins)
    assert a["findings"][1]["file_version_id"] == "file"
    assert b["findings"][1]["file_version_id"] == "another"


def test_text_appendix_and_direct_dependency_change_invalidate_verification():
    rules = {"rules": [{"id": "same", "rule_to_check": "old"}]}
    new = {"rules": [{"id": "same", "rule_to_check": "new"}]}
    assert rule_digest(rules) != rule_digest(new) != rule_digest(new, ({"text": "confirmed"},))
    record = ValidationRecord("a" * 64, rule_digest(rules), "b" * 64, {"peer": "c" * 64}, "lib1")
    args = dict(suite_digest="a" * 64, rule_digest=rule_digest(rules), checker_digest="b" * 64, related_suite_digests={"peer": "c" * 64}, library_revision="lib2")
    assert record.status(**args) == {"state": "verified", "samples_not_rerun": True}
    assert record.status(**{**args, "related_suite_digests": {"peer": "d" * 64}})["state"] == "stale"


def test_snapshot_roundtrip_deep_freezes_rules_and_rejects_source_drift():
    rules = {"rules": [{"id": "first", "rule_to_check": "initial"}, {"id": "second"}]}
    frozen = target(rules=rules)
    snapshot = ExecutionSnapshot("tenant", "user", inputs(), (frozen,), "runtime", "library", {"model": "text"}, "round")
    digest = snapshot.input_digest
    rules["rules"][0]["rule_to_check"] = "mutated"
    assert snapshot.input_digest == digest
    assert ExecutionSnapshot.from_dict(snapshot.to_dict()).input_digest == digest
    with pytest.raises(TypeError):
        frozen.rules["rules"][0]["rule_to_check"] = "changed"
    with pytest.raises(ValueError, match="sources_mismatch"):
        snapshot.validate_sources({"file": b"file"})
    with pytest.raises(ValueError, match="digest_mismatch"):
        snapshot.validate_sources({"file": b"drift", "peer": b"peer"})
    with pytest.raises(ValueError, match="model_identity"):
        replace(snapshot, model_identity={"api_key": "not accepted"})


def test_verified_target_rejects_unchanged_id_with_changed_text():
    frozen = target()
    with pytest.raises(ValueError, match="rule_digest"):
        replace(frozen, rules={"rules": [{"id": "first", "rule_to_check": "changed"}, {"id": "second"}]})
