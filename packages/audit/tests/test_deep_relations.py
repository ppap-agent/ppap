"""Cross-file facts use the final Binding and preserve local uncertainty."""

import json
import subprocess
import sys
from copy import deepcopy

import pytest
from audit.document_tools import bind, run_queries, snapshot
from audit.models import Related, RulePlan
from audit.trace import AuditTrace, Binding, evaluate_audit
from openpyxl import Workbook


def source(tmp_path, identity, rows):
    path = tmp_path / f"{identity}.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Records"
    sheet.append(["code", "name", "value"])
    for row in rows:
        sheet.append(row)
    book.save(path)
    book.close()
    document = snapshot(path)
    binding = Binding(
        id=identity,
        document_id=identity,
        source_sha256=document["source"]["sha256"],
        table="Records",
        header_rows=[1],
        first_row=2,
        last_row=len(rows) + 1,
        columns={"code": "A", "name": "B", "value": "C"},
        mapping_basis="The first row labels each field.",
        boundary_basis="All source records below the header.",
    )
    return document, binding


def relation_inputs(tmp_path, left=None, right=None):
    a, ab = source(tmp_path, "pfd", left if left is not None else [(10, "Receiving", "CC"), (20, "Inspection", "SC"), (30, "Packing", "CC")])
    b, bb = source(tmp_path, "cp", right if right is not None else [(10, "Receiving", "CC"), (20, "Inspection", "SC"), (40, "Shipping", "CC")])
    return {"pfd": a, "cp": b}, [ab, bb]


def request(**changes):
    return Related.model_validate({"kind": "related", "target": "target", "source_key": {"fields": ["code"]}, "target_key": {"fields": ["code"]}, "match": "any", **changes})


def artifacts(documents, bindings, selected=None, *, source_ids=None, target_ids=None, target_scope=None):
    source_ids, target_ids = source_ids or ["pfd"], target_ids or ["cp"]
    plan = RulePlan.model_validate(
        {
            "source_sha256": "rules",
            "plan_revision": "a" * 64,
            "rules": [
                {
                    "id": "R1",
                    "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                    "program": {"kind": "records", "source": "source", "quantifier": "forall", "require": (selected or request()).model_dump()},
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "The effective clause does not define an empty result",
                }
            ],
        }
    )
    trace = AuditTrace.model_validate(
        {
            "bindings": [binding.model_dump() for binding in bindings],
            "uses": [
                {
                    "audit_id": "cp",
                    "rule_id": "R1",
                    "inputs": {
                        "source": {"kind": "bound", "scopes": [{"binding_id": identity} for identity in source_ids]},
                        "target": {"kind": "bound", "scopes": [{"binding_id": identity, **(target_scope or {})} for identity in target_ids]},
                    },
                }
            ],
        }
    )
    return plan, trace


def run(documents, bindings, selected=None, **changes):
    plan, trace = artifacts(documents, bindings, selected, **changes)
    return evaluate_audit(trace, documents, {"cp": plan})


def facts(result):
    return [record["relation"] for record in result.observations if "relation" in record]


def test_actual_native_files_compare_values_and_preserve_direction_and_original_locations(tmp_path):
    documents, bindings = relation_inputs(tmp_path)
    before = deepcopy(documents)
    result = run(documents, bindings)
    assert [record["state"] for record in facts(result)] == ["matched", "matched", "missing"]
    assert facts(result)[0]["candidates"][0]["target_key"]["code"]["evidence"]["citation"] == {"document_id": "cp", "locator": {"kind": "xlsx_cell", "sheet": "Records", "cell": "A2"}}
    reverse = run(documents, bindings, source_ids=["cp"], target_ids=["pfd"])
    assert facts(reverse)[-1]["source_values"]["code"]["value"] == 40
    assert documents == before


@pytest.mark.parametrize("left,right,expected", [(0, 0, "PASS"), (False, False, "PASS"), (False, 0, "NG"), ("01", 1, "NG"), ("1", 1, "NG")])
def test_native_workbooks_preserve_booleans_zero_and_text_identifiers(tmp_path, left, right, expected):
    documents, bindings = relation_inputs(tmp_path, [(left, "A", "CC")], [(right, "A", "CC")])
    assert run(documents, bindings).report.checks[0].status == expected


def test_key_concatenation_and_text_normalization_are_cached_explicit_operations(tmp_path):
    documents, bindings = relation_inputs(tmp_path, [(10, "Receiving", "CC")], [("10 receiving", "A", "CC")])
    selected = request(source_key={"fields": ["code", "name"], "separator": " "})
    assert run(documents, bindings, selected).report.checks[0].status == "NG"
    selected.source_key.normalization = selected.target_key.normalization = "casefold_whitespace"
    assert run(documents, bindings, selected).report.checks[0].status == "PASS"


@pytest.mark.parametrize("match,status", [("any", "PASS"), ("all", "NG"), ("one_to_one", "NG")])
def test_duplicate_target_cardinality_survives_real_reading(tmp_path, match, status):
    documents, bindings = relation_inputs(tmp_path, [(10, "A", "CC")], [(10, "A", "CC"), (10, "B", "SC")])
    selected = request(match=match, comparisons=[{"source_field": "value", "target_field": "value", "mode": "exact"}])
    result = run(documents, bindings, selected)
    assert result.report.checks[0].status == status
    assert len(facts(result)[0]["candidates"]) == 2


def test_unknown_native_formula_key_cannot_prove_missing_target(tmp_path):
    documents, bindings = relation_inputs(tmp_path, [(10, "A", "CC")], [("=external!A1", "A", "CC")])
    result = run(documents, bindings)
    assert result.report.checks[0].status == "UNABLE"
    assert facts(result)[0]["possible_targets"] == [{"binding_id": "cp", "row": 2}]


def test_known_composite_key_component_keeps_unknown_formula_impact_local(tmp_path):
    documents, bindings = relation_inputs(tmp_path, [(10, "Incoming", "CC"), (99, "Incoming", "CC")], [(99, "=external!A1", "CC")])
    selected = request(source_key={"fields": ["code", "name"]}, target_key={"fields": ["code", "name"]})
    result = run(documents, bindings, selected)
    assert [record["state"] for record in facts(result)] == ["missing", "unresolved"]
    assert (result.report.checks[0].status, result.report.checks[0].unknown) == ("NG", 1)


def test_multiple_original_source_files_remain_distinct_without_collapsing_rows(tmp_path):
    documents, bindings = relation_inputs(tmp_path)
    extra, binding = source(tmp_path, "pfd-second", [(40, "Shipping", "CC")])
    documents["pfd-second"] = extra
    bindings.append(binding)
    result = run(documents, bindings, source_ids=["pfd", "pfd-second"])
    assert [record["state"] for record in facts(result)] == ["matched", "matched", "missing", "matched"]
    assert facts(result)[-1]["source"] == {"binding_id": "pfd-second", "row": 2}


def test_local_semantic_value_question_contains_original_pair_evidence(tmp_path):
    documents, bindings = relation_inputs(tmp_path, [(10, "A", "100% visual check")], [(10, "A", "全数目视")])
    result = run(documents, bindings, request(comparisons=[{"source_field": "value", "target_field": "value", "mode": "semantic"}]))
    assert result.report.checks[0].status == "UNABLE"
    need = result.needs[0]
    assert need.context["source"]["fields"]["value"]["evidence"]["citation"]["document_id"] == "pfd"
    assert need.context["target"]["fields"]["value"]["evidence"]["citation"]["document_id"] == "cp"


def test_audit_cli_returns_counts_and_question_ids_without_echoing_raw_values(tmp_path):
    documents, bindings = relation_inputs(tmp_path, [("private Incoming", "A", "private-value")], [("收货", "A", "target-value")])
    plan, trace = artifacts(documents, bindings, request(key_match="semantic"))
    for name, value in (("documents.json", documents), ("plans.json", {"r": plan.model_dump(mode="json")}), ("targets.json", {"cp": "r"}), ("execution.json", trace.model_dump(mode="json"))):
        (tmp_path / name).write_text(json.dumps(value))
    completed = subprocess.run(
        [sys.executable, "-m", "audit.audit_tools", "evaluate", "execution.json", "--out", "evaluation.json"], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    brief = json.loads(completed.stdout)
    saved = json.loads((tmp_path / "evaluation.json").read_text())
    assert brief["pending_questions"] == 1
    assert "private Incoming" not in completed.stdout and "private-value" not in completed.stdout
    assert saved["needs"][0]["context"]["source"]["fields"]["code"]["value"] == "private Incoming"
    view = subprocess.run([sys.executable, "-m", "audit.audit_tools", "view", "evaluation.json", "--limit", "1"], cwd=tmp_path, capture_output=True, text=True, check=True)
    assert json.loads(view.stdout)["count"] == 1


def test_sql_remains_auxiliary_and_exposes_raw_unknown_states(tmp_path):
    documents, bindings = relation_inputs(tmp_path, [(10, "A", "=1+1")])
    raw = bindings[0].model_dump()
    raw["columns"] = {'频率"值': "C"}
    materialized = bind(documents["pfd"], raw)
    result = run_queries({"pfd": materialized}, [{"id": "values", "sql": 'SELECT "频率""值", states_json, scope_state FROM pfd'}])
    row = result["queries"][0]["rows"][0]
    assert row['频率"值'] is None
    assert json.loads(row["states_json"]) == {'频率"值': "unknown_formula_cache"}
    assert row["scope_state"] == "known"


def test_old_sql_binding_contract_is_rejected(tmp_path):
    documents, _ = relation_inputs(tmp_path)
    with pytest.raises(ValueError):
        bind(documents["pfd"], {"table": "Records", "header_rows": "1", "body_rows": "2:4", "columns": {"code": "A"}})


def test_binding_cli_materializes_the_same_physical_range_with_compact_output(tmp_path):
    documents, bindings = relation_inputs(tmp_path)
    for name, value in (("documents.json", documents), ("binding.json", bindings[0].model_dump())):
        (tmp_path / name).write_text(json.dumps(value))
    result = subprocess.run(
        [sys.executable, "-m", "audit.document_tools", "bind", "documents.json", "--binding", "binding.json", "--out", "bound.json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {"output": "bound.json", "binding_id": "pfd", "rows": 3, "unknown_cells": 0, "unresolved_rows": 0}
    assert len(json.loads((tmp_path / "bound.json").read_text())["records"]) == 3
