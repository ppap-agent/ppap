"""Manifest transport preserves explicit empty source versus new generation."""

import hashlib
from uuid import UUID

import pytest
from agent.checker_development import FrozenDevelopmentInput
from agent.checker_program import CheckerManifest, run_adapt_manifest, write_manifest
from agent.checker_spec import CheckerJobSpec
from agent.execution.checker_guest import CheckerProgramError
from audit.contracts import AuditInput, RuleInput
from openpyxl import Workbook


@pytest.mark.parametrize("selected", [False, True])
def test_manifest_does_not_turn_explicit_empty_selected_source_into_generation(tmp_path, monkeypatch, selected):
    path = tmp_path / "sample.xlsx"
    book = Workbook()
    book.active.append(["value", 5])
    book.save(path)
    book.close()
    raw = path.read_bytes()
    audit = AuditInput(audit_id="original", file_url=str(path))
    rule = RuleInput(rule_id="rule", rules=[{"id": "R1", "rule_to_check": "value"}])
    inputs = FrozenDevelopmentInput(audit, rule, raw)
    spec = CheckerJobSpec(execution_id=UUID(int=0), operation="adapt-checker", input_digest=inputs.digest, executor_revision="test")
    spec = spec.model_copy(update={"execution_id": spec.expected_execution_id()})
    manifest = CheckerManifest(
        operation="adapt-checker",
        role="target",
        spec=spec,
        document=path.name,
        audit_id=audit.audit_id,
        rule=rule.model_dump(mode="json"),
        file_sha256=hashlib.sha256(raw).hexdigest(),
        source_files={} if selected else None,
    )
    write_manifest(tmp_path / "inputs", manifest, raw)
    references = []
    monkeypatch.setattr("agent.execution.checker_guest.task_reference", lambda *args: references.append("reference"))

    class Stop(Exception):
        pass

    class Runner:
        def run(self, *args, **kwargs):
            raise Stop()

    with pytest.raises(CheckerProgramError if selected else Stop):
        run_adapt_manifest(tmp_path / "inputs", tmp_path / "work", Runner(), 10**12)
    assert references == ([] if selected else ["reference"])
