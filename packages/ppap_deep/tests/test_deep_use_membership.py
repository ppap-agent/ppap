"""Reading order and business membership can share one physical binding."""

from copy import deepcopy

import pytest
from openpyxl import Workbook
from ppap_deep.semantic.document_tools import snapshot
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.trace import AuditTrace, evaluate_audit


@pytest.mark.parametrize("description,initial_status,confirmed_status", [("Apply adhesive", "UNABLE", "PASS"), (None, "NG", "NG")])
def test_unresolved_group_role_does_not_become_a_missing_description_or_change_reading_order(tmp_path, description, initial_status, confirmed_status):
    book = Workbook()
    book.active.title = "Steps"
    book.active.append(["Name", "Description"])
    book.active.append(["Assembly", None])
    book.active.append(["Bond", description])
    path = tmp_path / "steps.xlsx"
    book.save(path)
    book.close()
    document = snapshot(path)
    before = deepcopy(document)
    rules = [
        {
            "id": rule_id,
            "source_locator": {"kind": "published_clause", "clause_id": rule_id},
            "program": {"kind": "records", "source": source, "quantifier": "forall", "require": {"kind": "value_test", "field": field, "op": "not_empty"}},
            "empty_scope_status": "UNABLE",
            "empty_scope_basis": "No empty-range exception is specified.",
        }
        for rule_id, source, field in [("description", "processes", "description"), ("order", "physical_rows", "name")]
    ]
    plan = RulePlan.model_validate({"source_sha256": "rule", "plan_revision": "a" * 64, "rules": rules})
    claim = {"row": 2, "reason": "The row may be a group heading; its role is not yet established.", "evidence_locator": {"kind": "xlsx_cell", "sheet": "Steps", "cell": "A2"}}
    raw = {
        "bindings": [
            {
                "id": "body",
                "document_id": "file",
                "source_sha256": document["source"]["sha256"],
                "table": "Steps",
                "header_rows": [1],
                "first_row": 2,
                "last_row": 3,
                "columns": {"name": "A", "description": "B"},
                "mapping_basis": "The two source headers identify the columns.",
                "boundary_basis": "Both physical rows remain available.",
            }
        ],
        "uses": [
            {"audit_id": "file", "rule_id": "description", "inputs": {"processes": {"kind": "bound", "scopes": [{"binding_id": "body", "unresolved_rows": [claim]}]}}},
            {"audit_id": "file", "rule_id": "order", "inputs": {"physical_rows": {"kind": "bound", "scopes": [{"binding_id": "body"}]}}},
        ],
    }

    def checks():
        result = evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})
        return {check.rule_id: check for check in result.report.checks}

    initial = checks()
    assert (initial["description"].status, initial["description"].unknown) == (initial_status, 1)
    assert (initial["order"].status, initial["order"].total, initial["order"].checked) == ("PASS", 2, 2)
    # This is an explicit, sourced role decision for one use, not a title heuristic.
    raw["uses"][0]["inputs"]["processes"]["scopes"][0] = {
        "binding_id": "body",
        "excluded_rows": [{**claim, "reason_type": "non_record", "reason": "Confirmed as a group heading for this business collection."}],
    }
    confirmed = checks()
    assert (confirmed["description"].status, confirmed["description"].unknown) == (confirmed_status, 0)
    assert (confirmed["order"].status, confirmed["order"].total, confirmed["order"].checked) == ("PASS", 2, 2)
    assert document == before
