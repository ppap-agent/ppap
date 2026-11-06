"""One host-side development loop for Deep and independently evaluated candidates.

Only the job's target and explicitly allowed training feedback cross the guest
boundary. Oracle labels remain in this host module; holdout documents are sent
only to a fresh fixed-execution job, after all model work has ended.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal, Protocol

from audit.checker_validation import CaseEvaluation, ClauseEvaluation, SuiteValidationReport
from audit.contracts import AuditInput, AuditResultItem, GroupContextInput, RuleInput

from agent.checker_program import encoded, input_digest
from agent.checker_spec import CheckerArtifact, CheckerProgramBundle, SuiteCheckerProgram
from agent.client import DeepError


@dataclass(frozen=True)
class FrozenDevelopmentInput:
    audit_file: AuditInput
    rule: RuleInput
    file_bytes: bytes
    role: str = "target"
    group_context: GroupContextInput | None = None
    related_bytes: dict[str, bytes] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        return input_digest(self.audit_file, self.rule, file_sha256=hashlib.sha256(self.file_bytes).hexdigest(), role=self.role, group_context=self.group_context)

    @property
    def document_digests(self) -> set[str]:
        return {hashlib.sha256(raw).hexdigest() for raw in (self.file_bytes, *self.related_bytes.values())}


@dataclass(frozen=True)
class OracleSample:
    """A user-confirmed sample with a persistent, never-renumbered ordinal."""

    case_id: str
    ordinal: int
    inputs: FrozenDevelopmentInput
    expected: dict[str, Literal["PASS", "NG", "NA"]]
    confirmed_by: str
    expected_rule_digest: str | None = None
    repair_selected: bool = True

    @property
    def holdout(self) -> bool:
        return self.ordinal % 4 == 0

    @property
    def expectations_current(self) -> bool:
        return self.expected_rule_digest is None or self.expected_rule_digest == effective_rule_digest(self.inputs.rule)


def effective_rule_digest(rule: RuleInput) -> str:
    """A new rule row ID is not a semantic change; text and appendices are."""
    return hashlib.sha256(encoded(rule.model_dump(mode="json", exclude={"rule_id"}))).hexdigest()


@dataclass(frozen=True)
class DevelopmentRequest:
    target: FrozenDevelopmentInput
    executor_revision: str
    initiating_user_id: str
    suite_revision_id: str | None = None
    suite_digest: str | None = None
    related_revision_ids: tuple[str, ...] = ()
    library_revision: str = ""
    related_suite_digests: dict[str, str] = field(default_factory=dict)
    prior: CheckerArtifact | SuiteCheckerProgram | None = None
    fixed: CheckerArtifact | SuiteCheckerProgram | None = None
    feedback: str | None = None
    oracle_revision: str | None = None
    oracle: tuple[OracleSample, ...] = ()
    allow_model_calls: bool = True


class DevelopmentJob(Protocol):
    def __call__(
        self,
        inputs: FrozenDevelopmentInput,
        *,
        operation: Literal["adapt-checker", "execute-checker"],
        artifact: CheckerArtifact | SuiteCheckerProgram | None = None,
        prior: CheckerArtifact | SuiteCheckerProgram | None = None,
        feedback: str | None = None,
    ) -> CheckerProgramBundle: ...


@dataclass
class DevelopmentOutcome:
    artifact: CheckerArtifact | SuiteCheckerProgram | None
    results: list[AuditResultItem]
    trials: list[str]
    usage: dict[str, int]
    validation: SuiteValidationReport | None = None
    error_code: str | None = None
    usage_uncertain: bool = False
    usage_source: Literal["unknown", "provider", "controlled"] = "unknown"

    def receipt(self) -> dict:
        return {
            "artifact": self.artifact.model_dump(mode="json") if self.artifact else None,
            "results": [row.model_dump(mode="json") for row in self.results],
            "trials": self.trials,
            "usage": self.usage,
            "validation": self.validation.receipt() if self.validation else None,
            "error_code": self.error_code,
            "usage_uncertain": self.usage_uncertain,
            "usage_source": self.usage_source,
        }


def _validate(request: DevelopmentRequest) -> None:
    if not request.executor_revision or request.oracle and not request.initiating_user_id:
        raise ValueError("optimization requires the initiating user and executor revision")
    if request.oracle and not request.oracle_revision:
        raise ValueError("oracle requires its independent revision identity")
    if len(request.oracle) > 64:
        raise ValueError("too many oracle samples for one bounded development request")
    if request.feedback and len(request.feedback) > 8000:
        raise ValueError("development feedback is too large")
    if not request.allow_model_calls and request.fixed is None:
        raise ValueError("model-free development requires a fixed artifact")
    if request.target.group_context is not None:
        contextual = request.target.group_context.rules_by_id.get(request.target.audit_file.audit_id)
        if contextual is None or effective_rule_digest(contextual) != effective_rule_digest(request.target.rule):
            raise ValueError("development group context has different effective rules")
    ids: set[str] = set()
    ordinals: set[int] = set()
    visible = request.target.document_digests
    for sample in request.oracle:
        if not sample.holdout:
            visible.update(sample.inputs.document_digests)
    # Holdout documents may not be target/reference inputs of a development job.
    for sample in request.oracle:
        if not sample.case_id or not sample.confirmed_by or sample.ordinal < 1 or sample.case_id in ids or sample.ordinal in ordinals:
            raise ValueError("oracle samples require unique identity, ordinal and user confirmation")
        ids.add(sample.case_id)
        ordinals.add(sample.ordinal)
        if effective_rule_digest(sample.inputs.rule) != effective_rule_digest(request.target.rule):
            raise ValueError("oracle trials must use the candidate's effective rules and appendix")
        if sample.inputs.group_context is not None:
            contextual = sample.inputs.group_context.rules_by_id.get(sample.inputs.audit_file.audit_id)
            if contextual is None or effective_rule_digest(contextual) != effective_rule_digest(sample.inputs.rule):
                raise ValueError("oracle group context has different effective rules")
        expected_ids = [str(row.get("id") or "") for row in sample.inputs.rule.rules]
        if (
            not sample.expected
            or sample.expectations_current
            and not set(sample.expected).issubset(expected_ids)
            or len(expected_ids) != len(set(expected_ids))
            or any(value not in {"PASS", "NG", "NA"} for value in sample.expected.values())
        ):
            raise ValueError("oracle expectations must name unique effective clauses")
        if sample.holdout and visible.intersection(sample.inputs.document_digests):
            raise ValueError("holdout document is exposed in development inputs")


def _trial_artifact(source: CheckerArtifact | SuiteCheckerProgram, inputs: FrozenDevelopmentInput, revision: str) -> CheckerArtifact | SuiteCheckerProgram:
    # This is a fresh trial of the same source, never an assertion that it was
    # validated for this input. Every oracle sample is actually executed.
    if isinstance(source, SuiteCheckerProgram):
        return SuiteCheckerProgram(input_digest=inputs.digest, executor_revision=revision, binding=source.binding, files=dict(source.files), program_digest=source.program_digest)
    return CheckerArtifact(input_digest=inputs.digest, executor_revision=revision, entrypoint=source.entrypoint, files=dict(source.files), program_digest=source.program_digest)


def _trial(job: DevelopmentJob, source: CheckerArtifact | SuiteCheckerProgram, inputs: FrozenDevelopmentInput, revision: str) -> tuple[list[AuditResultItem], str | None]:
    try:
        bound = _trial_artifact(source, inputs, revision)
        bundle = job(inputs, operation="execute-checker", artifact=bound)
        return [AuditResultItem.model_validate(row) for row in bundle.results], None
    except DeepError as exc:
        return [], exc.code
    except (ValueError, TypeError):
        return [], "deep_output_invalid"


def _evaluate(sample: OracleSample, results: list[AuditResultItem], baseline: list[AuditResultItem], error: str | None) -> CaseEvaluation:
    counts = Counter(str(row.clause_id or "") for row in results)
    actual = {str(row.clause_id): row for row in results}
    from agent.mapping import _checker_finding

    target = sample.inputs.audit_file
    version = str(target.file_metadata.get("file_version_id") or target.audit_id)
    file_facts = {version: (target.file_url, sample.inputs.role)}
    if sample.inputs.group_context is not None:
        for item in sample.inputs.group_context.by_audit_id.values():
            item_version = str(item.file_metadata.get("file_version_id") or item.audit_id)
            file_facts[item_version] = (item.file_url, str(item.file_metadata.get("role") or "reference"))
    previous = {str(row.clause_id): _checker_finding(row, file_version_id=version, file_facts=file_facts)[0] for row in baseline}
    clauses: list[ClauseEvaluation] = []
    for clause_id, expected in sample.expected.items():
        row = actual.get(clause_id)
        status, _, incomplete, sources = _checker_finding(row, file_version_id=version, file_facts=file_facts)
        locatable = bool(sources) and not incomplete
        clauses.append(
            ClauseEvaluation(
                clause_id=clause_id,
                expected=expected,
                actual=status if row is not None and counts[clause_id] == 1 else None,
                evidence_locatable=locatable,
                checks_performed=max(0, row.checks_performed or 0) if row is not None else 0,
                baseline_actual=previous.get(clause_id),
            )
        )
    return CaseEvaluation(
        case_id=sample.case_id,
        ordinal=sample.ordinal,
        input_digest=sample.inputs.digest,
        split="holdout" if sample.holdout else "training",
        coverage=counts == Counter(str(row.get("id") or "") for row in sample.inputs.rule.rules),
        clauses=tuple(clauses),
        error_code=error,
    )


def develop_and_run(request: DevelopmentRequest, job: DevelopmentJob) -> DevelopmentOutcome:
    """Make at most one adaptation request; uncertain provider work is not retried.

    The caller owns durable request identity, authorization and cancellation.
    This function does not charge audit points or publish a Suite default.
    """
    _validate(request)
    usage = dict.fromkeys(("input_tokens", "output_tokens", "model_requests"), 0)
    outcome = DevelopmentOutcome(request.fixed, [], [], usage)
    baseline: dict[str, list[AuditResultItem]] = {}
    training: list[dict] = []
    if request.prior is not None:
        for sample in request.oracle:
            if sample.holdout:
                continue
            rows, error = _trial(job, request.prior, sample.inputs, request.executor_revision)
            baseline[sample.case_id] = rows
            if sample.expectations_current and sample.repair_selected:
                training.append({"case_id": sample.case_id, "expected": sample.expected, "actual": {str(row.clause_id): row.status for row in rows}, "error_code": error})
    feedback = request.feedback or ""
    if training:
        # The generation boundary is bounded even when many long clause IDs or
        # user notes are present. Omitted training rows still run in evaluation.
        heading = "\n用户确认的训练样本（不是模型自评；不得改写预期）：\n"
        available = 8000 - len(heading)
        selected: list[dict] = []
        for training_row in training:
            if len(encoded([*selected, training_row]).decode()) > available:
                break
            selected.append(training_row)
        training_text = encoded(selected).decode()
        feedback = feedback[: available - len(training_text)] + heading + training_text
    try:
        if outcome.artifact is None:
            adapted = job(request.target, operation="adapt-checker", prior=request.prior, feedback=feedback or None)
            for name in usage:
                usage[name] += int(getattr(adapted, name, 0) or 0)
            outcome.artifact = adapted.artifact
            outcome.trials.extend(adapted.observations)
            if outcome.artifact is None:
                raise DeepError("deep_output_invalid")
        executed = job(request.target, operation="execute-checker", artifact=outcome.artifact)
        for name in usage:
            usage[name] += int(getattr(executed, name, 0) or 0)
        outcome.results = [AuditResultItem.model_validate(row) for row in executed.results]
    except DeepError as exc:
        outcome.error_code = exc.code
        outcome.usage_uncertain = exc.code in {"deep_model_protocol", "deep_timeout", "deep_execution_failed", "deep_runtime_unavailable", "deep_budget_exhausted"}
        return outcome
    evaluations: list[CaseEvaluation] = []
    for sample in request.oracle:
        if request.prior is not None and sample.case_id not in baseline:
            baseline[sample.case_id], _ = _trial(job, request.prior, sample.inputs, request.executor_revision)
        rows, error = _trial(job, outcome.artifact, sample.inputs, request.executor_revision)
        evaluations.append(_evaluate(sample, rows, baseline.get(sample.case_id, []), error))
    reasons: list[str] = []
    if not request.oracle:
        reasons.append("no_independent_oracle")
    elif len(request.oracle) < 4 or not any(row.holdout for row in request.oracle):
        reasons.append("too_few_samples")
    if request.oracle:
        if any(not sample.expectations_current for sample in request.oracle):
            reasons.append("oracle_rules_changed")
        if any(set(sample.expected) != {str(row.get("id") or "") for row in sample.inputs.rule.rules} for sample in request.oracle):
            reasons.append("oracle_clause_coverage_incomplete")
    outcome.validation = SuiteValidationReport(
        suite_revision_id=request.suite_revision_id,
        suite_digest=request.suite_digest,
        library_revision=request.library_revision,
        related_suite_digests=request.related_suite_digests,
        program_digest=outcome.artifact.program_digest,
        oracle_revision=request.oracle_revision,
        related_revision_ids=request.related_revision_ids,
        cases=tuple(evaluations),
        reasons=tuple(reasons),
    )
    return outcome
