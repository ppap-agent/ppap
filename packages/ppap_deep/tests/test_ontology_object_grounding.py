"""Object grouping needs readable source support and cannot erase identities."""

from copy import deepcopy

import pytest
from openpyxl import Workbook
from ppap_deep.semantic.document_tools import snapshot
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.trace import AuditTrace, evaluate_audit


def case(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "CP"
    sheet.append(["工序编号", "分类"])
    for row in [["010", "SC"], [None, "SC"], ["020", "SC"]]:
        sheet.append(row)
    path = tmp_path / "CP.xlsx"
    workbook.save(path)
    workbook.close()
    document = snapshot(path)
    plan = RulePlan.model_validate(
        {
            "source_sha256": "rule",
            "plan_revision": "a" * 64,
            "ontology": {
                "entities": [
                    {
                        "id": "processes",
                        "grain": "One process, with continuation rows identified from source layout",
                        "attributes": {"code": "string", "classification": "string"},
                        "identity": ["code"],
                    }
                ]
            },
            "rules": [
                {
                    "id": "R1",
                    "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                    "program": {"kind": "records", "source": "processes", "quantifier": "forall", "require": {"kind": "value_test", "field": "classification", "op": "eq", "value": "SC"}},
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "No observed processes cannot establish fulfillment.",
                }
            ],
        }
    )
    objects = [obj("010", [2, 3]), obj("020", [4])]
    raw = {
        "bindings": [
            {
                "id": "cp",
                "document_id": "file",
                "source_sha256": document["source"]["sha256"],
                "table": "CP",
                "header_rows": [1],
                "first_row": 2,
                "last_row": 4,
                "columns": {"code": "A", "classification": "B"},
                "mapping_basis": "Read the original header",
                "boundary_basis": "All body rows are included",
            }
        ],
        "uses": [{"audit_id": "file", "rule_id": "R1", "inputs": {"processes": {"kind": "bound", "scopes": [{"binding_id": "cp", "objects": objects}]}}}],
    }
    return document, plan, raw


def obj(identity, rows):
    return {
        "id": identity,
        "rows": rows,
        "basis": "The source identifies a process and its continuation rows; distinct process headings remain separate.",
        "evidence": [{"kind": "xlsx_cell", "sheet": "CP", "cell": f"A{row}"} for row in rows],
    }


def evaluate(document, plan, raw):
    return evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})


def test_grounded_multirow_objects_keep_member_sources_and_original_observations(tmp_path):
    document, plan, raw = case(tmp_path)
    result = evaluate(document, plan, raw)
    assert (result.report.checks[0].status, result.report.checks[0].total) == ("PASS", 2)
    first = result.observations[0]["logical_object"]
    assert first["source_rows"] == [2, 3]
    assert first["grouping"]["state"] == "supported"
    assert [item["locator"]["cell"] for item in first["grouping"]["evidence"]] == ["A2", "A3"]
    assert document["tables"][0]["cells"]["A2"]["value"] == "010"


def test_logical_object_judgment_keeps_the_next_physical_row_even_if_it_is_not_a_member(tmp_path):
    document, plan, raw = case(tmp_path)
    program = plan.model_dump(mode="json")
    program["rules"][0]["program"]["require"] = {"kind": "judge", "question": "What is in the next physical row?", "fields": ["code"], "inputs": ["processes"]}
    plan = RulePlan.model_validate(program)
    scope = raw["uses"][0]["inputs"]["processes"]["scopes"][0]
    scope["objects"] = [obj("010", [2]), obj("020", [4])]
    scope["excluded_rows"] = [
        {
            "row": 3,
            "reason_type": "non_record",
            "reason": "This physical row is outside the business collection, but remains in the source.",
            "evidence_locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "B3"},
        }
    ]
    before = deepcopy(document)
    result = evaluate(document, plan, raw)
    first = next(need for need in result.needs if need.context["source"]["record"]["row"] == "object:010")
    assert first.context["source"]["source_rows"] == [2]
    assert [item["record"]["row"] for item in first.context["adjacent"]] == [3]
    assert first.context["adjacent"][0]["fields"]["code"]["value"] is None
    assert document == before


@pytest.mark.parametrize("change", ["missing_reason", "missing_evidence", "partial_coverage", "header_only", "unreadable"])
def test_incomplete_grouping_support_remains_unknown(tmp_path, change):
    document, plan, raw = case(tmp_path)
    first = raw["uses"][0]["inputs"]["processes"]["scopes"][0]["objects"][0]
    if change == "missing_reason":
        first.pop("basis")
    elif change == "missing_evidence":
        first.pop("evidence")
    elif change == "partial_coverage":
        first["evidence"].pop()
    elif change == "header_only":
        first["evidence"] = [{"kind": "xlsx_cell", "sheet": "CP", "cell": "A1"}]
    else:
        document = deepcopy(document)
        document["tables"][0]["cells"]["A2"]["state"] = "unknown_formula"
    result = evaluate(document, plan, raw)
    assert result.report.checks[0].status == "UNABLE"
    assert result.observations[0]["logical_object"]["grouping"]["state"] == "unresolved"
    assert result.observations[-1]["value"] is True


@pytest.mark.parametrize("location", [{"kind": "xlsx_cell", "sheet": "CP", "cell": "A4"}, {"kind": "xlsx_cell", "sheet": "outside", "cell": "A2"}])
def test_grouping_evidence_cannot_borrow_another_objects_source(tmp_path, location):
    document, plan, raw = case(tmp_path)
    raw["uses"][0]["inputs"]["processes"]["scopes"][0]["objects"][0]["evidence"] = [location]
    with pytest.raises(ValueError, match="[Gg]rouping.*outside"):
        evaluate(document, plan, raw)


def test_same_classification_does_not_justify_merging_distinct_process_identities(tmp_path):
    document, plan, raw = case(tmp_path)
    raw["uses"][0]["inputs"]["processes"]["scopes"][0]["objects"] = [obj("one-claimed-object", [2, 3, 4])]
    result = evaluate(document, plan, raw)
    assert result.report.checks[0].status == "UNABLE"
    observed = result.observations[0]["logical_object"]["attributes"]["code"]
    assert [item["value"] for item in observed if item["value"]] == ["010", "020"]


def test_explicit_fields_cannot_erase_a_members_identity_or_violation(tmp_path):
    document, plan, raw = case(tmp_path)
    document["tables"][0]["cells"]["B4"]["value"] = "CC"
    assert evaluate(document, plan, raw).report.checks[0].status == "NG"
    grouped = obj("claimed-single-process", [2, 3, 4])
    grouped["fields"] = {
        "code": [{"kind": "xlsx_cell", "sheet": "CP", "cell": "A2"}],
        "classification": [{"kind": "xlsx_cell", "sheet": "CP", "cell": "B2"}],
    }
    raw["uses"][0]["inputs"]["processes"]["scopes"][0]["objects"] = [grouped]
    result = evaluate(document, plan, raw)
    assert result.report.checks[0].status == "UNABLE"
    observed = result.observations[0]["logical_object"]["attributes"]
    assert [item["value"] for item in observed["code"] if item["value"]] == ["010", "020"]
    assert [item["value"] for item in observed["classification"]] == ["SC", "SC", "CC"]
