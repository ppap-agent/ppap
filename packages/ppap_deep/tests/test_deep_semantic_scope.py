"""Real 58-row source: the host owns coverage, model answers only local holes."""

from copy import deepcopy

import pytest
from openpyxl import Workbook
from ppap_deep.semantic.document_tools import snapshot
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.trace import AuditTrace, evaluate_audit, validate_trace


@pytest.fixture
def case(tmp_path):
    path = tmp_path / "inspection.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Inspection"
    sheet.cell(5, 1, "Requirement")
    for row in range(6, 64):
        sheet.cell(row, 1, f"Item {row}")
    book.save(path)
    book.close()
    source = snapshot(path)
    plan = RulePlan.model_validate(
        {
            "source_sha256": "a" * 64,
            "plan_revision": "b" * 64,
            "rules": [
                {
                    "id": "R2",
                    "source_locator": {"kind": "published_clause", "clause_id": "R2"},
                    "program": {
                        "kind": "records",
                        "source": "requirements",
                        "quantifier": "forall",
                        "require": {"kind": "judge", "question": "Does this requirement satisfy the frozen clause?", "fields": ["requirement"]},
                    },
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "No empty scope exception",
                }
            ],
        }
    )
    trace = AuditTrace.model_validate(
        {
            "bindings": [
                {
                    "id": "body",
                    "document_id": "target",
                    "source_sha256": source["source"]["sha256"],
                    "table": "Inspection",
                    "header_rows": [5],
                    "first_row": 6,
                    "last_row": 63,
                    "columns": {"requirement": "A"},
                    "mapping_basis": "Actual row 5 header",
                    "boundary_basis": "Complete original rows 6 through 63",
                }
            ],
            "uses": [{"audit_id": "target", "rule_id": "R2", "inputs": {"requirements": {"kind": "bound", "scopes": [{"binding_id": "body"}]}}}],
        }
    )
    return source, plan, trace


def run(case, trace=None):
    source, plan, original = case
    return evaluate_audit(trace or original, {"target": source}, {"target": plan})


def fill(trace, needs, count=None, value="true"):
    raw = trace.model_dump(mode="json")
    raw["answers"] = [
        {
            "need_id": need.id,
            "value": value,
            "reason": "Checked this original requirement",
            "evidence": [{"document_id": "target", "locator": {"kind": "xlsx_cell", "sheet": "Inspection", "cell": f"A{need.context['source']['record']['row']}"}}],
        }
        for need in needs[:count]
    ]
    return AuditTrace.model_validate(raw)


def test_eleven_answers_cannot_hide_the_other_47_original_rows(case):
    initial = run(case)
    partial = fill(case[2], initial.needs, 11)
    check = run(case, partial).report.checks[0]
    assert (check.status, check.total, check.checked, check.unknown) == ("UNABLE", 58, 11, 47)


def test_complete_local_answers_preserve_complete_real_workbook_scope(case):
    complete = fill(case[2], run(case).needs)
    check = run(case, complete).report.checks[0]
    assert (check.status, check.total, check.checked, check.unknown) == ("PASS", 58, 58, 0)


@pytest.mark.parametrize("defect", ["outside_answer", "duplicate_answer", "binding_sha", "binding_document", "binding_table", "missing_scope", "model_row_verdict"])
def test_bad_identity_or_model_verdict_cannot_fake_coverage(case, defect):
    raw = fill(case[2], run(case).needs).model_dump(mode="json")
    if defect == "outside_answer":
        raw["answers"][0]["need_id"] = "0" * 64
    elif defect == "duplicate_answer":
        raw["answers"].append(deepcopy(raw["answers"][0]))
    elif defect == "binding_sha":
        raw["bindings"][0]["source_sha256"] = "wrong"
    elif defect == "binding_document":
        raw["bindings"][0]["document_id"] = "outside"
    elif defect == "binding_table":
        raw["bindings"][0]["table"] = "Other"
    elif defect == "missing_scope":
        raw["uses"][0]["inputs"]["requirements"]["scopes"][0]["binding_id"] = "outside"
    else:
        raw["uses"][0]["records"] = [{"row": 6, "status": "PASS"}]
    with pytest.raises(ValueError):
        run(case, AuditTrace.model_validate(raw))


def test_changed_inputs_cannot_reuse_a_previous_answer_identity(case):
    complete = fill(case[2], run(case).needs)
    complete.bindings[0].last_row = 62
    with pytest.raises(ValueError, match="stale"):
        run(case, complete)


def test_parser_unknown_cannot_be_overridden_by_a_semantic_answer(case):
    case[0]["tables"][0]["cells"]["A63"] = {"value": None, "state": "unknown_formula_cache"}
    initial = run(case)
    assert len(initial.needs) == 57
    check = run(case, fill(case[2], initial.needs)).report.checks[0]
    assert (check.status, check.unknown, check.diagnostic) == ("UNABLE", 1, "parser_unknown")


def test_report_cannot_change_a_host_generated_unknown_cause(case):
    initial = run(case)
    initial.report.checks[0].diagnostic = "budget_exhausted"
    with pytest.raises(ValueError):
        validate_trace(case[2], initial.report, {"target": case[0]}, {"target": case[1]})


def test_program_unit_is_required_and_old_method_plan_is_not_silently_upgraded(case):
    raw = case[1].model_dump(mode="json")
    raw["rules"][0]["predicate"] = {"kind": "semantic", "granularity": "table_rows"}
    raw["rules"][0].pop("program")
    with pytest.raises(ValueError):
        RulePlan.model_validate(raw)
