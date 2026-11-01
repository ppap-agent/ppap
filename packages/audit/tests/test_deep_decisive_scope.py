"""Scope duties follow the decisive expression, while source coverage stays complete."""

from copy import deepcopy

import pytest
from audit.models import RulePlan
from audit.trace import AuditTrace, evaluate_audit


def evaluate(require, *, target=None, source_issues=(), quantifier="forall"):
    document = {
        "source": {"file": "source.xlsx", "format": ".xlsx", "sha256": "source"},
        "tables": [
            {
                "id": "Data",
                "rows": 2,
                "cols": 2,
                "merges": [],
                "cells": {"A1": {"value": "code", "state": "known"}, "B1": {"value": "value", "state": "known"}, "A2": {"value": 10, "state": "known"}, "B2": {"value": "CC", "state": "known"}},
            }
        ],
    }
    binding = {
        "id": "source",
        "document_id": "source",
        "source_sha256": "source",
        "table": "Data",
        "header_rows": [1],
        "first_row": 2,
        "last_row": 2,
        "columns": {"code": "A", "value": "B"},
        "mapping_basis": "Native headers",
        "boundary_basis": "Complete data range",
    }
    inputs = {
        "source": {"kind": "bound", "scopes": [{"binding_id": "source"}], "unresolved_scopes": list(source_issues)},
        "target": target or {"kind": "unresolved", "reason": "Target table remains unbound"},
    }
    trace = AuditTrace.model_validate({"bindings": [binding], "uses": [{"audit_id": "source", "rule_id": "R1", "inputs": inputs}]})
    plan = RulePlan.model_validate(
        {
            "source_sha256": "rules",
            "plan_revision": "a" * 64,
            "rules": [
                {
                    "id": "R1",
                    "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                    "program": {"kind": "records", "source": "source", "quantifier": quantifier, "require": require},
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "No empty exception",
                }
            ],
        }
    )
    return evaluate_audit(trace, {"source": document}, {"source": plan})


TRUE = {"kind": "value_test", "field": "value", "op": "eq", "value": "CC"}
FALSE = {**TRUE, "value": "SC"}
RELATION = {"kind": "related", "target": "target", "source_key": {"fields": ["code"]}, "target_key": {"fields": ["code"]}, "match": "any"}


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("operator,known,status", [("any", TRUE, "PASS"), ("all", FALSE, "NG"), ("any", FALSE, "UNABLE"), ("all", TRUE, "UNABLE")])
def test_only_decisive_boolean_inputs_own_scope_obligations(operator, known, status, reverse):
    items = [known, RELATION]
    if reverse:
        items.reverse()
    result = evaluate({"kind": operator, "items": items})
    assert result.report.checks[0].status == status
    if status == "PASS":
        assert result.report.checks[0].unknown == 0
        assert any(component["value"] is None for component in result.observations[0]["components"])


@pytest.mark.parametrize("reverse", [False, True])
def test_nested_negation_preserves_boolean_scope_dependencies(reverse):
    children = [FALSE, RELATION]
    if reverse:
        children.reverse()
    assert evaluate({"kind": "not", "item": {"kind": "all", "items": children}}).report.checks[0].status == "PASS"
    assert evaluate({"kind": "not", "item": RELATION}).report.checks[0].status == "UNABLE"


@pytest.mark.parametrize("match", ["any", "all", "one_to_one"])
def test_incomplete_matching_target_is_required_only_by_its_relation(match):
    target = {"kind": "bound", "scopes": [{"binding_id": "source"}], "unresolved_scopes": ["Another target table is not yet located"]}
    relation = {**RELATION, "match": match}
    assert evaluate(relation, target=target).report.checks[0].status == "UNABLE"
    result = evaluate({"kind": "any", "items": [relation, TRUE]}, target=target)
    assert (result.report.checks[0].status, result.report.checks[0].unknown) == ("PASS", 0)
    assert evaluate({"kind": "not", "item": relation}, target=target).report.checks[0].status == "UNABLE"
    masked = next(component for component in result.observations[0]["components"] if "relation" in component)
    assert masked["input_scopes"]["target"]["issues"] == target["unresolved_scopes"]


@pytest.mark.parametrize("match,status", [("any", "UNABLE"), ("all", "NG"), ("one_to_one", "NG")])
def test_unseen_candidates_can_repair_any_mismatch_but_not_confirmed_all_conflict(match, status):
    target = {"kind": "bound", "scopes": [{"binding_id": "source"}], "unresolved_scopes": ["Another target table remains unbound"]}
    relation = {**RELATION, "match": match, "comparisons": [{"source_field": "value", "target_field": "code", "mode": "exact"}]}
    assert evaluate(relation, target=target).report.checks[0].status == status
    expected_not = "UNABLE" if status == "UNABLE" else "PASS"
    assert evaluate({"kind": "not", "item": relation}, target=target).report.checks[0].status == expected_not


@pytest.mark.parametrize("quantifier", ["forall", "exists"])
def test_decisive_true_does_not_erase_unfinished_source_population(quantifier):
    result = evaluate({"kind": "any", "items": [TRUE, RELATION]}, source_issues=["Another source table remains unbound"], quantifier=quantifier)
    assert (result.report.checks[0].status, result.report.checks[0].unknown) == ("UNABLE", 1)


def test_unknown_target_membership_remains_visible_when_relation_is_needed():
    target = {
        "kind": "bound",
        "scopes": [{"binding_id": "source", "unresolved_rows": [{"row": 2, "reason": "Uncertain record role", "evidence_locator": {"kind": "xlsx_cell", "sheet": "Data", "cell": "A2"}}]}],
    }
    assert evaluate(RELATION, target=target).report.checks[0].status == "UNABLE"
    assert evaluate({"kind": "any", "items": [TRUE, RELATION]}, target=target).report.checks[0].status == "PASS"


def test_masked_scope_still_rejects_invalid_declaration():
    target = {"kind": "bound", "scopes": [{"binding_id": "source"}, {"binding_id": "source"}]}
    with pytest.raises(ValueError, match="duplicate collection binding"):
        evaluate({"kind": "any", "items": [TRUE, RELATION]}, target=deepcopy(target))
