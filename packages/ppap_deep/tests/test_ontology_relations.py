"""Ontology relation meaning constrains the real native matching evaluator."""

from copy import deepcopy

import pytest
from openpyxl import Workbook
from ppap_deep.semantic.document_tools import snapshot
from ppap_deep.semantic.models import CompiledRulePlan, RulePlan
from ppap_deep.semantic.trace import AuditTrace, evaluate_audit


def plan_data(*, kind="same_instance", source_identity=None, target_identity=None, source_key=None, target_key=None, match="one_to_one"):
    attributes = {"part": "string", "operation": "string", "classification": "string", "attachment": "string"}
    return {
        "source_sha256": "rule",
        "plan_revision": "a" * 64,
        "ontology": {
            "entities": [
                {"id": "source", "grain": "one process within one product", "attributes": attributes, "identity": source_identity or ["part", "operation"]},
                {"id": "target", "grain": "one declared process or attachment", "attributes": attributes, "identity": target_identity or ["part", "operation"]},
            ],
            "relationships": [{"id": "corresponds", "kind": kind, "source": "source", "target": "target", "meaning": "The relation stated by this rule."}],
        },
        "rules": [
            {
                "id": "R1",
                "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                "program": {
                    "kind": "records",
                    "source": "source",
                    "quantifier": "forall",
                    "require": {
                        "kind": "related",
                        "relationship_id": "corresponds",
                        "target": "target",
                        "source_key": source_key or {"fields": ["part", "operation"]},
                        "target_key": target_key or {"fields": ["part", "operation"]},
                        "match": match,
                    },
                },
                "empty_scope_status": "UNABLE",
                "empty_scope_basis": "No objects is unknown under the supplied rule.",
            }
        ],
    }


def native_case(tmp_path, left, right, raw_plan):
    documents, bindings = {}, []
    for identity, rows in (("source", left), ("target", right)):
        book = Workbook()
        sheet = book.active
        sheet.title = "Objects"
        sheet.append(["part", "operation", "classification", "attachment"])
        for row in rows:
            sheet.append(row)
        path = tmp_path / (identity + ".xlsx")
        book.save(path)
        book.close()
        documents[identity] = snapshot(path)
        bindings.append(
            {
                "id": identity,
                "document_id": identity,
                "source_sha256": documents[identity]["source"]["sha256"],
                "table": "Objects",
                "header_rows": [1],
                "first_row": 2,
                "last_row": 1 + len(rows),
                "columns": {"part": "A", "operation": "B", "classification": "C", "attachment": "D"},
                "mapping_basis": "Named original headers.",
                "boundary_basis": "Complete supplied body.",
            }
        )
    trace = AuditTrace.model_validate(
        {"bindings": bindings, "uses": [{"audit_id": "target", "rule_id": "R1", "inputs": {identity: {"kind": "bound", "scopes": [{"binding_id": identity}]} for identity in documents}}]}
    )
    return evaluate_audit(trace, documents, {"target": RulePlan.model_validate(raw_plan)})


def test_program_must_reference_one_specific_declared_relationship():
    raw = plan_data()
    raw["ontology"]["relationships"].append({"id": "contains", "kind": "association", "source": "source", "target": "target", "meaning": "Contains is distinct from same-instance correspondence."})
    for missing in (None, "not-declared"):
        candidate = deepcopy(raw)
        if missing is None:
            del candidate["rules"][0]["program"]["require"]["relationship_id"]
        else:
            candidate["rules"][0]["program"]["require"]["relationship_id"] = missing
        with pytest.raises(ValueError, match="relationship"):
            CompiledRulePlan.model_validate(candidate)
    reversed_relation = deepcopy(raw)
    reversed_relation["ontology"]["relationships"][0]["source"] = "target"
    reversed_relation["ontology"]["relationships"][0]["target"] = "source"
    with pytest.raises(ValueError, match="direction"):
        CompiledRulePlan.model_validate(reversed_relation)


def test_relationship_meaning_is_required_in_new_compiled_plans():
    raw = plan_data()
    del raw["ontology"]["relationships"][0]["kind"]
    with pytest.raises(ValueError, match="kind"):
        CompiledRulePlan.model_validate(raw)


@pytest.mark.parametrize("weak", ["classification", "operation"])
def test_same_instance_cannot_use_a_shared_classification_or_only_part_of_identity(weak):
    raw = plan_data(source_key={"fields": [weak]}, target_key={"fields": [weak]})
    with pytest.raises(ValueError, match="identity"):
        RulePlan.model_validate(raw)


@pytest.mark.parametrize("target_part,target_operation,expected", [("A", "010", "PASS"), ("B", "010", "NG"), ("A", "020", "NG"), ("A", "0100", "NG"), ("A", "O10", "NG"), ("A", "01-0", "NG")])
def test_complete_native_identity_keeps_products_processes_and_significant_text_distinct(tmp_path, target_part, target_operation, expected):
    result = native_case(tmp_path, [("A", "010", "SC", "file-1")], [(target_part, target_operation, "SC", "file-1")], plan_data())
    assert result.report.checks[0].status == expected
    assert result.observations[0]["relation"]["source_values"]["operation"]["value"] == "010"


def test_unknown_identity_does_not_become_a_confirmed_match(tmp_path):
    result = native_case(tmp_path, [("A", "010", "SC", "file-1")], [("=external!A1", "010", "SC", "file-1")], plan_data())
    assert result.report.checks[0].status == "UNABLE"


@pytest.mark.parametrize("source_part,target_part", [(None, None), (" ", " "), ("A", None), (None, "A")])
def test_partial_blank_identity_cannot_prove_same_instance(tmp_path, source_part, target_part):
    result = native_case(tmp_path, [(source_part, "010", "SC", "file-1")], [(target_part, "010", "SC", "file-1")], plan_data())
    assert result.report.checks[0].status == "UNABLE"


def test_same_instance_keeps_duplicate_instances_for_one_to_one_rejection(tmp_path):
    result = native_case(tmp_path, [("A", "010", "SC", "file-1")], [("A", "010", "SC", "file-1"), ("A", "010", "SC", "file-1")], plan_data())
    assert (result.report.checks[0].status, result.report.checks[0].total) == ("NG", 1)
    assert len(result.observations[0]["relation"]["candidates"]) == 2


def test_association_uses_a_foreign_key_without_replacing_the_source_identity(tmp_path):
    raw = plan_data(kind="association", target_identity=["attachment"], source_key={"fields": ["attachment"]}, target_key={"fields": ["attachment"]})
    result = native_case(tmp_path, [("A", "010", "SC", "file-1")], [("unrelated", "different", "CC", "file-1")], raw)
    assert result.report.checks[0].status == "PASS"


@pytest.mark.parametrize("label,expected", [("A / 010", "PASS"), ("A010", "NG"), ("A / 0100", "NG")])
def test_explicit_literal_projection_remains_valid_for_an_association(tmp_path, label, expected):
    raw = plan_data(kind="association", target_identity=["attachment"], source_key={"fields": ["part", "operation"], "template": "{part} / {operation}"}, target_key={"fields": ["attachment"]})
    result = native_case(tmp_path, [("A", "010", "SC", None)], [(None, None, None, label)], raw)
    assert result.report.checks[0].status == expected


def test_pure_legacy_unit_program_has_no_implicit_production_ontology():
    raw = plan_data()
    del raw["ontology"]
    del raw["rules"][0]["program"]["require"]["relationship_id"]
    assert RulePlan.model_validate(raw).ontology.entities == []
    with pytest.raises(ValueError, match="ontology"):
        CompiledRulePlan.model_validate(raw)


@pytest.mark.parametrize("projection", [{"separator": ""}, {"separator": " / "}, {"template": "{part}{operation}"}])
def test_same_instance_rejects_colliding_string_projections(projection):
    raw = plan_data(source_key={"fields": ["part", "operation"], **projection}, target_key={"fields": ["part", "operation"], **projection})
    # A/BC and AB/C both become ABC under a join. Even an explicit delimiter
    # does not prove that delimiter text cannot occur in an identity component.
    with pytest.raises(ValueError, match="component tuples"):
        RulePlan.model_validate(raw)


@pytest.mark.parametrize(
    "source_key,target_key",
    [
        (["operation", "part"], ["part", "operation"]),
        (["part", "operation", "classification"], ["classification", "part", "operation"]),
    ],
)
def test_same_instance_cannot_reorder_or_dilute_declared_identity(source_key, target_key):
    raw = plan_data(source_key={"fields": source_key}, target_key={"fields": target_key})
    with pytest.raises(ValueError, match="ordered entity identity"):
        RulePlan.model_validate(raw)
