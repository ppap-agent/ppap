"""Fact types and original observations remain distinct through host computation."""

from copy import deepcopy
from io import BytesIO

import fitz
import pytest
from openpyxl import Workbook
from PIL import Image, ImageDraw
from ppap_deep.semantic.document_tools import snapshot
from ppap_deep.semantic.holes import EvidenceHole, TypedAnswer, validate_answer
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.trace import AuditTrace, evaluate_audit


def native_source(tmp_path):
    path = tmp_path / "values.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Values"
    sheet.append(["Code", "Value"])
    sheet.append(["010", 12])
    workbook.save(path)
    workbook.close()
    return snapshot(path)


def request(value_type="number", **changes):
    raw = {
        "id": "a" * 64,
        "input_id": "b" * 64,
        "audit_id": "file",
        "rule_id": "R1",
        "kind": "attribute",
        "program_path": "require",
        "question": "请提供有来源的值",
        "value_type": value_type,
        "context": {"document_ids": ["file"], "objects": [{"binding_id": "records", "object_id": "010"}, {"binding_id": "records", "object_id": "020"}], "candidate_ids": ["process-code"]},
    }
    raw.update(changes)
    return EvidenceHole.model_validate(raw)


def response(**changes):
    raw = {
        "kind": "fact",
        "need_id": "a" * 64,
        "input_id": "b" * 64,
        "value": 12,
        "origin": "native",
        "reason": "原件单元格为数值12",
        "evidence": [{"document_id": "file", "locator": {"kind": "xlsx_cell", "sheet": "Values", "cell": "B2"}}],
    }
    raw.update(changes)
    return TypedAnswer.model_validate(raw)


def test_native_fact_keeps_original_numeric_type_and_rejects_a_different_value(tmp_path):
    document = native_source(tmp_path)
    assert validate_answer(request(), response(), {"file": document}) == 12
    with pytest.raises(ValueError, match="differs from the original"):
        validate_answer(request(), response(value=13), {"file": document})
    with pytest.raises(ValueError, match="requested type"):
        validate_answer(request(), response(value=True), {"file": document})


@pytest.mark.parametrize(
    "kind,value_type,value",
    [
        ("object_scope", "objects", [{"binding_id": "records", "object_id": "010"}, {"binding_id": "records", "object_id": "020"}]),
        ("relationship", "relationship", {"source": {"binding_id": "records", "object_id": "010"}, "target": {"binding_id": "records", "object_id": "020"}}),
        ("interpretation", "interpretation", "process-code"),
        ("semantic_predicate", "boolean", True),
    ],
)
def test_one_request_answer_contract_validates_objects_relations_interpretations_and_booleans(tmp_path, kind, value_type, value):
    assert validate_answer(request(value_type, kind=kind), response(value=value, origin="model"), {"file": native_source(tmp_path)}) == value


@pytest.mark.parametrize(
    "changes", [{"need_id": "c" * 64}, {"input_id": "d" * 64}, {"evidence": []}, {"evidence": [{"document_id": "other", "locator": {"kind": "xlsx_cell", "sheet": "Values", "cell": "B2"}}]}]
)
def test_wrong_request_input_or_source_cannot_provide_a_fact(tmp_path, changes):
    with pytest.raises(ValueError):
        validate_answer(request(), response(**changes), {"file": native_source(tmp_path)})


@pytest.mark.parametrize(
    "value_type,value",
    [
        ("objects", [{"binding_id": "records", "object_id": "missing"}]),
        ("relationship", {"source": {"binding_id": "records", "object_id": "010"}, "target": {"binding_id": "other", "object_id": "020"}}),
        ("interpretation", "unoffered-candidate"),
    ],
)
def test_object_endpoints_and_interpretation_values_are_confined_to_request(tmp_path, value_type, value):
    with pytest.raises(ValueError, match="requested type or object scope"):
        validate_answer(request(value_type), response(value=value, origin="model"), {"file": native_source(tmp_path)})


@pytest.mark.parametrize("state", ["unknown", "none_of_candidates"])
def test_unanswered_or_rejected_candidates_do_not_supply_a_default_fact(tmp_path, state):
    assert validate_answer(request(), response(value=None, state=state, evidence=[]), {"file": native_source(tmp_path)}) is None
    with pytest.raises(ValueError, match="cannot carry"):
        validate_answer(request(), response(value=12, state=state), {"file": native_source(tmp_path)})


def visual_source(tmp_path):
    image = Image.new("RGB", (120, 30), "white")
    ImageDraw.Draw(image).text((5, 5), "12", fill="black")
    stream = BytesIO()
    image.save(stream, format="PNG")
    document = fitz.open()
    page = document.new_page(width=200, height=150)
    for x in (10, 100, 190):
        page.draw_line((x, 10), (x, 90))
    for y in (10, 50, 90):
        page.draw_line((10, y), (190, y))
    page.insert_text((15, 30), "Code")
    page.insert_text((105, 30), "Value")
    page.insert_text((15, 70), "A")
    page.insert_image(fitz.Rect(105, 55, 180, 80), stream=stream.getvalue())
    path = tmp_path / "visual.pdf"
    document.save(path)
    document.close()
    return snapshot(path)


def test_visual_numeric_answer_is_distinct_from_unknown_native_value_and_host_compares(tmp_path):
    document = visual_source(tmp_path)
    assert document["tables"][0]["cells"]["B2"]["state"] == "unknown_visual_evidence"
    plan = RulePlan.model_validate(
        {
            "source_sha256": "rule",
            "plan_revision": "a" * 64,
            "ontology": {"entities": [{"id": "records", "grain": "one code record", "attributes": {"code": "string", "value": "number"}, "identity": ["code"]}]},
            "rules": [
                {
                    "id": "R1",
                    "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                    "program": {"kind": "records", "source": "records", "quantifier": "forall", "require": {"kind": "value_test", "field": "value", "op": "gt", "value": 10}},
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "No record is unknown.",
                }
            ],
        }
    )
    trace = AuditTrace.model_validate(
        {
            "bindings": [
                {
                    "id": "body",
                    "document_id": "file",
                    "source_sha256": document["source"]["sha256"],
                    "table": "page-1",
                    "header_rows": [1],
                    "first_row": 2,
                    "last_row": 2,
                    "columns": {"code": "A", "value": "B"},
                    "mapping_basis": "Page headers",
                    "boundary_basis": "Complete grid body",
                }
            ],
            "uses": [{"audit_id": "file", "rule_id": "R1", "inputs": {"records": {"kind": "bound", "scopes": [{"binding_id": "body"}]}}}],
        }
    )
    initial = evaluate_audit(trace, {"file": document}, {"file": plan})
    assert initial.report.checks[0].status == "UNABLE"
    need = initial.needs[0]
    raw = trace.model_dump(mode="json")
    raw["answers"] = [
        {
            "kind": "fact",
            "need_id": need.id,
            "input_id": need.input_id,
            "origin": "model",
            "value": 12,
            "reason": "受控模型对原件图像的解释，非原生值",
            "evidence": [{"document_id": "file", "locator": {"kind": "pdf_cell", "page": 1, "row": 2, "column": 2}}],
        }
    ]
    before = deepcopy(document)
    result = evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})
    assert result.report.checks[0].status == "PASS"
    assert result.observations[0]["inferred_fact"]["origin"] == "model"
    assert document == before
    raw["answers"][0]["origin"] = "native"
    with pytest.raises(ValueError, match="known native"):
        evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})


def test_native_fact_answer_resolves_only_the_requested_attribute_and_host_compares(tmp_path):
    document = native_source(tmp_path)
    plan = RulePlan.model_validate(
        {
            "source_sha256": "rule",
            "plan_revision": "a" * 64,
            "ontology": {"entities": [{"id": "records", "grain": "one code object", "attributes": {"code": "string", "value": "number"}, "identity": ["code"]}]},
            "rules": [
                {
                    "id": "R1",
                    "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                    "program": {"kind": "records", "source": "records", "quantifier": "forall", "require": {"kind": "value_test", "field": "value", "op": "gt", "value": 10}},
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "No records is unknown.",
                }
            ],
        }
    )
    trace = AuditTrace.model_validate(
        {
            "bindings": [
                {
                    "id": "body",
                    "document_id": "file",
                    "source_sha256": document["source"]["sha256"],
                    "table": "Values",
                    "header_rows": [1],
                    "first_row": 2,
                    "last_row": 2,
                    "columns": {"code": "A"},
                    "field_holes": {"value": {"state": "unresolved", "reason": "精确值位置尚未指定"}},
                    "mapping_basis": "Code header",
                    "boundary_basis": "Complete body",
                }
            ],
            "uses": [{"audit_id": "file", "rule_id": "R1", "inputs": {"records": {"kind": "bound", "scopes": [{"binding_id": "body"}]}}}],
        }
    )
    initial = evaluate_audit(trace, {"file": document}, {"file": plan})
    assert initial.report.checks[0].status == "UNABLE"
    need = initial.needs[0]
    raw = trace.model_dump(mode="json")
    raw["answers"] = [response(need_id=need.id, input_id=need.input_id).model_dump(mode="json")]
    result = evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})
    assert result.report.checks[0].status == "PASS"
    assert result.observations[0]["inferred_fact"]["origin"] == "native"
    assert trace.bindings[0].field_holes["value"].state == "unresolved"


def test_same_numeric_value_in_another_object_cannot_be_used_as_this_objects_native_fact(tmp_path):
    path = tmp_path / "two-objects.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Values"
    sheet.append(["Code", "Value"])
    sheet.append(["010", 12])
    sheet.append(["020", 12])
    workbook.save(path)
    workbook.close()
    hole = request(context={"document_ids": ["file"], "source_scope": {"document_id": "file", "table": "Values", "rows": [2]}})
    wrong = response(evidence=[{"document_id": "file", "locator": {"kind": "xlsx_cell", "sheet": "Values", "cell": "B3"}}])
    with pytest.raises(ValueError, match="outside requested object"):
        validate_answer(hole, wrong, {"file": snapshot(path)})


def test_evaluator_rejects_borrowing_another_objects_native_number_to_turn_ng_into_pass(tmp_path):
    path = tmp_path / "two-records.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Values"
    sheet.append(["Code", "Value"])
    sheet.append(["010", 2])
    sheet.append(["020", 12])
    workbook.save(path)
    workbook.close()
    document = snapshot(path)
    plan = RulePlan.model_validate(
        {
            "source_sha256": "rule",
            "plan_revision": "a" * 64,
            "ontology": {"entities": [{"id": "records", "grain": "one code record", "attributes": {"code": "string", "value": "number"}, "identity": ["code"]}]},
            "rules": [
                {
                    "id": "R1",
                    "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                    "program": {"kind": "records", "source": "records", "quantifier": "forall", "require": {"kind": "value_test", "field": "value", "op": "gt", "value": 10}},
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "No record is unknown.",
                }
            ],
        }
    )
    trace = AuditTrace.model_validate(
        {
            "bindings": [
                {
                    "id": "body",
                    "document_id": "file",
                    "source_sha256": document["source"]["sha256"],
                    "table": "Values",
                    "header_rows": [1],
                    "first_row": 2,
                    "last_row": 3,
                    "columns": {"code": "A"},
                    "field_holes": {"value": {"state": "unresolved", "reason": "Value location not yet mapped"}},
                    "mapping_basis": "Code header",
                    "boundary_basis": "Complete two-record body",
                }
            ],
            "uses": [{"audit_id": "file", "rule_id": "R1", "inputs": {"records": {"kind": "bound", "scopes": [{"binding_id": "body"}]}}}],
        }
    )
    initial = evaluate_audit(trace, {"file": document}, {"file": plan})
    raw = trace.model_dump(mode="json")
    raw["answers"] = [
        response(need_id=need.id, input_id=need.input_id, value=12, evidence=[{"document_id": "file", "locator": {"kind": "xlsx_cell", "sheet": "Values", "cell": "B3"}}]).model_dump(mode="json")
        for need in initial.needs
    ]
    with pytest.raises(ValueError, match="outside requested object"):
        evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})
    first = raw["answers"][0]
    first["value"] = 2
    first["evidence"][0]["locator"]["cell"] = "B2"
    result = evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})
    assert (result.report.checks[0].status, result.report.checks[0].checked) == ("NG", 2)


@pytest.mark.parametrize("kind,value_type,value", [("interpretation", "interpretation", "process-code"), ("object_scope", "objects", "by-code")])
def test_structural_selection_cannot_attribute_its_choice_to_sources_outside_request(tmp_path, kind, value_type, value):
    document = native_source(tmp_path)
    context = {"document_ids": ["file"], "candidate_ids": ["process-code"], "object_choices": [{"id": "by-code"}], "source_scope": {"document_id": "file", "table": "Values", "rows": [1]}}
    hole = request(value_type, kind=kind, context=context)
    with pytest.raises(ValueError, match="outside requested object"):
        validate_answer(hole, response(origin="model", value=value), {"file": document})


def test_native_range_citation_keeps_numeric_type_instead_of_text_rendering(tmp_path):
    document = native_source(tmp_path)
    evidence = [{"document_id": "file", "locator": {"kind": "xlsx_range", "sheet": "Values", "start": "B2", "end": "B2"}}]
    assert validate_answer(request(), response(evidence=evidence), {"file": document}) == 12
    with pytest.raises(ValueError, match="differs from the original"):
        validate_answer(request("string"), response(evidence=evidence, value="12"), {"file": document})
