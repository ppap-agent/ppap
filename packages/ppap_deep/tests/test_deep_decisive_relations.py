"""Native witnesses avoid irrelevant model questions without relaxing matching."""

import pytest
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.trace import AuditTrace, evaluate_audit


def relation_case(source_rows, target_rows, *, comparisons=None, match="any", target_when=None, source_when=None):
    documents, bindings = {}, []
    for identity, rows in (("source", source_rows), ("target", target_rows)):
        cells = {"A1": {"value": "code", "state": "known"}, "B1": {"value": "value", "state": "known"}}
        for index, values in enumerate(rows, 2):
            for column, value in zip("AB", values, strict=True):
                cells[f"{column}{index}"] = value if isinstance(value, dict) else {"value": value, "state": "known"}
        documents[identity] = {
            "source": {"sha256": identity, "format": ".xlsx", "file": identity + ".xlsx"},
            "tables": [{"id": "Records", "rows": len(rows) + 1, "cols": 2, "cells": cells, "merges": []}],
        }
        bindings.append(
            {
                "id": identity,
                "document_id": identity,
                "source_sha256": identity,
                "table": "Records",
                "header_rows": [1],
                "first_row": 2,
                "last_row": len(rows) + 1,
                "columns": {"code": "A", "value": "B"},
                "mapping_basis": "First row labels the native columns.",
                "boundary_basis": "Complete body below the header.",
            }
        )
    trace = AuditTrace.model_validate(
        {
            "bindings": bindings,
            "uses": [{"audit_id": "source", "rule_id": "R1", "inputs": {key: {"kind": "bound", "scopes": [{"binding_id": key}]} for key in documents}}],
        }
    )
    plan = RulePlan.model_validate(
        {
            "source_sha256": "rules",
            "plan_revision": "a" * 64,
            "rules": [
                {
                    "id": "R1",
                    "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                    "program": {
                        "kind": "records",
                        "source": "source",
                        "quantifier": "forall",
                        "when": source_when,
                        "require": {
                            "kind": "related",
                            "target": "target",
                            "source_key": {"fields": ["code"]},
                            "target_key": {"fields": ["code"]},
                            "key_match": "semantic",
                            "match": match,
                            "comparisons": comparisons or [],
                            "target_when": target_when,
                        },
                    },
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "The source does not define an empty result.",
                }
            ],
        }
    )
    return trace, documents, {"source": plan}


def answer_all(trace, result, value):
    raw = trace.model_dump(mode="json")
    raw["answers"] = [{"need_id": item.id, "value": value, "reason": "The original records were checked."} for item in result.needs]
    return AuditTrace.model_validate(raw)


@pytest.mark.parametrize("count", [20, 129])
@pytest.mark.parametrize(
    "target_when",
    [
        None,
        {
            "kind": "all",
            "items": [
                {"kind": "value_test", "field": "value", "op": "not_empty"},
                {"kind": "any", "items": [{"kind": "not", "item": {"kind": "value_test", "field": "code", "op": "lt", "value": 0}}, {"kind": "value_test", "field": "code", "op": "eq", "value": -1}]},
            ],
        },
    ],
)
def test_semantic_any_with_native_witnesses_needs_no_model_questions(count, target_when):
    rows = [(i, "SC") for i in range(count)]
    trace, documents, plans = relation_case(rows, rows, comparisons=[{"source_field": "value", "target_field": "value", "mode": "semantic"}], target_when=target_when)
    result = evaluate_audit(trace, documents, plans)
    assert result.report.checks[0].status == "PASS"
    assert result.needs == []
    relations = [item["relation"] for item in result.observations if "relation" in item]
    assert len(relations) == count
    assert all(item["candidate_search_complete"] is False for item in relations)


def test_any_searches_all_exact_candidates_before_asking_semantic_questions():
    trace, documents, plans = relation_case(
        [(10, "SC")],
        [(10, "different semantic value"), (10, "SC"), (20, "SC")],
        comparisons=[{"source_field": "value", "target_field": "value", "mode": "semantic"}],
    )
    result = evaluate_audit(trace, documents, plans)
    assert result.report.checks[0].status == "PASS"
    assert result.needs == []
    relation = result.observations[0]["relation"]
    assert [pair["state"] for pair in relation["candidates"]] == ["unresolved", "matched"]
    assert relation["candidate_search_complete"] is False


@pytest.mark.parametrize("first_value", ["CC", {"value": None, "state": "unknown_formula_cache"}])
def test_unsatisfied_exact_candidate_does_not_hide_a_semantic_alternative(first_value):
    trace, documents, plans = relation_case([(10, "SC")], [(10, first_value), ("ten", "SC")], comparisons=[{"source_field": "value", "target_field": "value", "mode": "exact"}])
    initial = evaluate_audit(trace, documents, plans)
    assert initial.report.checks[0].status == "UNABLE"
    assert len(initial.needs) == 1
    assert initial.needs[0].kind == "semantic_key"
    resolved = evaluate_audit(answer_all(trace, initial, "true"), documents, plans)
    assert resolved.report.checks[0].status == "PASS"
    rejected = evaluate_audit(answer_all(trace, initial, "false"), documents, plans)
    assert rejected.report.checks[0].status == ("UNABLE" if isinstance(first_value, dict) else "NG")


@pytest.mark.parametrize("match", ["all", "one_to_one"])
def test_stricter_relations_still_request_the_complete_semantic_graph(match):
    rows = [(10, "SC"), (20, "SC")]
    trace, documents, plans = relation_case(rows, rows, match=match)
    initial = evaluate_audit(trace, documents, plans)
    assert initial.report.checks[0].status == "UNABLE"
    assert len(initial.needs) == (3 if match == "one_to_one" else 2)
    assert evaluate_audit(answer_all(trace, initial, "false"), documents, plans).report.checks[0].status == "PASS"


def test_native_witness_does_not_accept_an_unissued_semantic_answer():
    trace, documents, plans = relation_case([(10, "SC")], [(10, "SC"), (20, "SC")])
    raw = trace.model_dump(mode="json")
    raw["answers"] = [{"need_id": "f" * 64, "value": "true", "reason": "This answer was never requested."}]
    with pytest.raises(ValueError, match="Unknown or stale"):
        evaluate_audit(AuditTrace.model_validate(raw), documents, plans)


def test_answering_a_batch_does_not_invalidate_other_issued_questions():
    trace, documents, plans = relation_case([(10, "SC")], [(10, "Special characteristic"), ("ten", "SC")], comparisons=[{"source_field": "value", "target_field": "value", "mode": "semantic"}])
    initial = evaluate_audit(trace, documents, plans)
    assert {need.kind for need in initial.needs} == {"semantic_key", "semantic_value"}
    assert len(initial.needs) == 2
    result = evaluate_audit(answer_all(trace, initial, "true"), documents, plans)
    assert result.report.checks[0].status == "PASS"
    assert {need.id for need in result.needs} == {need.id for need in initial.needs}


@pytest.mark.parametrize("nested", [False, True])
def test_target_membership_answers_do_not_discard_previous_key_questions(nested):
    condition = {
        "kind": "any",
        "items": [{"kind": "value_test", "field": "code", "op": "eq", "value": 20}, {"kind": "judge", "question": "Does this target record belong?", "fields": ["code"]}],
    }
    if nested:
        condition = {"kind": "not", "item": {"kind": "not", "item": condition}}
    trace, documents, plans = relation_case([(10, "SC")], [(10, "SC"), (20, "SC")], target_when=condition)
    initial = evaluate_audit(trace, documents, plans)
    assert [need.kind for need in initial.needs].count("semantic_predicate") == 2
    assert [need.kind for need in initial.needs].count("semantic_key") == 1
    result = evaluate_audit(answer_all(trace, initial, "true"), documents, plans)
    assert result.report.checks[0].status == "PASS"
    assert {need.id for need in result.needs} == {need.id for need in initial.needs}


def test_source_membership_answer_keeps_other_sources_issued_questions():
    condition = {
        "kind": "any",
        "items": [{"kind": "value_test", "field": "code", "op": "eq", "value": 20}, {"kind": "judge", "question": "Does this source record belong?", "fields": ["code"]}],
    }
    trace, documents, plans = relation_case([(10, "SC"), (20, "SC")], [(10, "SC"), (30, "SC")], source_when=condition)
    initial = evaluate_audit(trace, documents, plans)
    assert [need.kind for need in initial.needs].count("semantic_predicate") == 2
    assert [need.kind for need in initial.needs].count("semantic_key") == 2
    result = evaluate_audit(answer_all(trace, initial, "true"), documents, plans)
    assert result.report.checks[0].status == "PASS"
    assert {need.id for need in result.needs} == {need.id for need in initial.needs}
