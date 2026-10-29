"""A single program covers complete distinct tables and frozen related files."""

from copy import deepcopy

import pytest
from openpyxl import Workbook
from ppap_deep.semantic.document_tools import snapshot
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.trace import AuditTrace, evaluate_audit, validate_trace
from ppap_deep.semantic.validation import validate_report


@pytest.fixture
def source(tmp_path):
    book = Workbook()
    book.remove(book.active)
    for name in ("PFC-SZ", "PFC-HF"):
        sheet = book.create_sheet(name)
        sheet.append(["Process"])
        sheet.append(["Receiving"])
        sheet.append(["Inspection"])
    path = tmp_path / "two-parts.xlsx"
    book.save(path)
    book.close()
    return snapshot(path)


def make_case(source, *, quantifier="forall"):
    plan = RulePlan.model_validate(
        {
            "source_sha256": "a" * 64,
            "plan_revision": "b" * 64,
            "rules": [
                {
                    "id": "R1",
                    "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                    "program": {"kind": "records", "source": "processes", "quantifier": quantifier, "require": {"kind": "value_test", "field": "process", "op": "not_empty"}},
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "No empty exception",
                }
            ],
        }
    )
    bindings = [
        {
            "id": name,
            "document_id": "target",
            "source_sha256": source["source"]["sha256"],
            "table": name,
            "header_rows": [1],
            "first_row": 2,
            "last_row": 3,
            "columns": {"process": "A"},
            "mapping_basis": "Process header",
            "boundary_basis": "Both body records",
        }
        for name in ("PFC-SZ", "PFC-HF")
    ]
    raw = {"bindings": bindings, "uses": [{"audit_id": "target", "rule_id": "R1", "inputs": {"processes": {"kind": "bound", "scopes": [{"binding_id": name} for name in ("PFC-SZ", "PFC-HF")]}}}]}
    return plan, raw


def checked(source, plan, raw, **context):
    trace = AuditTrace.model_validate(raw)
    documents = {"target": source, **context}
    result = evaluate_audit(trace, documents, {"target": plan})
    validate_report(result.report, {"target": plan}, documents)
    validate_trace(trace, result.report, documents, {"target": plan})
    return result.report.checks[0]


def test_all_rows_are_generated_for_both_real_tables(source):
    plan, raw = make_case(source)
    result = checked(source, plan, raw)
    assert (result.status, result.total, result.checked, result.unknown) == ("PASS", 4, 4, 0)
    assert {cite.locator.sheet for cite in result.evidence} == {"PFC-SZ", "PFC-HF"}


def test_a_target_can_quantify_all_authorized_related_document_rows(source):
    plan, raw = make_case(source)
    for binding in raw["bindings"]:
        binding["document_id"] = "pfd"
    target = deepcopy(source)
    target["source"]["sha256"] = "target-hash"
    result = checked(target, plan, raw, pfd=source)
    assert (result.status, result.total) == ("PASS", 4)
    assert {cite.document_id for cite in result.evidence} == {"pfd"}


def test_same_named_tables_in_different_frozen_documents_stay_distinct(source):
    plan, raw = make_case(source)
    other = deepcopy(source)
    raw["bindings"][1].update(document_id="other", table="PFC-SZ")
    result = checked(source, plan, raw, other=other)
    assert (result.status, result.total) == ("PASS", 4)
    assert {cite.document_id for cite in result.evidence} == {"target", "other"}


@pytest.mark.parametrize("quantifier,first,second,expected", [("forall", "good", "", "NG"), ("exists", "", "good", "PASS"), ("exists", "", "", "NG")])
def test_quantifier_applies_to_the_combined_source_collection(source, quantifier, first, second, expected):
    plan, raw = make_case(source, quantifier=quantifier)
    for table, value in zip(source["tables"], (first, second), strict=True):
        for row in (2, 3):
            table["cells"][f"A{row}"]["value"] = value
    assert checked(source, plan, raw).status == expected


@pytest.mark.parametrize("quantifier", ["forall", "exists"])
@pytest.mark.parametrize("empty", [False, True])
def test_second_scope_distinguishes_unknown_members_from_verified_nonrecords(source, quantifier, empty):
    plan, raw = make_case(source, quantifier=quantifier)
    scope = raw["uses"][0]["inputs"]["processes"]["scopes"][1]
    claims = [{"row": row, "reason": "Structure is unresolved", "evidence_locator": {"kind": "xlsx_cell", "sheet": "PFC-HF", "cell": f"A{row}"}} for row in (2, 3)]
    scope["excluded_rows" if empty else "unresolved_rows"] = [{**claim, "reason_type": "non_record"} for claim in claims] if empty else claims
    result = checked(source, plan, raw)
    assert result.status == ("PASS" if empty else "UNABLE")
    assert result.unknown == (0 if empty else 2)


@pytest.mark.parametrize("invalid", ["duplicate", "overlap", "outside_document", "sha", "table"])
def test_multiple_scopes_keep_identity_and_source_guards(source, invalid):
    plan, raw = make_case(source)
    if invalid == "duplicate":
        raw["uses"][0]["inputs"]["processes"]["scopes"].append({"binding_id": "PFC-SZ"})
    elif invalid == "overlap":
        raw["bindings"][1]["table"] = "PFC-SZ"
    elif invalid == "outside_document":
        raw["bindings"][1]["document_id"] = "outside"
    elif invalid == "sha":
        raw["bindings"][1]["source_sha256"] = "wrong"
    elif invalid == "table":
        raw["bindings"][1]["table"] = "outside"
    with pytest.raises(ValueError):
        checked(source, plan, raw)


def test_old_proof_output_is_rejected_instead_of_silently_adapted(source):
    with pytest.raises(ValueError):
        AuditTrace.model_validate({"proofs": [{"kind": "semantic", "audit_id": "target", "rule_id": "R1"}]})
