"""Frozen native clause ownership must survive guest and host validation."""

import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest
from ppap_deep.semantic.document_tools import ReadLimits
from ppap_deep.semantic.evidence import EvidenceBudget, EvidenceLimitError
from ppap_deep.semantic.guest import PlanValidationError, compile_plans, validate_or_repair, validate_plan
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.protocol import DocumentRef, ResultBundle, RuleRef, SubmissionSpec
from ppap_deep.semantic.trace import AuditTrace, derive_report, evaluate_audit
from ppap_deep.semantic.validation import clause_source_error, validate_bundle


def native_case(file_format=".docx"):
    if file_format == ".pdf":
        locators = [{"kind": "pdf_block", "page": 1, "block": block} for block in (2, 3, 4, 1)]
        source = {"tables": [], "pages": [{"page": 1, "blocks": [{"text": text} for text in ("1", "First effective requirement", "Second effective requirement", "First clause context")]}]}
    else:
        positions = [(2, 2), (3, 2), (2, 3), (2, 1)]
        locators = [
            {"kind": "docx_cell", "table": 1, "row": row, "column": column} if file_format == ".docx" else {"kind": "xlsx_cell", "sheet": "Rules", "cell": f"{'ABC'[column - 1]}{row}"}
            for row, column in positions
        ]
        source = {
            "tables": [
                {
                    "id": "table:1" if file_format == ".docx" else "Rules",
                    "rows": 3,
                    "cols": 3,
                    "merges": [],
                    "cells": {
                        cell: {"value": text, "state": "known"}
                        for cell, text in (
                            ("A2", "1"),
                            ("B2", "First effective requirement"),
                            ("B3", "Second effective requirement"),
                            ("C2", "First clause context"),
                        )
                    },
                }
            ]
        }
    first, second, context, ordinal = locators
    source["source"] = {"sha256": "a" * 64, "format": file_format}
    clauses = [
        {"id": "R1", "rule_to_check": "First effective requirement", "source_locators": [first, context]},
        {"id": "R2", "rule_to_check": "Second effective requirement", "source_locators": [second]},
    ]
    rule = RuleRef(rule_id="rule-1", sha256="a" * 64, format=file_format, plan_revision="b" * 64, clauses=clauses)
    plan = RulePlan.model_validate(
        {
            "source_sha256": rule.sha256,
            "plan_revision": rule.plan_revision,
            "ontology": {"entities": [{"id": "document", "grain": "one frozen input document", "attributes": {"id": "string"}, "identity": ["id"]}]},
            "rules": [
                {
                    "id": item["id"],
                    "source_locator": item["source_locators"][0],
                    "program": {"kind": "document", "require": {"kind": "judge", "question": "Does the frozen document meet this requirement?"}},
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "The rule does not authorize an empty scope result.",
                }
                for item in clauses
            ],
        }
    )
    document = {
        "source": {"sha256": "c" * 64, "format": ".xlsx"},
        "tables": [
            {
                "id": "Data",
                "rows": 1,
                "cols": 1,
                "merges": [],
                "cells": {"A1": {"value": "observed", "state": "known"}},
            }
        ],
    }
    spec = SubmissionSpec(
        execution_id=UUID(int=0),
        submission_id=UUID(int=1),
        project_id="tenant",
        documents=[DocumentRef(audit_id="target", sha256="c" * 64, format=".xlsx", role="target")],
        rules=[rule],
        targets={"target": rule.rule_id},
        policy_revision="d" * 64,
        semantic_engine_revision="e" * 64,
    )
    spec = spec.model_copy(update={"execution_id": spec.expected_execution_id()})
    trace = AuditTrace.model_validate({"uses": [{"audit_id": "target", "rule_id": item["id"]} for item in clauses]})
    evaluated = evaluate_audit(trace, {"target": document}, {"target": plan})
    raw_trace = trace.model_dump(mode="json")
    raw_trace["answers"] = [
        {"need_id": need.id, "value": "true", "reason": "Observed original document", "evidence": [{"document_id": "target", "locator": {"kind": "xlsx_cell", "sheet": "Data", "cell": "A1"}}]}
        for need in evaluated.needs
    ]
    trace = AuditTrace.model_validate(raw_trace)
    bundle = ResultBundle(
        execution_id=spec.execution_id,
        semantic_engine_revision=spec.semantic_engine_revision,
        documents={"target": document},
        rule_sources={rule.rule_id: source},
        plans={rule.rule_id: plan},
        trace=trace,
        report=derive_report(trace, {"target": document}, {"target": plan}),
    )
    return SimpleNamespace(rule=rule, source=source, plan=plan, ordinal=ordinal, other_clause=second, context=context, spec=spec, bundle=bundle)


@pytest.mark.parametrize("file_format", [".xlsx", ".xlsm", ".xls", ".docx", ".pdf"])
@pytest.mark.parametrize("selection", ["own", "own_context", "ordinal", "other_clause"])
def test_native_clause_membership_is_shared_by_guest_and_host(file_format, selection):
    case = native_case(file_format)
    selected = case.plan.rules[0].model_dump(mode="json")
    if selection != "own":
        selected["source_locator"] = getattr(case, "context" if selection == "own_context" else selection)
    case.plan.rules[0] = type(case.plan.rules[0]).model_validate(selected)
    if selection in {"own", "own_context"}:
        validate_plan(case.plan, case.rule, case.source, EvidenceBudget())
        # A changed program/source location invalidates prior local answer IDs.
        pending = case.bundle.trace.model_copy(update={"answers": []})
        evaluated = evaluate_audit(pending, case.bundle.documents, {"target": case.plan})
        raw_trace = pending.model_dump(mode="json")
        raw_trace["answers"] = [
            {"need_id": need.id, "value": "true", "reason": "Observed original document", "evidence": [{"document_id": "target", "locator": {"kind": "xlsx_cell", "sheet": "Data", "cell": "A1"}}]}
            for need in evaluated.needs
        ]
        case.bundle.trace = AuditTrace.model_validate(raw_trace)
        case.bundle.report = derive_report(case.bundle.trace, case.bundle.documents, {"target": case.plan})
        assert validate_bundle(case.bundle, case.spec) == set()
    else:
        with pytest.raises(PlanValidationError) as rejected:
            validate_plan(case.plan, case.rule, case.source, EvidenceBudget())
        assert rejected.value.reason == "source_locator_unresolved"
        with pytest.raises(ValueError, match="owned by its frozen clause"):
            validate_bundle(case.bundle, case.spec)


@pytest.mark.parametrize("bad", [None, [], {}, [{"kind": "docx_cell", "table": True, "row": 2, "column": 2}]])
def test_native_source_locations_are_required_typed_lists(bad):
    raw = native_case().rule.model_dump(mode="json")
    raw["clauses"][0]["source_locators"] = bad
    with pytest.raises(ValueError):
        RuleRef.model_validate(raw)


@pytest.mark.parametrize("file_format", [".docx", ".json"])
@pytest.mark.parametrize("body", [{}, {"rule_to_check": None}, {"rule_to_check": ""}, {"rule_to_check": " \n\t"}, {"rule_to_check": 1}, {"rule_to_check": {"text": "requirement"}}])
def test_frozen_clauses_require_actual_nonempty_text(file_format, body):
    raw = native_case().rule.model_dump(mode="json")
    raw.update(format=file_format, rule_version_id="published-v1" if file_format == ".json" else None)
    raw["clauses"][0] = {key: value for key, value in raw["clauses"][0].items() if key != "rule_to_check"} | body
    with pytest.raises(ValueError, match="nonempty rule_to_check"):
        RuleRef.model_validate(raw)


def test_frozen_clause_validation_preserves_complete_text_without_normalization():
    raw = native_case().rule.model_dump(mode="json")
    body = "  A 且 B 通过，否则 NG。\n缺表 NG；映射未知且无独立违规时 UNABLE。\n"
    raw["clauses"][0]["rule_to_check"] = body
    assert RuleRef.model_validate(raw).clauses[0]["rule_to_check"] == body


def test_legacy_source_units_cannot_replace_typed_locations():
    raw = native_case().rule.model_dump(mode="json")
    del raw["clauses"][0]["source_locators"]
    raw["clauses"][0]["source_units"] = ["docx:1:2:2"]
    with pytest.raises(ValueError):
        RuleRef.model_validate(raw)


@pytest.mark.parametrize(
    "replacement",
    [
        {"kind": "xlsx_cell", "sheet": "Rules", "cell": "B2"},
        {"kind": "published_clause", "clause_id": "R1"},
    ],
)
def test_native_locations_must_match_the_rule_file_format(replacement):
    raw = native_case().rule.model_dump(mode="json")
    raw["clauses"][0]["source_locators"] = [replacement]
    with pytest.raises(ValueError, match="format mismatch"):
        RuleRef.model_validate(raw)


def test_native_locations_are_bounded_and_duplicate_order_does_not_matter():
    raw = native_case().rule.model_dump(mode="json")
    locator = raw["clauses"][0]["source_locators"][0]
    raw["clauses"][0]["source_locators"] = [locator, dict(reversed(list(locator.items())))]
    with pytest.raises(ValueError, match="Duplicate"):
        RuleRef.model_validate(raw)
    raw = native_case(".pdf").rule.model_dump(mode="json")
    raw["clauses"][0]["source_locators"] = [{"kind": "pdf_block", "page": 1, "block": block} for block in range(1, 257)]
    assert len(RuleRef.model_validate(raw).clauses[0]["source_locators"]) == 256
    raw["clauses"][0]["source_locators"].append({"kind": "pdf_block", "page": 1, "block": 257})
    with pytest.raises(ValueError):
        RuleRef.model_validate(raw)


@pytest.mark.parametrize(
    "state,value,reason",
    [
        ("known", " ", "source_locator_empty"),
        ("unknown_formula_cache", None, "source_locator_unresolved"),
    ],
)
def test_owned_location_still_requires_known_nonempty_source(state, value, reason):
    case = native_case(".xlsx")
    case.source["tables"][0]["cells"]["B2"].update(state=state, value=value)
    assert clause_source_error(case.rule, case.plan.rules[0], case.source, EvidenceBudget()) == reason
    with pytest.raises(PlanValidationError) as rejected:
        validate_plan(case.plan, case.rule, case.source, EvidenceBudget())
    assert rejected.value.reason == reason
    with pytest.raises(ValueError):
        validate_bundle(case.bundle, case.spec)


def test_source_budget_exhaustion_is_not_a_repairable_locator_failure():
    case = native_case()
    with pytest.raises(EvidenceLimitError) as exhausted:
        validate_plan(case.plan, case.rule, case.source, EvidenceBudget(remaining=0))
    assert not isinstance(exhausted.value, PlanValidationError)


def test_cached_source_budget_exhaustion_does_not_start_model(tmp_path, monkeypatch):
    from ppap_deep.semantic import guest

    case = native_case()
    (tmp_path / "cached.json").write_text(case.plan.model_dump_json())
    monkeypatch.setattr(guest, "EvidenceBudget", lambda: EvidenceBudget(remaining=0))
    calls = []

    class Runner:
        def run(self, work, prompt, schema, deadline, **kwargs):
            calls.append(work.name)
            return case.plan

    with pytest.raises(EvidenceLimitError) as exhausted:
        compile_plans(tmp_path, tmp_path, [case.rule], {case.rule.rule_id: case.source}, {case.rule.rule_id: "cached.json"}, Runner(), 9999999999.0, ReadLimits())
    assert not isinstance(exhausted.value, PlanValidationError)
    assert calls == []
    assert not (tmp_path / "compile-0").exists()


def test_audit_work_limit_does_not_start_model_repair(tmp_path, monkeypatch):
    from ppap_deep.semantic import guest, trace

    case = native_case()
    monkeypatch.setattr(trace, "EvidenceBudget", lambda: EvidenceBudget(remaining=0))
    calls = []

    class Runner:
        def run(self, work, prompt, schema, deadline, **kwargs):
            calls.append(work.name)
            raise AssertionError("A host work limit cannot be repaired by the model")

    (tmp_path / "audit").mkdir()

    def entrypoint(inputs, work, credentials):
        validate_or_repair(case.bundle, case.spec, work, Runner(), 9999999999.0)

    monkeypatch.setattr(guest, "run_entrypoint", entrypoint)
    guest.run_with_faults(tmp_path, tmp_path, tmp_path)
    assert calls == []
    assert not (tmp_path / "audit-repair").exists()
    assert not (tmp_path / "audit/validation-feedback.json").exists()
    assert (tmp_path / "audit/execution.json").exists()
    assert json.loads((tmp_path / "result/fault.json").read_text()) == {"ok": False, "code": "RESOURCE_LIMIT"}


@pytest.mark.parametrize("cached", [False, True])
def test_unowned_native_locator_is_repaired_or_recompiled_from_cache(tmp_path, cached):
    case = native_case()
    invalid = case.plan.model_copy(deep=True)
    invalid.rules[0].source_locator = type(invalid.rules[0].source_locator).model_validate(case.ordinal)
    calls = []

    class Runner:
        def run(self, work, prompt, schema, deadline, **kwargs):
            calls.append(work.name)
            return case.plan if cached or len(calls) == 2 else invalid

    caches = {}
    if cached:
        (tmp_path / "cached.json").write_text(invalid.model_dump_json())
        caches[case.rule.rule_id] = "cached.json"
    result = compile_plans(tmp_path, tmp_path, [case.rule], {case.rule.rule_id: case.source}, caches, Runner(), 9999999999.0, ReadLimits())
    assert result == {case.rule.rule_id: case.plan}
    assert calls == (["compile-0"] if cached else ["compile-0", "compile-repair-0"])


def test_audit_repair_cannot_rewrite_cached_source_programs(tmp_path):
    case = native_case()
    invalid = case.plan.model_copy(deep=True)
    invalid.rules[0].source_locator = type(invalid.rules[0].source_locator).model_validate(case.ordinal)
    case.bundle.plans[case.rule.rule_id] = invalid
    calls = []

    class Runner:
        input_tokens = output_tokens = 0

        def run(self, work, prompt, schema, deadline, **kwargs):
            calls.append(work.name)
            assert schema is AuditTrace
            return case.bundle.trace

    (tmp_path / "audit").mkdir()
    with pytest.raises(ValueError):
        validate_or_repair(case.bundle, case.spec, tmp_path, Runner(), 9999999999.0)
    assert calls == ["audit-repair"]
    assert case.bundle.plans[case.rule.rule_id] == invalid


def test_source_membership_does_not_certify_the_empty_scope_policy():
    case = native_case()
    case.plan.rules[0].empty_scope_status = "PASS"
    case.plan.rules[0].empty_scope_basis = "An invented empty-scope exception still needs separate semantic review."
    validate_plan(case.plan, case.rule, case.source, EvidenceBudget())


def test_published_rules_use_only_their_own_clause_id_without_native_locations():
    clauses = [{"id": "R1", "rule_to_check": "First effective requirement"}, {"id": "R2", "rule_to_check": "Second effective requirement"}]
    rule = RuleRef(rule_id="published", rule_version_id="v1", sha256="a" * 64, format=".json", plan_revision="b" * 64, clauses=clauses)
    source = {"source": {"sha256": rule.sha256, "format": ".json"}, "kind": "published_rule", "rule_version_id": "v1", "rule_body": {"rules": clauses}, "clauses": clauses}
    selected = native_case().plan.rules[0].model_dump(mode="json")
    selected["source_locator"] = {"kind": "published_clause", "clause_id": "R1"}
    planned = type(native_case().plan.rules[0]).model_validate(selected)
    assert clause_source_error(rule, planned, source, EvidenceBudget()) is None
    raw = deepcopy(rule.model_dump(mode="json"))
    raw["clauses"][0]["source_locators"] = [{"kind": "published_clause", "clause_id": "R2"}]
    rule = RuleRef.model_validate(raw)
    planned.source_locator.clause_id = "R2"
    assert clause_source_error(rule, planned, source, EvidenceBudget()) == "source_locator_unresolved"
