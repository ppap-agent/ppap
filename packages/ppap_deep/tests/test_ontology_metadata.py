"""A form's logical attributes need not occupy distinct physical columns."""

import pytest
from openpyxl import Workbook
from ppap_deep.semantic.document_tools import snapshot
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.trace import AuditTrace, evaluate_audit


def metadata_case(tmp_path, *, mapping="points", blank_code=False):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Metadata"
    for row, values in enumerate([["Label", "Value"], ["Product name", "Widget"], ["Product code", None if blank_code else "P001"]], 1):
        for column, value in enumerate(values, 10):
            sheet.cell(row, column, value)
    path = tmp_path / "Metadata.xlsx"
    workbook.save(path)
    workbook.close()
    document = snapshot(path)
    plan = RulePlan.model_validate(
        {
            "source_sha256": "frozen-rule",
            "plan_revision": "a" * 64,
            "ontology": {
                "entities": [
                    {
                        "id": "form",
                        "grain": "One form identified by its product code; labeled attributes occupy different rows",
                        "attributes": {"name": "string", "code": "string"},
                        "identity": ["code"],
                    }
                ]
            },
            "rules": [
                {
                    "id": "R1",
                    "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                    "program": {
                        "kind": "records",
                        "source": "form",
                        "quantifier": "forall",
                        "require": {
                            "kind": "all",
                            "items": [{"kind": "value_test", "field": "name", "op": "eq", "value": "Widget"}, {"kind": "value_test", "field": "code", "op": "eq", "value": "P001"}],
                        },
                    },
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "The rule does not grant a pass for an unread form.",
                }
            ],
        }
    )

    def location(cell):
        return {"kind": "xlsx_cell", "sheet": "Metadata", "cell": cell}

    binding = {
        "id": "header",
        "document_id": "file",
        "source_sha256": document["source"]["sha256"],
        "table": "Metadata",
        "header_rows": [1],
        "first_row": 2,
        "last_row": 3,
        "columns": {"name": "K", "code": "K"},
        "mapping_basis": "Labeled form values occupy the same physical column",
        "boundary_basis": "Both labeled attribute rows belong to this form",
    }
    if mapping == "points":
        binding["columns"] = {}
    elif mapping == "holes":
        binding["columns"] = {}
        binding["field_holes"] = {field: {"state": "unresolved", "reason": "There is no global row-column mapping; each object supplies the exact source position"} for field in ["name", "code"]}
    trace = AuditTrace.model_validate(
        {
            "bindings": [binding],
            "uses": [
                {
                    "audit_id": "file",
                    "rule_id": "R1",
                    "inputs": {
                        "form": {
                            "kind": "bound",
                            "scopes": [
                                {
                                    "binding_id": "header",
                                    "objects": [
                                        {
                                            "id": "one-form",
                                            "rows": [2, 3],
                                            "basis": "The two labels identify different properties of this one form",
                                            "evidence": [location("J2"), location("J3")],
                                            "fields": {"name": [location("K2")], "code": [location("K3")]},
                                        }
                                    ],
                                }
                            ],
                        }
                    },
                }
            ],
        }
    )
    return document, plan, trace


@pytest.mark.parametrize("mapping", ["points", "holes"])
def test_explicit_labeled_positions_bind_metadata_without_placeholder_columns(tmp_path, mapping):
    document, plan, trace = metadata_case(tmp_path, mapping=mapping)
    result = evaluate_audit(trace, {"file": document}, {"file": plan})
    assert (result.report.checks[0].status, result.report.checks[0].total) == ("PASS", 1)
    attributes = result.observations[0]["logical_object"]["attributes"]
    assert [item["value"] for item in attributes["name"]] == ["Widget"]
    assert [item["value"] for item in attributes["code"]] == ["P001"]
    assert document["tables"][0]["cells"]["K2"]["value"] == "Widget"
    assert document["tables"][0]["cells"]["K3"]["value"] == "P001"


def test_a_located_logical_attribute_exists_even_when_its_value_is_blank(tmp_path):
    document, plan, trace = metadata_case(tmp_path, mapping="points", blank_code=True)
    raw = plan.model_dump(mode="json")
    program = raw["rules"][0]["program"]
    program["precondition"] = {"kind": "column_exists", "input": "form", "field": "code"}
    program["require"] = {"kind": "value_test", "field": "code", "op": "not_empty"}
    result = evaluate_audit(trace, {"file": document}, {"file": RulePlan.model_validate(raw)})
    assert result.report.checks[0].status == "NG"
    assert next(item for item in result.observations if item["role"] == "prerequisite")["value"] is True
    assert result.observations[-1]["value"] is False


def test_an_unconfirmed_meaning_does_not_prove_logical_attribute_existence(tmp_path):
    document, plan, trace = metadata_case(tmp_path)
    raw = plan.model_dump(mode="json")
    raw["rules"][0]["program"] = {"kind": "document", "require": {"kind": "column_exists", "input": "form", "field": "code"}}
    altered = trace.model_dump(mode="json")
    altered["bindings"][0]["interpretations"] = [
        {
            "id": "code-meaning",
            "field": "code",
            "question": "Does this label identify the product?",
            "candidates": [
                {"id": "product", "label": "Product code", "explanation": "This field identifies the product."},
                {"id": "equipment", "label": "Equipment code", "explanation": "This field identifies equipment."},
            ],
            "evidence": [{"document_id": "file", "locator": {"kind": "xlsx_cell", "sheet": "Metadata", "cell": "J3"}}],
        }
    ]
    result = evaluate_audit(AuditTrace.model_validate(altered), {"file": document}, {"file": RulePlan.model_validate(raw)})
    assert result.report.checks[0].status == "UNABLE"
    assert len(result.clarifications) == 1


@pytest.mark.parametrize("confirmed", [False, True])
def test_empty_record_population_does_not_erase_a_header_meaning_question(tmp_path, confirmed):
    document, plan, trace = metadata_case(tmp_path, mapping="columns")
    raw = plan.model_dump(mode="json")
    raw["rules"][0]["program"] = {"kind": "document", "require": {"kind": "column_exists", "input": "form", "field": "code"}}
    altered = trace.model_dump(mode="json")
    altered["bindings"][0]["interpretations"] = [
        {
            "id": "header-meaning",
            "field": "code",
            "question": "Does this header identify the product?",
            "candidates": [{"id": "product", "label": "Product", "explanation": "The header identifies the product code."}],
            "evidence": [{"document_id": "file", "locator": {"kind": "xlsx_cell", "sheet": "Metadata", "cell": "K1"}}],
        }
    ]
    scope = altered["uses"][0]["inputs"]["form"]["scopes"][0]
    scope["objects"] = []
    scope["excluded_rows"] = [
        {"row": row, "reason_type": "non_record", "evidence_locator": {"kind": "xlsx_cell", "sheet": "Metadata", "cell": f"J{row}"}, "reason": "Labeled metadata, not body records"} for row in [2, 3]
    ]
    if confirmed:
        raw["appendix"] = [
            {
                "kind": "interpretation",
                "question_id": "header-meaning",
                "candidate_id": "product",
                "content": "The header identifies the product code.",
                "scope": {"document_ids": ["file"], "rule_ids": ["R1"]},
            }
        ]
    result = evaluate_audit(AuditTrace.model_validate(altered), {"file": document}, {"file": RulePlan.model_validate(raw)})
    assert result.report.checks[0].status == ("PASS" if confirmed else "UNABLE")
    assert result.report.checks[0].unknown == (0 if confirmed else 1)
    assert len(result.clarifications) == (0 if confirmed else 1)


def test_explicit_positions_do_not_erase_a_conflicting_global_column_mapping(tmp_path):
    document, plan, trace = metadata_case(tmp_path, mapping="columns")
    result = evaluate_audit(trace, {"file": document}, {"file": plan})
    assert result.report.checks[0].status == "UNABLE"
    observed = result.observations[0]["logical_object"]["attributes"]
    assert [item["value"] for item in observed["code"]] == ["Widget", "P001"]


def test_pure_point_mapping_cannot_claim_members_with_no_attribute_sources(tmp_path):
    document, plan, trace = metadata_case(tmp_path)
    raw = trace.model_dump(mode="json")
    item = raw["uses"][0]["inputs"]["form"]["scopes"][0]["objects"][0]
    item["fields"] = {"code": [{"kind": "xlsx_cell", "sheet": "Metadata", "cell": "K3"}]}
    revised = plan.model_dump(mode="json")
    revised["rules"][0]["program"]["require"] = {"kind": "value_test", "field": "code", "op": "not_empty"}
    result = evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": RulePlan.model_validate(revised)})
    assert result.report.checks[0].status == "UNABLE"


def test_one_objects_positions_do_not_prove_another_objects_attribute_exists(tmp_path):
    document, plan, trace = metadata_case(tmp_path)
    raw = trace.model_dump(mode="json")
    base = raw["uses"][0]["inputs"]["form"]["scopes"][0]["objects"][0]
    raw["uses"][0]["inputs"]["form"]["scopes"][0]["objects"] = [
        {**base, "id": "name-only", "rows": [2], "evidence": [base["evidence"][0]], "fields": {"name": base["fields"]["name"]}},
        {**base, "id": "code-only", "rows": [3], "evidence": [base["evidence"][1]], "fields": {"code": base["fields"]["code"]}},
    ]
    revised = plan.model_dump(mode="json")
    revised["rules"][0]["program"] = {"kind": "document", "require": {"kind": "column_exists", "input": "form", "field": "code"}}
    result = evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": RulePlan.model_validate(revised)})
    assert result.report.checks[0].status == "UNABLE"


def test_explicit_source_conflicts_with_a_confirmed_absent_field(tmp_path):
    document, plan, trace = metadata_case(tmp_path, mapping="holes")
    raw = trace.model_dump(mode="json")
    raw["bindings"][0]["field_holes"]["code"]["state"] = "absent"
    with pytest.raises(ValueError, match="absent field"):
        evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})


def test_a_formula_unknown_value_does_not_remove_its_located_attribute(tmp_path):
    document, plan, trace = metadata_case(tmp_path)
    document["tables"][0]["cells"]["K2"]["state"] = "unknown_formula"
    document["tables"][0]["cells"]["K2"]["value"] = None
    raw = plan.model_dump(mode="json")
    raw["rules"][0]["program"] = {"kind": "document", "require": {"kind": "column_exists", "input": "form", "field": "name"}}
    result = evaluate_audit(trace, {"file": document}, {"file": RulePlan.model_validate(raw)})
    assert result.report.checks[0].status == "PASS"
    assert document["tables"][0]["cells"]["K2"]["state"] == "unknown_formula"


def test_a_physical_range_alone_cannot_prove_logical_fields(tmp_path):
    document, plan, trace = metadata_case(tmp_path)
    raw = trace.model_dump(mode="json")
    raw["uses"][0]["inputs"]["form"]["scopes"][0]["objects"] = []
    with pytest.raises(ValueError, match="identity attributes|requires explicit object fields"):
        evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})
