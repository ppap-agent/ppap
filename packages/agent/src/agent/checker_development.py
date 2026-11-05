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
from agent.checker_spec import CheckerArtifact, CheckerProgramBundle
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

    @property
    def holdout(self) -> bool:
        return self.ordinal % 4 == 0


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
    prior: CheckerArtifact | None = None
    fixed: CheckerArtifact | None = None
    feedback: str | None = None
    oracle_revision: str | None = None
    oracle: tuple[OracleSample, ...] = ()


class DevelopmentJob(Protocol):
    def __call__(
        self,
        inputs: FrozenDevelopmentInput,
        *,
        operation: Literal["adapt-checker", "execute-checker"],
        artifact: CheckerArtifact | None = None,
        prior: CheckerArtifact | None = None,
        feedback: str | None = None,
    ) -> CheckerProgramBundle: ...


@dataclass
class DevelopmentOutcome:
    artifact: CheckerArtifact | None
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
        expected_ids = [str(row.get("id") or "") for row in sample.inputs.rule.rules]
        if (
            not sample.expected
            or not set(sample.expected).issubset(expected_ids)
            or len(expected_ids) != len(set(expected_ids))
            or any(value not in {"PASS", "NG", "NA"} for value in sample.expected.values())
        ):
            raise ValueError("oracle expectations must name unique effective clauses")
        if sample.holdout and visible.intersection(sample.inputs.document_digests):
            raise ValueError("holdout document is exposed in development inputs")


def _trial_artifact(source: CheckerArtifact, inputs: FrozenDevelopmentInput, revision: str) -> CheckerArtifact:
    # This is a fresh trial of the same source, never an assertion that it was
    # validated for this input. Every oracle sample is actually executed.
    return CheckerArtifact(input_digest=inputs.digest, executor_revision=revision, entrypoint=source.entrypoint, files=dict(source.files), program_digest=source.program_digest)


def _trial(job: DevelopmentJob, source: CheckerArtifact, inputs: FrozenDevelopmentInput, revision: str) -> tuple[list[AuditResultItem], str | None]:
    try:
        bound = _trial_artifact(source, inputs, revision)
        bundle = job(inputs, operation="execute-checker", artifact=bound)
        return [AuditResultItem.model_validate(row) for row in bundle.results], None
    except DeepError as exc:
        return [], exc.code


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
            training.append({"case_id": sample.case_id, "expected": sample.expected, "actual": {str(row.clause_id): row.status for row in rows}, "error_code": error})
    feedback = request.feedback or ""
    if training:
        feedback += "\n用户确认的训练样本（不是模型自评；不得改写预期）：\n" + encoded(training).decode()
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
        labeled = {clause for sample in request.oracle for clause in sample.expected}
        if not {str(row.get("id") or "") for row in request.target.rule.rules}.issubset(labeled):
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
