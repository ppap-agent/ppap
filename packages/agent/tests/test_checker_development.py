"""Real fixed execution with a controlled author; no paid model quality claim."""

import hashlib
import json
from dataclasses import replace
from uuid import UUID

import pytest
from agent.checker_development import DevelopmentRequest, FrozenDevelopmentInput, OracleSample, develop_and_run
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


def request(tmp_path, *, oracle=True):
    target = source(tmp_path, "target", 5)
    prior = CheckerArtifact(input_digest="f" * 64, executor_revision="old", entrypoint="checker:create_audit", files={"checker.py": PROGRAM.replace("value >= 10", "value >= 0").encode()})
    samples = tuple(
        OracleSample(f"case-{i}" if i != 4 else "hidden-sentinel", i, source(tmp_path, f"sample-{i}", value), {"R1": expected}, "reviewer")
        for i, (value, expected) in enumerate([(4, "NG"), (15, "PASS"), (16, "PASS"), (6, "NG")], 1)
    )
    return DevelopmentRequest(
        target=target,
        executor_revision="revision",
        initiating_user_id="reviewer",
        prior=prior,
        oracle=samples if oracle else (),
        oracle_revision="oracle-1" if oracle else None,
        suite_revision_id="suite-1",
        suite_digest="a" * 64,
    )


def test_existing_suite_binding_is_preserved_in_trials_and_execution_identity(tmp_path):
    from agent.checker_development import _trial_artifact
    from agent.checker_spec import SuiteCheckerProgram
    from agent.core_program import AuditJobSpec, development_snapshot
    from agent.protocol import ExecuteRequest
    from audit.checker_core import RuleBinding, checker_digest

    inputs = source(tmp_path, "original", 12)
    files = {"checker.py": b"def first(context): return None\ndef second(context): return None\n", "resources.py": b"LIMIT = 10\n"}
    binding = RuleBinding("tenant-suite", checker_digest(files), {"R1": "checker:first"})
    program = SuiteCheckerProgram(input_digest=inputs.digest, executor_revision="revision", files=files, binding=binding)
    assert SuiteCheckerProgram.model_validate_json(program.model_dump_json()) == program
    trial_input = source(tmp_path, "new-original", 8)
    trial = _trial_artifact(program, trial_input, "revision")
    assert isinstance(trial, SuiteCheckerProgram)
    assert trial.input_digest == trial_input.digest != program.input_digest
    assert trial.binding == binding and trial.files == files
    with pytest.raises(ValueError, match="digest mismatch"):
        SuiteCheckerProgram(input_digest=inputs.digest, executor_revision="revision", files={"checker.py": files["checker.py"]}, binding=binding)
    # Host-only direct bindings can never be smuggled into a generated factory.
    with pytest.raises(ValueError):
        CheckerArtifact.model_validate({**program.model_dump(), "entrypoint": "checker:create_audit"})
    with pytest.raises(ValueError):
        CheckerArtifact(input_digest=inputs.digest, executor_revision="revision", files=files, entrypoint="checker:first")
    spec = ExecuteRequest(protocol_revision="protocol", executor="ppap-deep", executor_revision="revision", round_id="round", tenant_id="tenant", initiating_user_id="user", files=[])
    first, _ = development_snapshot(inputs, program, spec)
    changed = SuiteCheckerProgram(input_digest=inputs.digest, executor_revision="revision", files=files, binding=replace(binding, entrypoints={"R1": "checker:second"}))
    second, _ = development_snapshot(inputs, changed, spec)
    assert first.targets[0].binding == binding
    assert first.input_digest != second.input_digest
    assert AuditJobSpec.from_snapshot(first, allow_model_calls=False).execution_id != AuditJobSpec.from_snapshot(second, allow_model_calls=False).execution_id


def test_deep_and_optimizer_share_real_fixed_program_loop(tmp_path):
    req = request(tmp_path)
    runtime = Runtime(tmp_path / "runtime")
    outcome = develop_and_run(req, runtime)
    assert outcome.error_code is None
    assert outcome.results[0].status == "NG"
    assert outcome.validation.verified
    assert outcome.validation.total_samples == outcome.validation.passed_samples == 4
    assert outcome.validation.cases[0].clauses[0].baseline_actual == "PASS"
    assert outcome.validation.cases[0].clauses[0].actual == "NG"
    assert outcome.validation.cases[-1].split == "holdout"
    assert outcome.usage == {"input_tokens": 12, "output_tokens": 24, "model_requests": 2}
    assert len(runtime.prompts) == 2
    adapt_index = next(i for i, call in enumerate(runtime.calls) if call[0] == "adapt-checker")
    assert all(call[1] != "sample-4" for call in runtime.calls[:adapt_index])
    assert all(call[0] == "execute-checker" for call in runtime.calls[adapt_index + 1 :])
    assert "用户确认的训练样本" in runtime.prompts[0]
    assert "case-1" in runtime.prompts[0]
    # Exactly the same source and request path works without labels, unverified.
    deep = develop_and_run(replace(req, oracle=(), oracle_revision=None), Runtime(tmp_path / "deep"))
    assert deep.results[0].status == "NG"
    assert not deep.validation.verified
    assert deep.validation.reasons == ("no_independent_oracle",)


@pytest.mark.parametrize("status", ["UNABLE", "ERR", "PASS"])
def test_wrong_or_unable_results_cannot_game_negative_samples(tmp_path, status):
    runtime = Runtime(tmp_path / "runtime", program=PROGRAM.replace("'PASS' if value >= 10 else 'NG'", repr(status)))
    outcome = develop_and_run(request(tmp_path), runtime)
    assert not outcome.validation.verified
    assert outcome.validation.cases[0].clauses[0].kind == ("missed_detection" if status == "PASS" else status)


def test_missing_checks_and_fake_locator_never_verify(tmp_path):
    runtime = Runtime(tmp_path / "runtime", program=PROGRAM.replace("checks_performed=1", "checks_performed=0"))
    outcome = develop_and_run(request(tmp_path), runtime)
    assert not outcome.validation.verified
    assert outcome.validation.cases[1].clauses[0].actual == "ERR"
    runtime = Runtime(tmp_path / "fake-locator", program=PROGRAM.replace('"sheet":"Sheet"', '"sheet":"Sheet","file_version_id":"foreign"'))
    outcome = develop_and_run(request(tmp_path), runtime)
    assert outcome.error_code == "deep_output_invalid"
    assert outcome.artifact is None
    assert outcome.results == []
    assert outcome.validation is None


@pytest.mark.parametrize("code", ["deep_model_protocol", "deep_timeout", "deep_budget_exhausted"])
def test_failed_provider_consumption_is_readable_and_not_retried(tmp_path, code):
    runtime = Runtime(tmp_path / "runtime", error=code)
    outcome = develop_and_run(request(tmp_path, oracle=False), runtime)
    assert outcome.error_code == code
    assert outcome.usage_uncertain
    assert outcome.artifact is None
    assert outcome.results == []
    assert len(runtime.calls) == 1


def test_holdout_rejects_overlap_with_target_or_training_before_model_work(tmp_path):
    req = request(tmp_path)
    overlap = replace(req.oracle[-1], inputs=req.target)
    runtime = Runtime(tmp_path / "runtime")
    with pytest.raises(ValueError, match="exposed"):
        develop_and_run(replace(req, oracle=(*req.oracle[:-1], overlap)), runtime)
    assert runtime.calls == []


def test_too_few_samples_and_frozen_replay_remain_unverified(tmp_path):
    req = request(tmp_path)
    runtime = Runtime(tmp_path / "runtime")
    outcome = develop_and_run(replace(req, oracle=req.oracle[:3]), runtime)
    assert not outcome.validation.verified
    assert outcome.validation.reasons == ("too_few_samples",)
    replay_runtime = Runtime(tmp_path / "replay")
    replay = develop_and_run(replace(req, oracle=(), oracle_revision=None, fixed=outcome.artifact), replay_runtime)
    assert len(replay_runtime.calls) == 1
    assert replay_runtime.calls[0][0] == "execute-checker"
    assert replay.results == outcome.results
    assert replay.usage == {"input_tokens": 0, "output_tokens": 0, "model_requests": 0}


def test_model_free_request_cannot_silently_start_adaptation(tmp_path):
    runtime = Runtime(tmp_path / "runtime")
    with pytest.raises(ValueError, match="fixed artifact"):
        develop_and_run(replace(request(tmp_path), allow_model_calls=False), runtime)
    assert runtime.calls == []


def test_combined_user_feedback_and_training_remain_bounded(tmp_path):
    runtime = Runtime(tmp_path / "runtime")
    outcome = develop_and_run(replace(request(tmp_path), feedback="confirmed note " * 530), runtime)
    assert outcome.validation.verified
    feedback = next(call[2] for call in runtime.calls if call[0] == "adapt-checker")
    assert len(feedback) <= 8000
    assert "case-1" in feedback and "hidden-sentinel" not in feedback


def test_partial_oracle_labels_do_not_verify_an_entire_suite(tmp_path):
    req = request(tmp_path)
    rule = req.target.rule.model_copy(update={"rules": [*req.target.rule.rules, {"id": "R2", "category": "threshold", "rule_to_check": "second"}]})
    req = replace(req, target=replace(req.target, rule=rule), oracle=tuple(replace(sample, inputs=replace(sample.inputs, rule=rule)) for sample in req.oracle))
    outcome = develop_and_run(req, Runtime(tmp_path / "runtime"))
    assert not outcome.validation.verified
    assert "oracle_clause_coverage_incomplete" in outcome.validation.reasons
    assert all(case.coverage for case in outcome.validation.cases)


@pytest.mark.parametrize("change", ["threshold", "appendix", "order"])
def test_same_clause_ids_cannot_hide_old_rule_semantics_in_trials(tmp_path, change):
    req = request(tmp_path)
    if change == "threshold":
        modified = req.target.rule.model_copy(update={"rules": [{**req.target.rule.rules[0], "rule_to_check": "A1至少0"}]})
    elif change == "appendix":
        modified = req.target.rule.model_copy(update={"appendix": [{"rule_id": "R1", "interpretation": "Use a different threshold"}]})
    else:
        modified = req.target.rule.model_copy(update={"rules": [*req.target.rule.rules, {"id": "R2", "category": "threshold", "rule_to_check": "second"}]})
        req = replace(req, oracle=tuple(replace(sample, inputs=replace(sample.inputs, rule=modified.model_copy(update={"rules": list(reversed(modified.rules))}))) for sample in req.oracle))
    runtime = Runtime(tmp_path / "runtime")
    with pytest.raises(ValueError, match="effective rules and appendix"):
        develop_and_run(replace(req, target=replace(req.target, rule=modified)), runtime)
    assert runtime.calls == []


def test_old_expected_labels_cannot_verify_after_effective_rule_change(tmp_path):
    from agent.checker_development import effective_rule_digest

    req = request(tmp_path)
    old = req.target.rule.model_copy(update={"rules": [{**req.target.rule.rules[0], "rule_to_check": "A1至少0"}]})
    req = replace(req, oracle=tuple(replace(sample, expected_rule_digest=effective_rule_digest(old)) for sample in req.oracle))
    runtime = Runtime(tmp_path / "runtime")
    outcome = develop_and_run(req, runtime)
    assert all(case.passed for case in outcome.validation.cases)
    assert not outcome.validation.verified
    assert "oracle_rules_changed" in outcome.validation.reasons
    prompt = next(call[2] for call in runtime.calls if call[0] == "adapt-checker")
    assert prompt is None  # Obsolete labels are not generator training truth.


def test_removed_clause_is_a_stale_regression_not_a_success_or_admission_block(tmp_path):
    from agent.checker_development import effective_rule_digest

    req = request(tmp_path)
    previous = effective_rule_digest(req.target.rule)
    new_rule = req.target.rule.model_copy(update={"rules": [{"id": "R2", "category": "threshold", "rule_to_check": "new criterion"}]})
    req = replace(req, target=replace(req.target, rule=new_rule), oracle=tuple(replace(sample, inputs=replace(sample.inputs, rule=new_rule), expected_rule_digest=previous) for sample in req.oracle))
    outcome = develop_and_run(req, Runtime(tmp_path / "runtime"))
    assert outcome.error_code is None
    assert not outcome.validation.verified
    assert "oracle_rules_changed" in outcome.validation.reasons
    assert all(case.clauses[0].actual is None for case in outcome.validation.cases)
    assert any(row.clause_id == "R2" for row in outcome.results)


def test_controlled_grouped_and_individual_repair_keep_the_full_oracle(tmp_path):
    """Controlled cost comparison, not evidence of provider quality or billing."""
    req = request(tmp_path)
    grouped_runtime = Runtime(tmp_path / "grouped")
    grouped = develop_and_run(req, grouped_runtime)
    individual = []
    for selected in req.oracle[:3]:
        runtime = Runtime(tmp_path / selected.case_id)
        outcome = develop_and_run(replace(req, oracle=tuple(replace(sample, repair_selected=sample.case_id == selected.case_id) for sample in req.oracle)), runtime)
        individual.append(outcome)
        prompt = next(call[2] for call in runtime.calls if call[0] == "adapt-checker")
        assert selected.case_id in prompt
        assert all(sample.case_id not in prompt for sample in req.oracle if sample.case_id != selected.case_id)
    assert grouped.validation.passed_samples == grouped.validation.total_samples == 4
    assert all(outcome.validation.passed_samples == outcome.validation.total_samples == 4 for outcome in individual)
    assert grouped.usage["model_requests"] == 2
    assert sum(outcome.usage["model_requests"] for outcome in individual) == 6
    assert grouped.usage["input_tokens"] + grouped.usage["output_tokens"] == 36
    assert sum(outcome.usage["input_tokens"] + outcome.usage["output_tokens"] for outcome in individual) == 108


@pytest.mark.parametrize("text", ["A1至少10", "A1不得小于10"])
def test_rule_change_context_runs_every_sample_and_reports_unchanged_code(tmp_path, text):
    original = request(tmp_path)
    current = replace(original.target, rule=original.target.rule.model_copy(update={"rules": [{**original.target.rule.rules[0], "rule_to_check": text}]}))
    samples = tuple(replace(sample, inputs=replace(sample.inputs, rule=current.rule)) for sample in original.oracle)
    prior = original.prior.model_copy(update={"files": {"checker.py": PROGRAM.encode()}})
    runtime = Runtime(tmp_path / "rule-change")
    outcome = develop_and_run(replace(original, target=current, oracle=samples, prior=prior, rule_changes=({"clause_id": "R1", "kind": "changed", "before": "A1至少0", "after": text},)), runtime)
    assert outcome.error_code is None
    assert outcome.validation.verified
    assert outcome.code_changed is False
    assert outcome.receipt()["code_changed"] is False
    assert len(outcome.validation.cases) == 4
    assert {row.case_id for row in outcome.validation.cases} == {sample.case_id for sample in samples}
    adaptation = next(call for call in runtime.calls if call[0] == "adapt-checker")
    assert "规则原文差异" in adaptation[2] and "A1至少0" in adaptation[2] and text in adaptation[2]


def test_threshold_update_repairs_code_and_regresses_unchanged_clause(tmp_path):
    original = request(tmp_path)
    rule = original.target.rule.model_copy(
        update={
            "rules": [
                *original.target.rule.rules,
                {"id": "R2", "category": "阈值", "rule_to_check": "同一原件 A1 至少 10"},
            ]
        }
    )
    target = replace(original.target, rule=rule)
    samples = tuple(
        replace(sample, inputs=replace(sample.inputs, rule=rule), expected={"R1": sample.expected["R1"], "R2": sample.expected["R1"]}, repair_clause_ids=frozenset({"R2"}))
        for sample in original.oracle
    )
    runtime = Runtime(tmp_path / "threshold-change")
    outcome = develop_and_run(
        replace(
            original,
            target=target,
            oracle=samples,
            rule_changes=(
                {"clause_id": "R1", "kind": "changed", "before": "A1至少0", "after": "A1至少10"},
                {"clause_id": "R2", "kind": "unchanged", "before": "同一原件 A1 至少 10", "after": "同一原件 A1 至少 10"},
            ),
        ),
        runtime,
    )
    assert outcome.validation.verified and outcome.code_changed
    assert len(outcome.validation.cases) == 4
    assert all({row.clause_id for row in case.clauses} == {"R1", "R2"} for case in outcome.validation.cases)
    generation = next(call[2] for call in runtime.calls if call[0] == "adapt-checker")
    training = json.loads(generation.split("用户确认的训练样本（不是模型自评；不得改写预期）：\n")[1])
    assert all(set(row["expected"]) == set(row["actual"]) == {"R2"} for row in training)
