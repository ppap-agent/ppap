"""A rule-plan failure must not discard an independent frozen target.

The Controller, semantic adapter, source parser, guest and output validators are
real. Only model responses and external object storage are controlled. The thin
in-process runtime does not prove container isolation or real model quality.
"""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from ppap_core.audits import DEEP_PROTOCOL_REVISION
from ppap_core.files import ObjectExists, ObjectMissing, ObjectTooLarge
from ppap_deep.auditor import SemanticRuntime, product_response
from ppap_deep.client import DeepError
from ppap_deep.controller import Controller, ExecutionContext
from ppap_deep.protocol import ExecuteRequest, FrozenFile
from ppap_deep.semantic.guest import CompilationReview, Manifest, execute
from ppap_deep.semantic.guest_model import ModelBudgetError, ModelProtocolError
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.protocol import ResultBundle, SubmissionSpec
from ppap_deep.semantic.trace import AuditTrace, QueryProof

ENGINE = "a" * 64
BAD_TARGET = "bad-file-version"
GOOD_TARGET = "good-file-version"
BAD_RULE = "bad-rule-version"
GOOD_RULE = "good-rule-version"


def workbook_bytes(frequency, samples):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "数据"
    sheet.append(["频次", "样本数"])
    sheet.append([frequency, samples])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


class MemoryObjects:
    def __init__(self):
        self.values = {}

    def get(self, key, *, max_bytes=None):
        if key not in self.values:
            raise ObjectMissing()
        data = self.values[key]
        if max_bytes is not None and len(data) > max_bytes:
            raise ObjectTooLarge()
        return data

    def put_exclusive(self, key, body, _content_type):
        if key in self.values:
            raise ObjectExists()
        self.values[key] = body


class ModelResponses:
    """External StageRunner seam; never replaces guest validation or parsing."""

    def __init__(self, broken_first_plan):
        self.failed_plan_versions = {BAD_RULE} if broken_first_plan else set()
        self.invalid_schema_attempts = {}
        self.invalid_source_locator = False
        self.global_fault = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.plan_attempts = {BAD_RULE: 0, GOOD_RULE: 0}
        self.audit_calls = 0
        self.audit_targets = []
        self.incomplete_audit_once = False
        self.after_plan = None
        self.rule_refs = {}

    def run(self, work, _prompt, schema, _deadline, *, reasoning_effort):
        assert reasoning_effort == "xhigh"
        self.input_tokens += 10
        self.output_tokens += 2
        if schema is RulePlan:
            source = json.loads((work / "rule-source.json").read_text())
            clauses = json.loads((work / "clauses.json").read_text())
            version = source["rule_version_id"]
            rule = self.rule_refs[version]
            self.plan_attempts[version] += 1
            if self.global_fault is not None:
                raise self.global_fault
            planned = [
                {
                    "id": clause["id"],
                    "description": clause["rule_to_check"],
                    "source_locator": {"kind": "published_clause", "clause_id": clause["id"]},
                    "scope": "数据工作表第 2 行",
                    "predicate": {"kind": "semantic", "question": clause["rule_to_check"]},
                    "missing_evidence": "UNABLE",
                    "ambiguities": [],
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "原文未规定空正文时的结论。",
                }
                for clause in clauses
            ]
            digest = rule.sha256
            if version in self.failed_plan_versions:
                if self.invalid_source_locator:
                    planned[0]["source_locator"]["clause_id"] = "not-the-confirmed-clause"
                elif self.plan_attempts[version] == 1:
                    digest = "0" * 64
                else:
                    planned.pop()
            if self.plan_attempts[version] in self.invalid_schema_attempts.get(version, set()):
                planned[0]["predicate"] = {"kind": "private-invalid-model-output", "question": "private rejected plan text"}
            if self.after_plan is not None:
                self.after_plan(version, self.plan_attempts[version], _deadline)
            return RulePlan.model_validate({"source_sha256": digest, "plan_revision": rule.plan_revision, "rules": planned})

        assert schema is AuditTrace
        self.audit_calls += 1
        targets = json.loads((work / "targets.json").read_text())
        self.audit_targets.append(targets)
        proofs = []
        for target, version in targets.items():
            prefix = "B" if version == BAD_RULE else "G"
            for index, (cell, status, reason) in enumerate(
                [
                    ("A2", "PASS", "正文频次已填写。"),
                    ("B2", "PASS" if target == BAD_TARGET else "NG", "正文样本数为 3。" if target == BAD_TARGET else "正文样本数为 1，小于 2。"),
                ],
                1,
            ):
                rule_id = f"{prefix}{index}"
                evidence = [{"document_id": target, "locator": {"kind": "xlsx_cell", "sheet": "数据", "cell": cell}}]
                proofs.append(
                    {
                        "kind": "semantic",
                        "audit_id": target,
                        "rule_id": rule_id,
                        "scope_basis": "数据工作表只有第 2 行正文",
                        "records": [{"record_id": "row-2", "role": "applicable", "applicability_basis": "已读取的正文记录", "status": status, "reason": reason, "evidence": evidence}],
                    }
                )
        if self.incomplete_audit_once and self.audit_calls == 1:
            proofs.pop()
        return AuditTrace.model_validate({"proofs": proofs})


class InProcessGuest:
    """Exercise the actual guest, without launching a process or container."""

    def __init__(self, model):
        self.model = model
        self.calls = 0

    def engine_revision(self):
        return ENGINE

    def run(self, submission, inputs, work, deadline, cancelled, *, on_progress):
        assert not cancelled.is_set()
        manifest = Manifest.model_validate_json((inputs / "manifest.json").read_bytes())
        assert manifest.spec == submission
        self.calls += 1
        self.model.rule_refs = {rule.rule_id: rule for rule in submission.rules}
        execute(inputs, work, self.model, deadline)


def frozen_case(broken_first_plan=True, *, shared_bad_rule=False, bad_clause_id="B1"):
    blobs = {BAD_TARGET: workbook_bytes("每班", 3), GOOD_TARGET: workbook_bytes("每批", 1)}
    spec = ExecuteRequest(
        protocol_revision=DEEP_PROTOCOL_REVISION,
        round_id="00000000-0000-4000-8000-000000000001",
        tenant_id="test-tenant",
        job_id="frozen-job",
        attempt=1,
        executor="ppap-deep",
        executor_revision=ENGINE,
        files=[
            FrozenFile(
                file_version_id=target,
                file_id="file-" + target,
                role="target",
                filename=name,
                sha256=hashlib.sha256(blobs[target]).hexdigest(),
                rule_version_id=version,
                rule_body={
                    "kind": "engine",
                    "rule_profile": "v3",
                    "rules": [
                        {"id": bad_clause_id if version == BAD_RULE else prefix + "1", "rule_to_check": "正文每行频次非空，否则不通过。"},
                        {"id": prefix + "2", "rule_to_check": "正文每行样本数至少为 2，否则不通过。"},
                    ],
                },
            )
            for target, version, prefix, name in [(BAD_TARGET, BAD_RULE, "B", "first.xlsx"), (GOOD_TARGET, GOOD_RULE, "G", "independent.xlsx")]
        ],
    )
    if shared_bad_rule:
        shared = spec.files[0].model_copy(deep=True, update={"file_version_id": "shared-bad-version", "file_id": "shared-bad-file", "filename": "shared-first.xlsx"})
        spec.files.insert(1, shared)
        blobs[shared.file_version_id] = blobs[BAD_TARGET]
    original = spec.model_dump(mode="json")
    original_blobs = dict(blobs)
    authorized, reads = [], []

    def authorize(request):
        assert request.model_dump(mode="json") == original
        authorized.append(request.job_id)

    def read(version_id, max_bytes=None):
        reads.append(version_id)
        data = blobs[version_id]
        if max_bytes is not None and len(data) > max_bytes:
            raise DeepError("deep_budget_exhausted")
        return data

    model = ModelResponses(broken_first_plan)
    guest = InProcessGuest(model)
    objects = MemoryObjects()
    controller = Controller(SemanticRuntime(guest, objects), read, executor_revision=ENGINE, authorize=authorize)
    return SimpleNamespace(
        spec=spec, blobs=blobs, original=original, original_blobs=original_blobs, authorized=authorized, reads=reads, model=model, guest=guest, objects=objects, controller=controller
    )


def handle_case(case, context=None):
    try:
        return case.controller.handle(case.spec, context)
    finally:
        assert case.spec.model_dump(mode="json") == case.original
        assert case.blobs == case.original_blobs


def persisted_bundle(case):
    bundle = next(body for key, body in case.objects.values.items() if key.endswith("/bundle.json"))
    submission = next(body for key, body in case.objects.values.items() if key.endswith("/inputs.json"))
    return ResultBundle.model_validate_json(bundle), SubmissionSpec.model_validate_json(submission)


@pytest.mark.parametrize("broken_first_plan", [False, True], ids=["valid-control", "independent-target-survives-plan-failure"])
def test_controller_keeps_all_frozen_clauses_when_one_rule_plan_fails(broken_first_plan):
    case = frozen_case(broken_first_plan)
    spec, model = case.spec, case.model

    result = handle_case(case)

    assert case.authorized == ["frozen-job"]
    assert case.reads == [BAD_TARGET, GOOD_TARGET]
    assert case.guest.calls == 1
    assert result.files == spec.files
    assert result.round_id == spec.round_id
    assert model.audit_calls == 1
    assert model.plan_attempts == {BAD_RULE: 2 if broken_first_plan else 1, GOOD_RULE: 1}
    assert result.usage["input_tokens"] == (40 if broken_first_plan else 30)
    assert result.usage["output_tokens"] == (8 if broken_first_plan else 6)
    by_key = {row.item_key: row for row in result.findings}
    expected = {
        f"{BAD_TARGET}:rule:B1": "ERR" if broken_first_plan else "PASS",
        f"{BAD_TARGET}:rule:B2": "ERR" if broken_first_plan else "PASS",
        f"{GOOD_TARGET}:rule:G1": "PASS",
        f"{GOOD_TARGET}:rule:G2": "NG",
    }
    assert {key: row.status for key, row in by_key.items()} == expected
    assert {row["item_key"]: row["status"] for row in result.coverage.rule_items} == expected
    assert all(row["rule_version_id"] == (BAD_RULE if row["file_version_id"] == BAD_TARGET else GOOD_RULE) for row in result.coverage.rule_items)
    for index, excerpt in [(1, "每批"), (2, "1")]:
        finding = by_key[f"{GOOD_TARGET}:rule:G{index}"]
        assert finding.locator["file_version_id"] == GOOD_TARGET
        assert finding.locator["semantic"]["rule_version_id"] == GOOD_RULE
        assert finding.locator["semantic"]["sources"][0]["excerpt"] == excerpt


@pytest.mark.parametrize(
    "invalid_attempts,invalid_coverage_first,expected_bad",
    [({1}, False, "PASS"), ({1, 2}, False, "ERR"), ({2}, True, "ERR")],
    ids=["malformed-plan-repaired", "malformed-plan-still-invalid", "coverage-repair-has-invalid-schema"],
)
def test_malformed_plan_output_is_repaired_within_the_same_frozen_rule(invalid_attempts, invalid_coverage_first, expected_bad):
    case = frozen_case(broken_first_plan=invalid_coverage_first)
    case.model.invalid_schema_attempts = {BAD_RULE: invalid_attempts}

    result = handle_case(case)

    assert result.files == case.spec.files
    assert [(row.item_key, row.status) for row in result.findings] == [
        (f"{BAD_TARGET}:rule:B1", expected_bad),
        (f"{BAD_TARGET}:rule:B2", expected_bad),
        (f"{GOOD_TARGET}:rule:G1", "PASS"),
        (f"{GOOD_TARGET}:rule:G2", "NG"),
    ]
    assert case.authorized == ["frozen-job"]
    assert case.reads == [BAD_TARGET, GOOD_TARGET]
    assert case.guest.calls == 1
    assert case.model.plan_attempts == {BAD_RULE: 2, GOOD_RULE: 1}
    assert case.model.audit_calls == 1
    assert case.model.audit_targets == [{BAD_TARGET: BAD_RULE, GOOD_TARGET: GOOD_RULE} if expected_bad == "PASS" else {GOOD_TARGET: GOOD_RULE}]
    assert (result.usage["input_tokens"], result.usage["output_tokens"]) == (40, 8)
    bundle, submission = persisted_bundle(case)
    assert set(bundle.failed_rules) == ({BAD_RULE} if expected_bad == "ERR" else set())
    assert set(bundle.plans) == ({GOOD_RULE} if expected_bad == "ERR" else {BAD_RULE, GOOD_RULE})
    if expected_bad == "ERR":
        assert bundle.failed_rules[BAD_RULE].reason == "plan_schema_invalid"
    for artifact in case.objects.values.values():
        assert b"private-invalid-model-output" not in artifact
        assert b"private rejected plan text" not in artifact
    assert product_response(case.spec, bundle, submission) == result


def test_shared_failed_rule_marks_both_targets_err_and_caches_only_the_independent_plan():
    case = frozen_case(shared_bad_rule=True)

    result = handle_case(case)

    assert [(row.item_key, row.status) for row in result.findings] == [
        (f"{BAD_TARGET}:rule:B1", "ERR"),
        (f"{BAD_TARGET}:rule:B2", "ERR"),
        ("shared-bad-version:rule:B1", "ERR"),
        ("shared-bad-version:rule:B2", "ERR"),
        (f"{GOOD_TARGET}:rule:G1", "PASS"),
        (f"{GOOD_TARGET}:rule:G2", "NG"),
    ]
    assert result.files == case.spec.files
    assert result.coverage.checked == ["independent.xlsx"]
    assert [row["coverage"] for row in result.coverage.rule_items] == ["execution_error"] * 4 + ["executed"] * 2
    assert case.authorized == ["frozen-job"]
    assert case.reads == [BAD_TARGET, "shared-bad-version", GOOD_TARGET]
    assert case.guest.calls == 1
    assert case.model.plan_attempts == {BAD_RULE: 2, GOOD_RULE: 1}
    assert case.model.audit_calls == 1
    assert (result.usage["input_tokens"], result.usage["output_tokens"]) == (40, 8)
    bundle, submission = persisted_bundle(case)
    assert set(bundle.failed_rules) == {BAD_RULE}
    assert set(bundle.plans) == {GOOD_RULE}
    failure = bundle.failed_rules[BAD_RULE]
    rule = next(item for item in submission.rules if item.rule_id == BAD_RULE)
    assert (failure.rule_id, failure.source_sha256, failure.plan_revision, failure.engine_revision, failure.stage, failure.reason) == (
        BAD_RULE,
        rule.sha256,
        rule.plan_revision,
        ENGINE,
        "compile_repair_validation",
        "coverage_mismatch",
    )
    cached = [json.loads(body) for key, body in case.objects.values.items() if "/plans/" in key]
    assert len(cached) == 1
    assert cached[0] == bundle.plans[GOOD_RULE].model_dump(mode="json")
    assert not any(rule.plan_revision in key for key in case.objects.values if "/plans/" in key)
    assert product_response(case.spec, bundle, submission) == result


def test_all_failed_plans_return_every_clause_err_without_calling_the_audit_model():
    case = frozen_case()
    case.model.failed_plan_versions.add(GOOD_RULE)

    result = handle_case(case)

    assert [row.item_key for row in result.findings] == [f"{BAD_TARGET}:rule:B1", f"{BAD_TARGET}:rule:B2", f"{GOOD_TARGET}:rule:G1", f"{GOOD_TARGET}:rule:G2"]
    assert [row.status for row in result.findings] == ["ERR"] * 4
    assert [row["coverage"] for row in result.coverage.rule_items] == ["execution_error"] * 4
    assert result.coverage.checked == []
    assert result.coverage.verification is None
    assert len(result.coverage.unchecked) == 2
    assert case.model.plan_attempts == {BAD_RULE: 2, GOOD_RULE: 2}
    assert case.model.audit_calls == 0
    assert case.guest.calls == 1
    assert (result.usage["input_tokens"], result.usage["output_tokens"]) == (40, 8)
    bundle, submission = persisted_bundle(case)
    assert set(bundle.failed_rules) == {BAD_RULE, GOOD_RULE}
    assert bundle.plans == {}
    assert bundle.report.checks == []
    assert bundle.trace.proofs == []
    assert not any("/plans/" in key for key in case.objects.values)
    assert product_response(case.spec, bundle, submission) == result


@pytest.mark.parametrize("clause_id", ["B1", "B" * 129], ids=["ordinary-id", "129-character-id"])
def test_invalid_locator_failure_keeps_the_full_confirmed_clause_id(clause_id):
    case = frozen_case(bad_clause_id=clause_id)
    case.model.invalid_source_locator = True

    result = handle_case(case)

    assert [(row.item_key, row.status) for row in result.findings] == [
        (f"{BAD_TARGET}:rule:{clause_id}", "ERR"),
        (f"{BAD_TARGET}:rule:B2", "ERR"),
        (f"{GOOD_TARGET}:rule:G1", "PASS"),
        (f"{GOOD_TARGET}:rule:G2", "NG"),
    ]
    assert case.model.plan_attempts == {BAD_RULE: 2, GOOD_RULE: 1}
    assert case.model.audit_targets == [{GOOD_TARGET: GOOD_RULE}]
    bundle, submission = persisted_bundle(case)
    failure = bundle.failed_rules[BAD_RULE]
    assert failure.clause_id == clause_id
    assert failure.reason == "source_locator_unresolved"
    assert product_response(case.spec, bundle, submission) == result


def test_audit_repair_keeps_failed_rules_and_repairs_only_the_independent_target():
    case = frozen_case()
    case.model.incomplete_audit_once = True

    result = handle_case(case)

    assert [row.status for row in result.findings] == ["ERR", "ERR", "PASS", "NG"]
    assert len(result.coverage.rule_items) == 4
    assert [row["coverage"] for row in result.coverage.rule_items] == ["execution_error", "execution_error", "executed", "executed"]
    assert case.model.plan_attempts == {BAD_RULE: 2, GOOD_RULE: 1}
    assert case.model.audit_calls == 2
    assert case.model.audit_targets == [{GOOD_TARGET: GOOD_RULE}, {GOOD_TARGET: GOOD_RULE}]
    assert case.guest.calls == 1
    assert (result.usage["input_tokens"], result.usage["output_tokens"]) == (50, 10)
    bundle, submission = persisted_bundle(case)
    assert set(bundle.failed_rules) == {BAD_RULE}
    assert set(bundle.plans) == {GOOD_RULE}
    assert [(check.audit_id, check.rule_id) for check in bundle.report.checks] == [(GOOD_TARGET, "G1"), (GOOD_TARGET, "G2")]
    assert product_response(case.spec, bundle, submission) == result


@pytest.mark.parametrize("fault", ["proof_kind", "unknown_diagnostic"])
def test_trace_derivation_preserves_bounded_repair_and_compilation_review(monkeypatch, fault):
    case = frozen_case()
    original_run = case.model.run
    reviewed = []
    feedback = []

    def run(work, prompt, schema, deadline, *, reasoning_effort):
        if schema is CompilationReview:
            requests = json.loads((work / "review-request.json").read_text())["requests"]
            reviewed.extend(requests)
            return CompilationReview.model_validate(
                {"reviews": [{"rule_file_id": request["rule_file_id"], "clause": request["planned_clause"], "reason": "原编译忠实于有效条款，修正执行记录类型。"} for request in requests]}
            )
        if work.name == "audit-repair":
            feedback.append(json.loads((work / "validation-feedback.json").read_text()))
            previous = json.loads((work / "previous-execution.json").read_text())
            assert set(previous) == {"bindings", "proofs"}
        output = original_run(work, prompt, schema, deadline, reasoning_effort=reasoning_effort)
        if schema is AuditTrace and work.name == "audit":
            proof = output.proofs[0]
            if fault == "proof_kind":
                output.proofs[0] = QueryProof(kind="query", audit_id=proof.audit_id, rule_id=proof.rule_id, binding_id="missing")
            else:
                proof.records[0].status = "UNABLE"
                proof.records[0].reason = "缺少分类映射，无法确认适用性。"
        return output

    monkeypatch.setattr(case.model, "run", run)
    result = handle_case(case)
    assert [row.status for row in result.findings] == ["ERR", "ERR", "PASS", "NG"]
    assert case.model.audit_calls == 2
    assert len(reviewed) == (1 if fault == "proof_kind" else 0)
    assert len(feedback) == 1
    expected_message = "Query proof contradicts" if fault == "proof_kind" else f"UNABLE observation lacks diagnostic: {GOOD_TARGET}/G1/row-2"
    assert expected_message in feedback[0]["message"]
    bundle, submission = persisted_bundle(case)
    assert set(bundle.failed_rules) == {BAD_RULE}
    assert product_response(case.spec, bundle, submission) == result


@pytest.mark.parametrize(
    "tamper",
    ["source_sha256", "plan_revision", "engine_revision", "rule_id", "unknown-clause", "overlap", "missing", "unknown-rule", "forged-check", "forged-proof", "empty-success-report"],
)
def test_product_response_rejects_tampered_failed_rule_identity_or_result_partition(tamper):
    case = frozen_case()
    handle_case(case)
    bundle, submission = persisted_bundle(case)
    original_artifacts = dict(case.objects.values)
    if tamper in {"source_sha256", "plan_revision", "engine_revision", "rule_id"}:
        setattr(bundle.failed_rules[BAD_RULE], tamper, "other-rule" if tamper == "rule_id" else "f" * 64)
    elif tamper == "unknown-clause":
        bundle.failed_rules[BAD_RULE].clause_id = "not-a-frozen-clause"
    elif tamper == "overlap":
        bundle.plans[BAD_RULE] = bundle.plans[GOOD_RULE].model_copy(deep=True)
    elif tamper == "missing":
        bundle.failed_rules.pop(BAD_RULE)
    elif tamper == "unknown-rule":
        bundle.failed_rules["unknown-rule"] = bundle.failed_rules[BAD_RULE].model_copy(update={"rule_id": "unknown-rule"})
    elif tamper == "forged-check":
        bundle.report.checks.append(bundle.report.checks[0].model_copy(update={"audit_id": BAD_TARGET, "rule_id": "B1"}))
    elif tamper == "forged-proof":
        bundle.trace.proofs.append(bundle.trace.proofs[0].model_copy(update={"audit_id": BAD_TARGET, "rule_id": "B1"}))
    else:
        bundle.report.checks.clear()
        bundle.trace.proofs.clear()

    with pytest.raises(ValueError):
        product_response(case.spec, bundle, submission)

    assert case.spec.model_dump(mode="json") == case.original
    assert case.blobs == case.original_blobs
    assert case.objects.values == original_artifacts
    assert case.guest.calls == 1


@pytest.mark.parametrize("fault", ["budget", "timeout", "protocol", "unclassified", "cancel"])
def test_global_model_faults_and_controller_cancellation_do_not_become_partial_results(fault):
    case = frozen_case()
    context = ExecutionContext()
    if fault == "cancel":
        context.cancelled.set()
    else:
        case.model.global_fault = {
            "budget": ModelBudgetError("external budget exhausted"),
            "timeout": TimeoutError("external timeout"),
            "protocol": ModelProtocolError("external model protocol failed"),
            "unclassified": ValueError("unclassified external failure"),
        }[fault]

    # This in-process adapter proves only that guest does not swallow a global
    # fault. ContainerRuntime's production fault-code mapping has its own owner.
    with pytest.raises(DeepError):
        handle_case(case, context)

    assert case.model.audit_calls == 0
    assert case.guest.calls == (0 if fault == "cancel" else 1)
    assert case.model.plan_attempts == {BAD_RULE: 0 if fault == "cancel" else 1, GOOD_RULE: 0}
    assert case.objects.values == {}


@pytest.mark.parametrize("invalid_schema_repair", [False, True], ids=["coverage-error", "schema-error"])
def test_compile_repair_that_returns_at_deadline_cannot_start_an_independent_rule(monkeypatch, invalid_schema_repair):
    case = frozen_case()
    if invalid_schema_repair:
        case.model.invalid_schema_attempts = {BAD_RULE: {2}}
    clock = [100.0]
    monkeypatch.setattr("ppap_deep.semantic.guest.time.monotonic", lambda: clock[0])

    def expire_after_repair(version, attempt, deadline):
        if version == BAD_RULE and attempt == 2:
            clock[0] = deadline

    case.model.after_plan = expire_after_repair

    with pytest.raises(DeepError) as caught:
        handle_case(case)

    assert caught.value.code == "deep_timeout"
    assert case.model.plan_attempts == {BAD_RULE: 2, GOOD_RULE: 0}
    assert case.model.audit_calls == 0
    assert case.guest.calls == 1
    assert case.objects.values == {}
