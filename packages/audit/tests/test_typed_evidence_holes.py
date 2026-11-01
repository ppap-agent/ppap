"""Fact types and original observations remain distinct through host computation."""

from copy import deepcopy
from io import BytesIO

import fitz
import pytest
from audit.document_tools import snapshot
from audit.evidence import EvidenceBudget
from audit.holes import EvidenceHole, TypedAnswer, validate_answer
from audit.models import RulePlan
from audit.trace import AuditTrace, evaluate_audit
from openpyxl import Workbook
from PIL import Image, ImageDraw


def native_source(tmp_path, value=12):
    path = tmp_path / "values.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Values"
    sheet.append(["Code", "Value"])
    sheet.append(["010", value])
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


def test_confirmed_empty_attribute_is_distinct_from_an_unanswered_attribute(tmp_path):
    document = native_source(tmp_path, value=None)
    empty = response(
        value="",
        origin="model",
        reason="已核查本对象的字段位置，字段确为空；不是尚未读取。",
        evidence=[{"document_id": "file", "locator": {"kind": "xlsx_cell", "sheet": "Values", "cell": "B2"}}],
    )
    before = deepcopy(document)
    assert validate_answer(request("string"), empty, {"file": document}) == ""
    assert document == before
    unanswered = empty.model_copy(update={"state": "unknown", "value": None})
    assert validate_answer(request("string"), unanswered, {"file": document}) is None
    with pytest.raises(ValueError, match="cannot carry"):
        validate_answer(request("string"), empty.model_copy(update={"state": "unknown"}), {"file": document})
    with pytest.raises(ValueError, match="differs from the original"):
        validate_answer(request("string"), empty.model_copy(update={"origin": "native"}), {"file": document})


@pytest.mark.parametrize("kind,value_type", [("semantic_key", "string"), ("semantic_value", "string"), ("interpretation", "interpretation")])
def test_an_empty_attribute_does_not_allow_empty_semantic_keys_or_interpretations(tmp_path, kind, value_type):
    with pytest.raises(ValueError, match="requested type"):
        validate_answer(request(value_type, kind=kind), response(value="", origin="model"), {"file": native_source(tmp_path)})


def test_range_answer_does_not_scan_every_unrelated_visual_object_for_each_cell(tmp_path):
    document = native_source(tmp_path)
    table = document["tables"][0]
    table["rows"] = 6000
    accesses = 0

    class ObservedVisual(dict):
        def __getitem__(self, key):
            nonlocal accesses
            accesses += key == "range"
            return super().__getitem__(key)

    table["visual_evidence"] = [
        ObservedVisual(source={"part": "xl/drawings/drawing1.xml", "anchor_index": index + 1}, kind="twoCellAnchor", content_observed=False, range=[5000 + index, 1, 5000 + index, 1])
        for index in range(1000)
    ]
    answer = response(
        value="", origin="model", reason="已核查局部属性的空值。", evidence=[{"document_id": "file", "locator": {"kind": "xlsx_range", "sheet": "Values", "start": "A1", "end": "A4096"}}]
    )
    assert validate_answer(request("string"), answer, {"file": document}, budget=EvidenceBudget(30_000)) == ""
    assert accesses < 50_000


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


def visual_case(tmp_path):
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
    return document, plan, trace


def test_visual_numeric_answer_is_distinct_from_unknown_native_value_and_host_compares(tmp_path):
    document, plan, trace = visual_case(tmp_path)
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


def test_judge_requests_the_visual_attribute_before_its_local_predicate(tmp_path):
    document, plan, trace = visual_case(tmp_path)
    program = plan.model_dump(mode="json")
    program["rules"][0]["program"]["require"] = {"kind": "judge", "question": "Does the observed value satisfy the stated local requirement?", "fields": ["value"]}
    plan = RulePlan.model_validate(program)
    before = deepcopy(document)
    initial = evaluate_audit(trace, {"file": document}, {"file": plan})
    assert initial.report.checks[0].status == "UNABLE"
    assert len(initial.needs) == 1
    attribute = initial.needs[0]
    assert (attribute.kind, attribute.value_type) == ("attribute", "number")
    raw = trace.model_dump(mode="json")
    evidence = [{"document_id": "file", "locator": {"kind": "pdf_cell", "page": 1, "row": 2, "column": 2}}]
    raw["answers"] = [{"kind": "fact", "need_id": attribute.id, "input_id": attribute.input_id, "origin": "model", "value": 12, "reason": "受控解释读取原件图像中的12。", "evidence": evidence}]
    with_value = evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})
    assert with_value.report.checks[0].status == "UNABLE"
    predicate = next(need for need in with_value.needs if need.kind == "semantic_predicate")
    assert predicate.context["source"]["fields"]["value"]["state"] == "unknown_visual_evidence"
    assert predicate.context["provided_attributes"]["value"]["value"] == 12
    assert predicate.context["provided_attributes"]["value"]["origin"] == "model"
    raw["answers"].append(
        {"kind": "fact", "need_id": predicate.id, "input_id": predicate.input_id, "origin": "model", "value": True, "reason": "根据原件属性解释，本局部要求满足。", "evidence": evidence}
    )
    final = evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})
    assert final.report.checks[0].status == "PASS"
    assert document == before
    raw["answers"] = [{"kind": "fact", "need_id": attribute.id, "input_id": attribute.input_id, "origin": "model", "state": "unknown", "value": None, "reason": "原图无法辨认。", "evidence": []}]
    unknown = evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})
    assert unknown.report.checks[0].status == "UNABLE"
    assert not any(need.kind == "semantic_predicate" for need in unknown.needs)


def test_target_judge_requests_the_target_ontology_type_instead_of_the_source_type(tmp_path):
    document, plan, trace = visual_case(tmp_path)
    program = plan.model_dump(mode="json")
    program["ontology"]["entities"][0]["attributes"]["value"] = "string"
    program["ontology"]["entities"].append({"id": "targets", "grain": "One matching measurement", "attributes": {"code": "string", "value": "number"}, "identity": ["code"]})
    program["ontology"]["relationships"] = [{"id": "matches", "kind": "association", "source": "records", "target": "targets", "meaning": "The source references a measurement by code."}]
    program["rules"][0]["program"]["require"] = {
        "kind": "related",
        "target": "targets",
        "relationship_id": "matches",
        "source_key": {"fields": ["code"]},
        "target_key": {"fields": ["code"]},
        "match": "any",
        "target_when": {"kind": "judge", "fields": ["value"], "question": "Is this measurement in the required target scope?"},
    }
    plan = RulePlan.model_validate(program)
    raw = trace.model_dump(mode="json")
    raw["bindings"].append({**raw["bindings"][0], "id": "target-body"})
    raw["bindings"][0]["columns"] = {"code": "A"}
    raw["uses"][0]["inputs"]["targets"] = {"kind": "bound", "scopes": [{"binding_id": "target-body"}]}
    initial = evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})
    attribute = next(need for need in initial.needs if need.kind == "attribute")
    assert attribute.value_type == "number"
    assert attribute.context["source"]["record"]["binding_id"] == "target-body"
    evidence = [{"document_id": "file", "locator": {"kind": "pdf_cell", "page": 1, "row": 2, "column": 2}}]
    raw["answers"] = [{"kind": "fact", "need_id": attribute.id, "input_id": attribute.input_id, "origin": "model", "value": 12, "reason": "原件目标属性图像为12。", "evidence": evidence}]
    with_value = evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})
    predicate = next(need for need in with_value.needs if need.kind == "semantic_predicate")
    raw["answers"].append({"kind": "fact", "need_id": predicate.id, "input_id": predicate.input_id, "origin": "model", "value": True, "reason": "目标属性满足本局部范围条件。", "evidence": evidence})
    final = evaluate_audit(AuditTrace.model_validate(raw), {"file": document}, {"file": plan})
    assert final.report.checks[0].status == "PASS"
    assert document["tables"][0]["cells"]["B2"]["state"] == "unknown_visual_evidence"


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
