"""Real fixed execution with a controlled author; no paid model quality claim."""

import hashlib
from dataclasses import replace
from uuid import UUID

import pytest
from agent.checker_development import DevelopmentRequest, FrozenDevelopmentInput, develop_and_run
from agent.checker_program import CheckerManifest, run_adapt_manifest, run_execute_manifest, write_manifest
from agent.checker_spec import CheckerArtifact, CheckerJobSpec, CheckerProgramBundle
from agent.client import DeepError
from agent.execution.checker_guest import CheckerProgramError
from audit.contracts import AuditInput, RuleInput
from openpyxl import Workbook

PROGRAM = """from openpyxl import load_workbook
from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource

class Check:
    def __init__(self, audit_file, clause):
        self.audit_file = audit_file
        self.clause = clause
    def check(self):
        book = load_workbook(self.audit_file.file_url, read_only=True)
        value = book.active['A1'].value
        book.close()
        return AuditResultItem(
            classification='rule', item='Threshold', priority='P1', clause_id=self.clause['id'],
            status='PASS' if value >= 10 else 'NG', suggestions='Compared original cell', checks_performed=1,
            evidence=AuditEvidence(required='structure', used='structure', source_format='xlsx', prepared_format='xlsx',
                sources=(EvidenceSource(kind='structure', locator='{"kind":"xlsx_cell","sheet":"Sheet","cell":"A1"}'),)))
class TaskAudit:
    def __init__(self, audit_file, rule):
        self.checks = [Check(audit_file, clause) for clause in rule.rules]
def create_audit(audit_file, rule, group_context=None):
    return TaskAudit(audit_file, rule)
"""


def source(tmp_path, name, value):
    path = tmp_path / f"{name}.xlsx"
    book = Workbook()
    book.active["A1"] = value
    book.save(path)
    book.close()
    raw = path.read_bytes()
    target = AuditInput(audit_id=name, file_url=str(path), file_metadata={"file_version_id": name, "sha256": hashlib.sha256(raw).hexdigest()})
    rule = RuleInput(rule_id="rules", rule_profile="v3", type="自定义", rules=[{"id": "R1", "category": "阈值", "rule_to_check": "A1至少10"}])
    return FrozenDevelopmentInput(target, rule, raw)


class Runtime:
    def __init__(self, root, *, program=PROGRAM, error=None):
        self.root, self.program, self.error = root, program, error
        self.calls = []
        self.prompts = []
        self.input_tokens = 12
        self.output_tokens = 24

    def run(self, work, prompt, schema, deadline, *, reasoning_effort):
        self.prompts.append(prompt)
        assert "hidden-sentinel" not in prompt
        return schema(entrypoint="checker:create_audit", files={"checker.py": self.program})

    def __call__(self, inputs, *, operation, artifact=None, prior=None, feedback=None):
        self.calls.append((operation, inputs.audit_file.audit_id, feedback))
        if operation == "adapt-checker" and self.error:
            raise DeepError(self.error)
        job = CheckerJobSpec(execution_id=UUID(int=0), operation=operation, input_digest=inputs.digest, executor_revision="revision")
        job = job.model_copy(update={"execution_id": job.expected_execution_id()})
        manifest = CheckerManifest(
            operation=operation,
            spec=job,
            document="target.xlsx",
            audit_id=inputs.audit_file.audit_id,
            rule=inputs.rule.model_dump(mode="json"),
            file_sha256=hashlib.sha256(inputs.file_bytes).hexdigest(),
            file_version_id=inputs.audit_file.audit_id,
            role="target",
            artifact=artifact,
            prior_files={name: value.decode() for name, value in prior.files.items()} if prior else None,
            development_feedback=feedback,
        )
        folder = self.root / str(len(self.calls))
        write_manifest(folder / "inputs", manifest, inputs.file_bytes)
        try:
            if operation == "adapt-checker":
                assert "hidden-sentinel" not in manifest.model_dump_json()
                run_adapt_manifest(folder / "inputs", folder / "work", self, 10**12)
            else:
                assert feedback is None
                run_execute_manifest(folder / "inputs", folder / "work")
        except CheckerProgramError:
            # Match the production guest fault -> trusted host error boundary.
            raise DeepError("deep_output_invalid") from None
        return CheckerProgramBundle.model_validate_json((folder / "work" / "result" / "bundle.json").read_bytes())


def request(tmp_path):
    target = source(tmp_path, "target", 5)
    prior = CheckerArtifact(input_digest="f" * 64, executor_revision="old", entrypoint="checker:create_audit", files={"checker.py": PROGRAM.replace("value >= 10", "value >= 0").encode()})
    return DevelopmentRequest(target=target, executor_revision="revision", initiating_user_id="reviewer", prior=prior)


def test_adapts_once_then_runs_the_real_fixed_program(tmp_path):
    req = request(tmp_path)
    runtime = Runtime(tmp_path / "runtime")
    outcome = develop_and_run(req, runtime)
    assert outcome.error_code is None
    assert [call[0] for call in runtime.calls] == ["adapt-checker", "execute-checker"]
    assert outcome.code_changed
    assert [row.status for row in outcome.results] == ["NG"]
    replay_runtime = Runtime(tmp_path / "replay")
    replay = develop_and_run(replace(req, fixed=outcome.artifact), replay_runtime)
    assert [call[0] for call in replay_runtime.calls] == ["execute-checker"]
    assert replay.results == outcome.results
    assert replay.usage == {"input_tokens": 0, "output_tokens": 0, "model_requests": 0}


def test_fake_locator_is_not_a_result(tmp_path):
    runtime = Runtime(tmp_path / "fake-locator", program=PROGRAM.replace('"sheet":"Sheet"', '"sheet":"Sheet","file_version_id":"foreign"'))
    outcome = develop_and_run(request(tmp_path), runtime)
    assert outcome.error_code == "deep_output_invalid"
    assert outcome.artifact is None
    assert outcome.results == []


@pytest.mark.parametrize("code", ["deep_model_protocol", "deep_timeout", "deep_budget_exhausted"])
def test_failed_provider_consumption_is_readable_and_not_retried(tmp_path, code):
    runtime = Runtime(tmp_path / "runtime", error=code)
    outcome = develop_and_run(request(tmp_path), runtime)
    assert outcome.error_code == code
    assert outcome.usage_uncertain
    assert outcome.artifact is None
    assert outcome.results == []
    assert len(runtime.calls) == 1


def test_model_free_request_cannot_silently_start_adaptation(tmp_path):
    runtime = Runtime(tmp_path / "runtime")
    with pytest.raises(ValueError, match="fixed artifact"):
        develop_and_run(replace(request(tmp_path), allow_model_calls=False), runtime)
    assert runtime.calls == []
