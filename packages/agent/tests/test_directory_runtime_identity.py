"""Frozen programs execute by exact digest without invoking historical routing."""

import hashlib

import pytest
from agent.execution.audit_runtime import AuditRunError, run_audit, run_audit_process
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest


def _program(status):
    return {
        "checker.py": f'''from audit.contracts import AuditResultItem, AuditEvidence, EvidenceSource
class Check:
    rule_item = type("Rule", (), {{"id": "R1"}})()
    def check(self):
        return AuditResultItem(classification="rule", item="R1", clause_id="R1", priority="P0", status="{status}", suggestions="frozen-{status}",
            evidence=AuditEvidence(required="structure", used="structure", source_format="txt", prepared_format="txt",
                sources=(EvidenceSource(kind="structure", locator='{{"kind":"text_line","line":1}}', excerpt="5"),)))
def create_audit(audit_file, rule, group_context=None):
    return type("Audit", (), {{"checks": [Check()]}})()
'''.encode()
    }


def _snapshot(revision, files, *, entry="checker:create_audit"):
    original = b"5\n"
    digest = checker_digest(files)
    target = FrozenTarget("original", revision, "UAES:U08", "a" * 64, {"rules": [{"id": "R1", "rule_to_check": "value"}]}, RuleBinding("UAES:U08", digest, {"R1": entry}))
    return ExecutionSnapshot("tenant", "user", (FrozenInput("original", hashlib.sha256(original).hexdigest(), "target", "original.txt"),), (target,), "executor", "library", {}), {"original": original}


def test_same_business_key_does_not_replace_the_frozen_program():
    older, newer = _program("NG"), _program("PASS")
    first, inputs = _snapshot("v5-revision", older)
    second, _ = _snapshot("v6-revision", newer)
    frozen = {checker_digest(older): older}
    result = run_audit_process(first, inputs, frozen)
    assert [(row["status"], row["suggestions"]) for row in result["findings"]] == [("NG", "frozen-NG")]
    assert run_audit_process(second, inputs, {checker_digest(newer): newer})["findings"][0]["status"] == "PASS"
    repeated = run_audit_process(first, inputs, frozen)
    assert repeated["findings"] == result["findings"]
    assert repeated["coverage"] == result["coverage"]


@pytest.mark.parametrize("fault", ["missing-program", "missing-entry", "corrupt-program"])
def test_a_failed_selected_program_never_uses_another_available_program(fault):
    selected, other = _program("NG"), _program("PASS")
    snapshot, inputs = _snapshot("selected-revision", selected, entry="checker:absent" if fault == "missing-entry" else "checker:create_audit")
    available = {checker_digest(other): other}
    if fault == "missing-entry":
        available = {checker_digest(selected): selected}
    elif fault == "corrupt-program":
        available[checker_digest(selected)] = {"checker.py": b"invalid Python!"}
    try:
        result = run_audit(snapshot, inputs, available)
    except AuditRunError as error:
        assert str(error) == ("checker_entrypoint_missing" if fault == "missing-entry" else "checker_program_digest_mismatch")
        return  # Frozen program closure rejects unrelated replacement bytes.
    assert not any(row["status"] == "PASS" for row in result["findings"])
    assert result["diagnostics"] or result["findings"]
