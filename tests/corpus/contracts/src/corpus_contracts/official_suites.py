"""Explicit official release bridge: frozen human corpus → actual Core → existing CAS.

No imported proof JSON, pytest exit status, known-gap waiver, or model answer is
release evidence. The local process harness is for reviewed platform code, not
an adversarial-code sandbox or a production execution service.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Literal, cast

from agent.execution.audit_runtime import AuditRunError, run_audit_process
from agent.execution.paddle_bridge import paddle_bridge
from agent.mapping import _checker_finding
from audit.checker_core import ExecutionSnapshot, FrozenTarget, RuleBinding, checker_digest, runtime_revision
from audit.checker_validation import CaseEvaluation, ClauseEvaluation, SuiteValidationReport
from audit.contracts import AuditResultItem, RuleInput
from audit.standard_suites import library_revision
from product.suites import canonical_json, content_digest

from corpus_contracts.official_models import LiveModels
from corpus_contracts.oracle import CASES_PATH, ORACLE_PATH, REPO_ROOT, OracleFile, build_round, load_oracle


@dataclass(frozen=True)
class Sample:
    identity: str
    ordinal: int
    oracle: OracleFile
    snapshot: ExecutionSnapshot
    sources: dict[str, bytes]


@dataclass(frozen=True)
class OfficialPlan:
    context: dict
    oracle_revision: str
    samples: tuple[Sample, ...]
    programs: dict[str, dict[str, bytes]]
    originals: dict[Path, str]
    reasons: tuple[str, ...]
    model_identity: dict[str, str] = field(default_factory=dict)

    def check_sources(self) -> None:
        if any(content_digest(path.read_bytes()) != digest for path, digest in self.originals.items()):
            raise ValueError("official_corpus_changed")


def _semantics(files) -> dict:
    rules = json.loads(files["rules.json"])
    return RuleInput.model_validate({**rules, "appendix": json.loads(files.get("appendix.json", b"[]")), "rule_id": ""}).model_dump(mode="json", exclude={"rule_id"})


def prepare(context: dict, *, cases: Path = CASES_PATH, oracle: Path = ORACLE_PATH, root: Path = REPO_ROOT, model_identity: dict[str, str] | None = None) -> OfficialPlan:
    """Freeze every corpus target judged under this revision's rules; callers cannot select easy cases."""
    suite = context["suite"]
    if suite.revision.tenant_id is not None:
        raise ValueError("official_validation_requires_official_suite")
    tables = {path: content_digest(path.read_bytes()) for path in (cases, oracle)}
    corpus = load_oracle(cases=cases, oracle=oracle, root=root)
    program = {key: value for key, value in suite.files.items() if key in {"checker.py", "resources.py"}}
    digest = checker_digest(program)
    rules = json.loads(suite.files["rules.json"])
    clauses = {row["id"] for row in rules["rules"]}
    binding = RuleBinding(suite.revision.suite_key, digest, rules.get("checker_binding", {}))
    reasons: set[str] = set()
    samples: list[Sample] = []
    originals = dict(tables)
    selected_originals = []
    meaning = _semantics(suite.files)
    other_rules = False
    for case in corpus:
        group = [file for file in case.files if file.role == "target" and file.checker_group == suite.revision.suite_key]
        # The operator chose one exact revision. Labels judged under another
        # generation's rules are neither evidence for it nor a reason to block it.
        selected = [file for file in group if _semantics(file.suite().files) == meaning]
        other_rules = other_rules or len(selected) != len(group)
        if not selected:
            continue
        snapshot, sources, _ = build_round(case)
        for file in case.files:
            digest_seen = content_digest(sources[file.file_id])
            if originals.setdefault(file.path, digest_seen) != digest_seen:
                raise ValueError("official_corpus_changed")
        for file in selected:
            if set(file.expected) != clauses:
                reasons.add("oracle_clause_coverage_incomplete")
            for expected in file.expected.values():
                if expected.status not in {"PASS", "NG", "NA"}:
                    reasons.add("oracle_truth_unresolved")
                # A human-confirmed PASS need not restate the rule in prose.
                # Attribution/date and actual execution evidence remain required.
                if not expected.confirmed_by or not expected.confirmed_on or (expected.status != "PASS" and not expected.manual_reason):
                    reasons.add("independent_human_confirmation_required")
                else:
                    try:
                        date.fromisoformat(expected.confirmed_on)
                    except ValueError:
                        reasons.add("independent_human_confirmation_required")
            target = FrozenTarget(
                file.file_id,
                suite.revision.id,
                suite.revision.suite_key,
                suite.revision.resolved_digest,
                rules,
                binding,
                appendix=tuple(json.loads(suite.files.get("appendix.json", b"[]"))),
                related_suite_digests=context["related_suite_digests"],
            )
            # Demoting another target must retain its actual contextual rules.
            # Direct dependencies use the same official revisions bound in proof,
            # not whatever source implementation the corpus selector resolves.
            inputs = []
            for item, source in zip(snapshot.inputs, case.files, strict=True):
                role: Literal["target", "reference"] = "target" if item.file_version_id == file.file_id else "reference"
                if item.file_version_id == file.file_id:
                    contextual_files, contextual_id = suite.files, suite.revision.id
                elif source.role == "target":
                    dependency = context["related_suites"].get(source.checker_group)
                    if dependency is not None:
                        contextual_files, contextual_id = dependency.files, dependency.revision.id
                        if _semantics(source.suite().files) != _semantics(contextual_files):
                            reasons.add("oracle_related_rules_changed")
                    else:
                        contextual_files, contextual_id = source.suite().files, "sha256:" + source.suite().revision_digest
                else:
                    inputs.append(replace(item, role=role))
                    continue
                inputs.append(
                    replace(
                        item,
                        role=role,
                        rule_revision_id=contextual_id,
                        rules=json.loads(contextual_files["rules.json"]),
                        appendix=tuple(json.loads(contextual_files.get("appendix.json", b"[]"))),
                    )
                )
            frozen = replace(
                snapshot,
                inputs=tuple(inputs),
                targets=(target,),
                initiating_user_id="official-corpus-runner",
                runtime_revision=runtime_revision("official-corpus-v1", library_revision()),
                library_revision=library_revision(),
                model_identity=model_identity or {},
            )
            samples.append(Sample(f"{case.id}/{file.file_id}", len(samples) + 1, file, frozen, sources))
            selected_originals.append(content_digest(sources[file.file_id]))
    if len(samples) < 4:
        reasons.add("insufficient_independent_samples")
    if other_rules and not samples:
        reasons.add("oracle_rules_changed")
    if len(set(selected_originals)) != len(selected_originals):
        reasons.add("duplicate_sample_original")
    training = {item.sha256 for sample in samples if sample.ordinal % 4 for item in sample.snapshot.inputs}
    holdout = {item.sha256 for sample in samples if not sample.ordinal % 4 for item in sample.snapshot.inputs}
    if training & holdout:
        reasons.add("holdout_original_exposed")
    if not clauses or set(binding.entrypoints) != clauses:
        reasons.add("suite_clause_coverage_incomplete")
    # Freeze the independent tables, including their human provenance. Originals
    # and full execution context are additionally bound by each input_digest.
    oracle_revision = content_digest(canonical_json({"cases": tables[cases], "oracle": tables[oracle]}))
    plan = OfficialPlan(context, oracle_revision, tuple(samples), {digest: program}, originals, tuple(sorted(reasons)), dict(model_identity or {}))
    plan.check_sources()
    return plan


def _report(plan: OfficialPlan, cases=(), reasons=()) -> SuiteValidationReport:
    suite = plan.context["suite"]
    return SuiteValidationReport(
        suite_revision_id=suite.revision.id,
        suite_digest=suite.revision.resolved_digest,
        library_revision=library_revision(),
        program_digest=next(iter(plan.programs)),
        related_suite_digests=plan.context["related_suite_digests"],
        related_revision_ids=plan.context["related_revision_ids"],
        oracle_revision=plan.oracle_revision,
        model_identity=plan.model_identity,
        cases=tuple(cases),
        reasons=tuple(reasons),
    )


@contextmanager
def _paddle(sample: Sample) -> Iterator[dict[str, str]]:
    """Host keeps the upstream token; the guest gets one capability for this sample's PDFs."""
    with tempfile.TemporaryDirectory(prefix="ppap-official-paddle-") as work:
        root = Path(work)
        names = {key: f"source-{index}" for index, key in enumerate(sample.sources)}
        for key, name in names.items():
            (root / name).write_bytes(sample.sources[key])
        (root / "manifest.json").write_text(json.dumps({"operation": "execute-audit", "spec": {"allow_model_calls": True}, "source_paths": names}), encoding="utf-8")
        with paddle_bridge(root, time.monotonic() + 600, threading.Event()) as env:
            yield env


def evaluate(plan: OfficialPlan, *, models: LiveModels | None = None, on_progress: Callable[[dict], None] | None = None) -> SuiteValidationReport:
    """Execute fixed code; live grants are explicit and labels never enter the guest."""
    if plan.reasons:
        return _report(plan, reasons=plan.reasons)
    if plan.model_identity != (models.access.identity() if models is not None else {}):
        raise ValueError("official_model_identity_mismatch")
    if on_progress is not None:
        on_progress({"phase": "execution_started", "selected_samples": len(plan.samples), "models": models.receipt() if models else {"mode": "disabled"}})
    with models.session() if models else nullcontext(None) as grant:
        return _evaluate(plan, models=models, grant=grant, on_progress=on_progress)


def _evaluate(plan: OfficialPlan, *, models: LiveModels | None, grant: dict[str, str] | None, on_progress: Callable[[dict], None] | None) -> SuiteValidationReport:
    cases: list[CaseEvaluation] = []
    for sample in plan.samples:
        target = sample.snapshot.targets[0]
        try:
            with _paddle(sample) if models is not None else nullcontext({}) as tool_env:
                result = run_audit_process(sample.snapshot, sample.sources, plan.programs, allow_model_tools=models is not None, model_grant=grant, tool_env=tool_env)
        except AuditRunError:
            # Never promote partial/unknown execution to a successful proof.
            return _report(plan, cases, ("official_execution_incomplete",))
        rows = result["findings"]
        coverage = Counter((row["clause_id"], row["coverage"]) for row in result["coverage"])
        complete = coverage == Counter((clause, "mapped") for clause in sample.oracle.expected)
        actual = {row["clause_id"]: AuditResultItem.model_validate(row) for row in rows}
        facts = {item.file_version_id: (item.filename, item.role) for item in sample.snapshot.inputs}
        evaluations = []
        for clause, expected in sample.oracle.expected.items():
            row = actual.get(clause)
            status, _, incomplete, sources = _checker_finding(row, file_version_id=target.file_version_id, file_facts=facts)
            evaluations.append(
                ClauseEvaluation(
                    clause_id=clause,
                    expected=cast(Literal["PASS", "NG", "NA"], expected.status),
                    actual=status if row is not None else None,
                    evidence_locatable=bool(sources) and not incomplete,
                    checks_performed=max(0, row.checks_performed or 0) if row else 0,
                )
            )
        cases.append(
            CaseEvaluation(
                case_id=sample.identity,
                ordinal=sample.ordinal,
                input_digest=sample.snapshot.input_digest,
                split="holdout" if sample.ordinal % 4 == 0 else "training",
                coverage=complete,
                clauses=tuple(evaluations),
            )
        )
        if models is not None:
            receipt = models.receipt()
            if on_progress is not None:
                on_progress({"phase": "sample_completed", "completed_samples": len(cases), "models": receipt})
            if receipt["usage"]["uncertain"] or receipt["usage"]["budget_exhausted"]:
                return _report(plan, cases, ("official_model_usage_incomplete",))
    plan.check_sources()
    return _report(plan, cases)


def readback(releases, revision_id: str) -> dict:
    context = releases.official_context(revision_id)
    return {"revision_id": revision_id, "head": context["head"], "validation": context["validation"], "report": context["report"]}


def validate(
    releases,
    revision_id: str,
    *,
    record: bool = False,
    expected_validation_revision: int | None = None,
    before_record: Callable[[dict], None] | None = None,
    models: LiveModels | None = None,
    on_progress: Callable[[dict], None] | None = None,
    **paths,
) -> dict:
    context = releases.official_context(revision_id)
    if record and (type(expected_validation_revision) is not int or expected_validation_revision != context["validation"]["write_revision"]):
        raise ValueError("suite_validation_conflict")
    plan = prepare(context, model_identity=models.access.identity() if models else {}, **paths)
    proof = evaluate(plan, models=models, on_progress=on_progress)
    result = {
        "recorded": False,
        "head": context["head"],
        "report": proof.receipt(),
        "selected_samples": len(plan.samples),
        "execution": "not_started_evidence_incomplete" if plan.reasons else "incomplete" if proof.reasons else "completed",
        "models": models.receipt() if models else {"mode": "disabled"},
    }
    if record:
        plan.check_sources()
        # A failed write can have committed. Do not replay; use readback with the
        # proof_digest already printed by the CLI before the write starts.
        if before_record is not None:
            before_record(result)
        result["validation"] = releases.record_official_validation(revision_id, proof, expected_revision=expected_validation_revision)
        result["recorded"] = True
    return result


def publish(releases, revision_id: str, *, expected_revision: int, expected_validation_revision: int, expected_proof_digest: str, **paths) -> dict:
    """Reconcile frozen evidence, then publish only the exact reviewed CAS heads."""
    context = releases.official_context(revision_id)
    if context["report"] is None:
        raise ValueError("official_suite_validation_required")
    proof = SuiteValidationReport.model_validate(context["report"])
    # Publication reads the frozen public identity, not today's credentials,
    # and never starts a gateway or replays a paid validation.
    plan = prepare(context, model_identity=proof.model_identity, **paths)
    if plan.reasons:
        raise ValueError("official_suite_validation_required")
    expected = _report(plan, proof.cases, proof.reasons)
    if (
        not proof.verified
        or proof.proof_digest != expected_proof_digest
        or expected.proof_digest != proof.proof_digest
        or [(row.case_id, row.ordinal, row.input_digest) for row in proof.cases] != [(row.identity, row.ordinal, row.snapshot.input_digest) for row in plan.samples]
    ):
        raise ValueError("official_validation_inputs_changed")
    plan.check_sources()
    return releases.publish_official(
        revision_id,
        expected_revision=expected_revision,
        expected_validation_revision=expected_validation_revision,
        expected_proof_digest=expected_proof_digest,
    )
