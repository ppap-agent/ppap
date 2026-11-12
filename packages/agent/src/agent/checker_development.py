"""One host-side development loop: adapt a checker for one frozen target, then run it.

Only the job's target and its related sources cross the guest boundary.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal, Protocol

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


def effective_rule_digest(rule: RuleInput) -> str:
    """A new rule row ID is not a semantic change; text and appendices are."""
    return hashlib.sha256(encoded(rule.model_dump(mode="json", exclude={"rule_id"}))).hexdigest()


@dataclass(frozen=True)
class DevelopmentRequest:
    target: FrozenDevelopmentInput
    executor_revision: str
    initiating_user_id: str
    prior: CheckerArtifact | SuiteCheckerProgram | None = None
    fixed: CheckerArtifact | SuiteCheckerProgram | None = None
    feedback: str | None = None
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
    error_code: str | None = None
    usage_uncertain: bool = False
    usage_source: Literal["unknown", "provider", "controlled"] = "unknown"
    code_changed: bool | None = None

    def receipt(self) -> dict:
        return {
            "artifact": self.artifact.model_dump(mode="json") if self.artifact else None,
            "code_changed": self.code_changed,
            "results": [row.model_dump(mode="json") for row in self.results],
            "trials": self.trials,
            "usage": self.usage,
            "error_code": self.error_code,
            "usage_uncertain": self.usage_uncertain,
            "usage_source": self.usage_source,
        }


def _validate(request: DevelopmentRequest) -> None:
    if not request.executor_revision:
        raise ValueError("development requires the executor revision")
    if request.feedback and len(request.feedback) > 8000:
        raise ValueError("development feedback is too large")
    if not request.allow_model_calls and request.fixed is None:
        raise ValueError("model-free development requires a fixed artifact")
    if request.target.group_context is not None:
        contextual = request.target.group_context.rules_by_id.get(request.target.audit_file.audit_id)
        if contextual is None or effective_rule_digest(contextual) != effective_rule_digest(request.target.rule):
            raise ValueError("development group context has different effective rules")


def develop_and_run(request: DevelopmentRequest, job: DevelopmentJob) -> DevelopmentOutcome:
    """Make at most one adaptation request; uncertain provider work is not retried.

    The caller owns durable request identity, authorization and cancellation.
    This function does not charge audit points or publish a Suite default.
    """
    _validate(request)
    usage = dict.fromkeys(("input_tokens", "output_tokens", "model_requests"), 0)
    outcome = DevelopmentOutcome(request.fixed, [], [], usage)
    try:
        if outcome.artifact is None:
            adapted = job(request.target, operation="adapt-checker", prior=request.prior, feedback=request.feedback or None)
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
    outcome.code_changed = request.prior is None or outcome.artifact.files != request.prior.files
    return outcome
