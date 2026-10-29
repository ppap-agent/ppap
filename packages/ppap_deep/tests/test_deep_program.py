"""Public evaluator regressions: full coverage, late binding and local judgment."""

from copy import deepcopy

import pytest
from ppap_deep.semantic.audit_tools import prepare_audit
from ppap_deep.semantic.bindings import Binding, BoundCollection
from ppap_deep.semantic.checker_tools import CheckRequest, check_fields
from ppap_deep.semantic.document_tools import TableReader, bind
from ppap_deep.semantic.evidence import EvidenceBudget
from ppap_deep.semantic.models import Key, RulePlan
from ppap_deep.semantic.trace import AuditTrace, evaluate_audit, validate_trace


def table(identity="source", rows=None, *, fields=None):
    fields = fields or {"code": "A", "name": "B", "value": "C"}
    rows = [(10, "Receiving", "CC"), (20, "Inspection", "SC")] if rows is None else rows
    cells = {f"{column}1": {"value": field, "state": "known"} for field, column in fields.items()}
    columns = list(fields.values())
    for row_number, values in enumerate(rows, 2):
        for column, value in zip(columns, values, strict=True):
            cells[f"{column}{row_number}"] = value if isinstance(value, dict) else {"value": value, "state": "known"}
    document = {"source": {"file": identity + ".xlsx", "format": ".xlsx", "sha256": identity}, "tables": [{"id": "Records", "rows": len(rows) + 1, "cols": len(columns), "cells": cells, "merges": []}]}
    binding = Binding(
        id=identity,
        document_id=identity,
        source_sha256=identity,
        table="Records",
        header_rows=[1],
        first_row=2,
        last_row=len(rows) + 1,
        columns=fields,
        mapping_basis="原件第一行字段表头",
        boundary_basis="第一行之后的完整物理记录范围",
    )
    return document, binding


def rule(require=None, **changes):
    program = {"kind": "records", "source": "source", "quantifier": "forall", "require": require or {"kind": "value_test", "field": "value", "op": "not_empty"}}
    program.update(changes)
    return RulePlan.model_validate(
        {
            "source_sha256": "rules",
            "plan_revision": "a" * 64,
            "rules": [
                {
                    "id": "R1",
                    "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                    "program": program,
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "原文未规定零适用对象的结论。",
                }
            ],
        }
    )


def bound(binding, **scope):
    return {"kind": "bound", "scopes": [{"binding_id": binding.id, **scope}]}


def certificate(bindings, inputs, **changes):
    return AuditTrace.model_validate({"bindings": [item.model_dump() for item in bindings], "uses": [{"audit_id": "source", "rule_id": "R1", "inputs": inputs}], **changes})


def evaluate(trace, documents, plan=None):
    return evaluate_audit(trace, documents, {"source": plan or rule()})


def related(**changes):
    return {"kind": "related", "target": "target", "source_key": {"fields": ["code"]}, "target_key": {"fields": ["code"]}, "match": "any", **changes}


def answered(trace, evaluated, value="true", **changes):
    raw = trace.model_dump(mode="json")
    raw["answers"] = [{"need_id": need.id, "value": value, "reason": "已核对原始记录", **changes} for need in evaluated.needs]
    return AuditTrace.model_validate(raw)


def test_host_evaluates_all_rows_without_model_row_verdicts():
    document, binding = table(rows=[(10, "A", "CC"), (20, "B", None), (30, "C", {"value": None, "state": "unknown_formula_cache"})])
    trace = certificate([binding], {"source": bound(binding)})
    result = evaluate(trace, {"source": document})
    check = result.report.checks[0]
    assert (check.status, check.total, check.checked, check.unknown) == ("NG", 3, 2, 1)
    assert check.evidence[0].locator.cell == "C3"
    assert [item["record"]["row"] for item in result.observations] == [2, 3, 4]
    assert not result.needs


@pytest.mark.parametrize("left,right,status", [(10.0, 10, "PASS"), (10, 10.0, "PASS"), (False, 0, "NG"), ("10", 10, "NG"), ("010", "10", "NG"), (0, False, "NG")])
def test_native_numeric_domain_does_not_coerce_text_or_boolean(left, right, status):
    a, ab = table(rows=[(left, "A", "CC")])
    b, bb = table("target", [(right, "A", "CC")])
    trace = certificate([ab, bb], {"source": bound(ab), "target": bound(bb)})
    assert evaluate(trace, {"source": a, "target": b}, rule(related())).report.checks[0].status == status
    predicate = rule({"kind": "value_test", "field": "code", "op": "eq", "value": right})
    assert evaluate(certificate([ab], {"source": bound(ab)}), {"source": a}, predicate).report.checks[0].status == status


def test_scope_membership_is_local_and_physical_binding_keeps_group_heading():
    a, ab = table(rows=[(10, "Process heading", None), (10.1, "Characteristic", "SC")])
    b, bb = table("target", [(10, "Process heading", "CC"), (10.1, "Characteristic", "SC")])
    exclusion = {"row": 2, "reason_type": "non_record", "reason": "此用途仅审核特性明细", "evidence_locator": {"kind": "xlsx_cell", "sheet": "Records", "cell": "B2"}}
    plan = rule(related())
    second = plan.rules[0].model_copy(deep=True)
    second.id = "R2"
    second.program.require = {"kind": "value_test", "field": "value", "op": "not_empty"}
    raw_plan = plan.model_dump(mode="json")
    raw_plan["rules"].append(
        {**raw_plan["rules"][0], "id": "R2", "program": {"kind": "records", "source": "source", "quantifier": "forall", "require": {"kind": "value_test", "field": "value", "op": "not_empty"}}}
    )
    plan = RulePlan.model_validate(raw_plan)
    trace = certificate([ab, bb], {"source": bound(ab), "target": bound(bb)})
    raw = trace.model_dump(mode="json")
    raw["uses"].append({"audit_id": "source", "rule_id": "R2", "inputs": {"source": bound(ab, excluded_rows=[exclusion])}})
    before = deepcopy(a)
    result = evaluate(AuditTrace.model_validate(raw), {"source": a, "target": b}, plan)
    assert [check.status for check in result.report.checks] == ["PASS", "PASS"]
    assert len(bind(a, ab.model_dump())["records"]) == 2
    assert [item["role"] for item in result.observations if item["rule_id"] == "R2"] == ["excluded", "applicable"]
    assert a == before


def test_missing_semantic_answers_expand_to_complete_unknown_coverage():
    a, ab = table(rows=[(i, "Operation", "CC") for i in range(58)])
    plan = rule({"kind": "judge", "question": "此控制方法是否满足本条明确要求？", "fields": ["value"]})
    trace = certificate([ab], {"source": bound(ab)})
    initial = evaluate(trace, {"source": a}, plan)
    raw = trace.model_dump(mode="json")
    raw["answers"] = [{"need_id": need.id, "value": "true", "reason": "已核对方法"} for need in initial.needs[:11]]
    result = evaluate(AuditTrace.model_validate(raw), {"source": a}, plan)
    check = result.report.checks[0]
    assert (check.status, check.total, check.checked, check.unknown) == ("UNABLE", 58, 11, 47)
    assert len(result.observations) == 58


@pytest.mark.parametrize("empty_status", ["PASS", "NA", "UNABLE"])
def test_passed_prerequisite_never_substitutes_for_unbound_body(empty_status):
    a, ab = table()
    plan = rule(precondition={"kind": "collection_exists", "input": "support"})
    plan.rules[0].empty_scope_status = empty_status
    trace = certificate([ab], {"support": bound(ab)})
    result = evaluate(trace, {"source": a}, plan)
    assert (result.report.checks[0].status, result.report.checks[0].unknown) == ("UNABLE", 1)
    assert result.observations[0]["value"] is True


def test_known_absence_is_false_while_unresolved_presence_is_unknown():
    a, _ = table()
    plan = rule(precondition={"kind": "collection_exists", "input": "required"})
    trace = certificate([], {"required": {"kind": "absent", "searched_documents": ["source"], "reason": "已检查提交的全部文件"}})
    result = evaluate(trace, {"source": a}, plan)
    assert result.report.checks[0].status == "NG"
    assert result.report.checks[0].unknown == 1
    unresolved = certificate([], {"required": {"kind": "unresolved", "reason": "文件身份未确认"}})
    assert evaluate(unresolved, {"source": a}, plan).report.checks[0].status == "UNABLE"


@pytest.mark.parametrize("quantifier", ["forall", "exists"])
def test_known_match_cannot_erase_an_unfinished_scope(quantifier):
    a, ab = table(rows=[(10, "A", "CC")])
    trace = certificate([ab], {"source": {**bound(ab), "unresolved_scopes": ["第二张工序表尚未定位"]}})
    result = evaluate(trace, {"source": a}, rule(quantifier=quantifier))
    assert (result.report.checks[0].status, result.report.checks[0].unknown) == ("UNABLE", 1)


@pytest.mark.parametrize("empty_status", ["PASS", "NA", "UNABLE"])
def test_only_known_zero_trigger_set_uses_empty_policy(empty_status):
    a, ab = table()
    plan = rule(when={"kind": "value_test", "field": "value", "op": "eq", "value": "NEVER"})
    plan.rules[0].empty_scope_status = empty_status
    trace = certificate([ab], {"source": bound(ab)})
    assert evaluate(trace, {"source": a}, plan).report.checks[0].status == empty_status
    a["tables"][0]["cells"]["C2"] = {"value": None, "state": "unknown_formula_cache"}
    check = evaluate(trace, {"source": a}, plan).report.checks[0]
    assert (check.status, check.unknown) == ("UNABLE", 1)


@pytest.mark.parametrize("match,status", [("any", "PASS"), ("all", "NG"), ("one_to_one", "NG")])
def test_duplicate_targets_follow_cached_cardinality(match, status):
    a, ab = table(rows=[(10, "A", "CC")])
    b, bb = table("target", [(10, "A", "CC"), (10, "B", "SC")])
    plan = rule(related(match=match, comparisons=[{"source_field": "value", "target_field": "value", "mode": "exact"}]))
    trace = certificate([ab, bb], {"source": bound(ab), "target": bound(bb)})
    assert evaluate(trace, {"source": a, "target": b}, plan).report.checks[0].status == status


def test_known_unequal_key_components_keep_target_membership_uncertainty_local():
    a, ab = table(rows=[(10, "A", "CC"), (99, "A", "CC")])
    b, bb = table("target", [(99, "A", {"value": None, "state": "unknown_formula_cache"})])
    plan = rule(related(target_when={"kind": "value_test", "field": "value", "op": "not_empty"}))
    result = evaluate(certificate([ab, bb], {"source": bound(ab), "target": bound(bb)}), {"source": a, "target": b}, plan)
    assert [item["value"] for item in result.observations] == [False, None]
    assert (result.report.checks[0].status, result.report.checks[0].unknown) == ("NG", 1)


def test_semantic_comparison_asks_only_about_the_actual_pair_and_keeps_source():
    a, ab = table(rows=[(10, "A", "100% visual check")])
    b, bb = table("target", [(10, "A", "全数目视")])
    plan = rule(related(comparisons=[{"source_field": "value", "target_field": "value", "mode": "semantic"}]))
    trace = certificate([ab, bb], {"source": bound(ab), "target": bound(bb)})
    initial = evaluate(trace, {"source": a, "target": b}, plan)
    assert len(initial.needs) == 1
    need = initial.needs[0]
    assert need.context["source"]["record"] == {"binding_id": "source", "row": 2}
    assert need.context["target"]["record"] == {"binding_id": "target", "row": 2}
    final = evaluate(answered(trace, initial), {"source": a, "target": b}, plan)
    assert final.report.checks[0].status == "PASS"
    assert {citation.document_id for citation in final.report.checks[0].evidence} == {"source", "target"}
    assert evaluate(answered(trace, initial, "false"), {"source": a, "target": b}, plan).report.checks[0].status == "NG"


def test_changed_binding_invalidates_old_local_answers():
    a, ab = table()
    plan = rule({"kind": "judge", "question": "方法是否满足要求？", "fields": ["value"]})
    trace = certificate([ab], {"source": bound(ab)})
    initial = evaluate(trace, {"source": a}, plan)
    complete = answered(trace, initial)
    complete.bindings[0].columns["value"] = "B"
    with pytest.raises(ValueError, match="stale"):
        evaluate(complete, {"source": a}, plan)


def test_missing_whole_use_produces_unknown_instead_of_placeholder_pass():
    a, _ = table()
    check = evaluate(AuditTrace(uses=[]), {"source": a}).report.checks[0]
    assert (check.status, check.unknown) == ("UNABLE", 1)


def test_format_key_preserves_punctuation_and_numeric_native_equivalence():
    a, ab = table(rows=[(10.0, "SC", "Length")])
    b, bb = table("target", [("【10】【SC】Length", "unused", "CC")])
    plan = rule(related(source_key={"fields": ["code", "name", "value"], "template": "【{code}】【{name}】{value}"}))
    trace = certificate([ab, bb], {"source": bound(ab), "target": bound(bb)})
    assert evaluate(trace, {"source": a, "target": b}, plan).report.checks[0].status == "PASS"
    b["tables"][0]["cells"]["A2"]["value"] = "[10][SC]Length"
    assert evaluate(trace, {"source": a, "target": b}, plan).report.checks[0].status == "NG"


@pytest.mark.parametrize("template", ["{code.__class__}", "{code[0]}", "{code!r}", "{code:04}", "{not_declared}", "literal"])
def test_key_templates_cannot_execute_other_access_or_omit_operands(template):
    with pytest.raises(ValueError):
        Key(fields=["code"], template=template)


@pytest.mark.parametrize("change", ["sha", "document", "overlap", "duplicate", "old_row_claim", "outside_use"])
def test_illegal_source_or_identity_is_a_protocol_error(change):
    a, ab = table()
    raw = certificate([ab], {"source": bound(ab)}).model_dump(mode="json")
    if change == "sha":
        raw["bindings"][0]["source_sha256"] = "wrong"
    elif change == "document":
        raw["bindings"][0]["document_id"] = "outside"
    elif change == "overlap":
        raw["bindings"].append({**raw["bindings"][0], "id": "overlap"})
        raw["uses"][0]["inputs"]["source"]["scopes"].append({"binding_id": "overlap"})
    elif change == "duplicate":
        raw["uses"].append(deepcopy(raw["uses"][0]))
    elif change == "old_row_claim":
        raw["bindings"][0]["excluded_rows"] = []
    else:
        raw["uses"][0]["audit_id"] = "outside"
    with pytest.raises(ValueError):
        evaluate(AuditTrace.model_validate(raw), {"source": a})


def hole(row=3):
    return {"row": row, "reason": "The record role remains uncertain", "evidence_locator": {"kind": "xlsx_cell", "sheet": "Records", "cell": f"A{row}"}}


@pytest.mark.parametrize("quantifier", ["forall", "exists"])
def test_unresolved_record_role_is_unknown_even_with_known_matching_values(quantifier):
    a, ab = table()
    trace = certificate([ab], {"source": bound(ab, unresolved_rows=[hole()])})
    check = evaluate(trace, {"source": a}, rule(quantifier=quantifier)).report.checks[0]
    assert (check.status, check.total, check.checked, check.unknown, check.diagnostic) == ("UNABLE", 2, 1, 1, "unsupported_structure")
    assert check.evidence[0].locator.cell == "A3"


@pytest.mark.parametrize("state,required,expected", [("absent", True, "NG"), ("absent", False, "UNABLE"), ("unresolved", True, "UNABLE")])
def test_known_absence_and_unresolved_mapping_take_distinct_branches(state, required, expected):
    a, ab = table()
    raw = ab.model_dump()
    raw["columns"].pop("value")
    raw["field_holes"] = {"value": {"state": state, "reason": "All actual header columns were inspected"}}
    ab = type(ab).model_validate(raw)
    plan = rule(precondition={"kind": "column_exists", "input": "source", "field": "value"} if required else None)
    trace = certificate([ab], {"source": bound(ab)})
    result = evaluate(trace, {"source": a}, plan)
    assert result.report.checks[0].status == expected
    assert result.report.checks[0].unknown >= 2
    if required:
        assert result.report.checks[0].evidence[0].locator.kind == "xlsx_range"


@pytest.mark.parametrize("state", ["unknown_formula_cache", "unknown_excel_error"])
@pytest.mark.parametrize("merged", [False, True])
def test_unreadable_exclusion_cannot_hide_an_unknown_record(state, merged):
    a, ab = table()
    a["tables"][0]["cells"]["A3"] = {"value": None, "state": state}
    if merged:
        a["tables"][0]["merges"] = [[2, 1, 3, 1]]
        a["tables"][0]["cells"]["A2"] = {"value": None, "state": state}
    trace = certificate([ab], {"source": bound(ab, excluded_rows=[{**hole(), "reason_type": "footer"}])})
    with pytest.raises(ValueError, match="known source evidence"):
        evaluate(trace, {"source": a})


@pytest.mark.parametrize("invalid", ["duplicate", "outside", "wrong_row", "wrong_table", "overlap", "empty_reason"])
def test_scope_claims_need_the_actual_record_and_a_reason(invalid):
    a, ab = table()
    claim = hole()
    scope = {"unresolved_rows": [claim]}
    if invalid == "duplicate":
        scope["unresolved_rows"].append(deepcopy(claim))
    elif invalid == "outside":
        claim["row"] = 99
    elif invalid == "wrong_row":
        claim["evidence_locator"]["cell"] = "A2"
    elif invalid == "wrong_table":
        claim["evidence_locator"]["sheet"] = "Other"
    elif invalid == "overlap":
        scope["excluded_rows"] = [{**deepcopy(claim), "reason_type": "footer"}]
    else:
        claim["reason"] = " "
    with pytest.raises(ValueError):
        evaluate(certificate([ab], {"source": bound(ab, **scope)}), {"source": a})


def test_absence_requires_readable_complete_header_search():
    a, ab = table()
    a["tables"][0]["cells"]["B1"] = {"value": None, "state": "unknown_formula_cache"}
    raw = ab.model_dump()
    raw["columns"].pop("value")
    raw["field_holes"] = {"value": {"state": "absent", "reason": "Searched headers"}}
    ab = type(ab).model_validate(raw)
    with pytest.raises(ValueError, match="readable searched headers"):
        evaluate(certificate([ab], {"source": bound(ab)}), {"source": a})


def test_an_independent_false_conjunct_survives_a_missing_mapping():
    a, ab = table(rows=[(10, "A", "CC")])
    plan = rule({"kind": "all", "items": [{"kind": "value_test", "field": "missing", "op": "not_empty"}, {"kind": "value_test", "field": "code", "op": "eq", "value": 99}]})
    result = evaluate(certificate([ab], {"source": bound(ab)}), {"source": a}, plan)
    assert result.report.checks[0].status == "NG"
    assert result.observations[0]["components"][0]["value"] is None


@pytest.mark.parametrize("changes", [{"status": "PASS"}, {"status": "NG"}, {"unknown": 0}, {"checked": 1}, {"diagnostic": "parser_unknown"}])
def test_unknown_report_must_match_replayed_status_counts_and_cause(changes):
    a, _ = table()
    trace = certificate([], {"source": {"kind": "unresolved", "reason": "Source body not found"}})
    plan = rule()
    report = evaluate(trace, {"source": a}, plan).report
    for key, value in changes.items():
        setattr(report.checks[0], key, value)
    with pytest.raises(ValueError):
        validate_trace(trace, report, {"source": a}, {"source": plan})


@pytest.mark.parametrize("reasons", [[], [" \t"], ["same", " same "], ["x" * 4097], [str(i) for i in range(257)]])
def test_unresolved_scope_reasons_are_bounded_and_distinct(reasons):
    raw = {"kind": "bound", "scopes": [{"binding_id": "source"}], "unresolved_scopes": reasons}
    if reasons == []:
        assert BoundCollection.model_validate(raw).unresolved_scopes == []
    else:
        with pytest.raises(ValueError):
            BoundCollection.model_validate(raw)


def test_local_field_checker_uses_the_same_unknown_and_source_contract():
    a, ab = table()
    raw = ab.model_dump()
    raw["columns"].pop("value")
    raw["field_holes"] = {"value": {"state": "absent", "reason": "Searched complete headers"}}
    request = CheckRequest.model_validate({"binding": raw, "checks": [{"id": "local", "kind": "field_nonempty", "field": "value"}]})
    result = check_fields(a, request).checks[0]
    assert (result.local_condition_status, result.checked, result.unknown) == ("UNABLE", 0, 2)
    assert result.unknown_examples == []


def test_shared_budget_bounds_source_validation_and_computation():
    a, ab = table()
    trace = certificate([ab], {"source": bound(ab)})
    with pytest.raises(ValueError):
        evaluate_audit(trace, {"source": a}, {"source": rule()}, budget=EvidenceBudget(remaining=1))


def test_exists_cannot_prove_no_match_while_another_source_scope_is_unresolved():
    a, ab = table(rows=[(10, "A", "CC")])
    plan = rule({"kind": "value_test", "field": "value", "op": "eq", "value": "SC"}, quantifier="exists")
    trace = certificate([ab], {"source": {**bound(ab), "unresolved_scopes": ["Another source table remains unbound"]}})
    check = evaluate(trace, {"source": a}, plan).report.checks[0]
    assert (check.status, check.unknown) == ("UNABLE", 1)


def test_known_local_match_does_not_erase_unresolved_target_record_membership():
    a, ab = table(rows=[(10, "A", "CC")])
    b, bb = table("target", [(10, "A", "CC"), (10, "B", "SC")])
    target_hole = {"row": 3, "reason": "Target may be a section label", "evidence_locator": {"kind": "xlsx_cell", "sheet": "Records", "cell": "A3"}}
    trace = certificate([ab, bb], {"source": bound(ab), "target": bound(bb, unresolved_rows=[target_hole])})
    check = evaluate(trace, {"source": a, "target": b}, rule(related())).report.checks[0]
    assert (check.status, check.unknown) == ("UNABLE", 1)


def test_semantic_key_matching_cannot_discard_an_unknown_target_with_a_different_spelling():
    a, ab = table(rows=[("Incoming", "A", "CC")])
    b, bb = table("target", [("Incoming", "A", "CC"), ("收货", "B", "SC")])
    target_hole = {"row": 3, "reason": "Unknown target membership", "evidence_locator": {"kind": "xlsx_cell", "sheet": "Records", "cell": "A3"}}
    plan = rule(related(match="all", key_match="semantic", comparisons=[{"source_field": "value", "target_field": "value", "mode": "exact"}]))
    check = evaluate(certificate([ab, bb], {"source": bound(ab), "target": bound(bb, unresolved_rows=[target_hole])}), {"source": a, "target": b}, plan).report.checks[0]
    assert check.status == "UNABLE"


def test_semantic_one_to_one_can_finish_after_actual_non_alias_judgments():
    a, ab = table(rows=[("A", "A", "CC"), ("B", "B", "CC")])
    b, bb = table("target", [("A", "A", "CC"), ("B", "B", "CC")])
    plan = rule(related(match="one_to_one", key_match="semantic"))
    trace = certificate([ab, bb], {"source": bound(ab), "target": bound(bb)})
    initial = evaluate(trace, {"source": a, "target": b}, plan)
    final = evaluate(answered(trace, initial, "false"), {"source": a, "target": b}, plan)
    assert final.report.checks[0].status == "PASS"


def test_a_new_rule_source_or_revision_invalidates_previous_local_answers():
    a, ab = table(rows=[(10, "A", "CC")])
    plan = rule({"kind": "judge", "question": "Does this method meet the effective requirement?", "fields": ["value"]})
    trace = certificate([ab], {"source": bound(ab)})
    complete = answered(trace, evaluate(trace, {"source": a}, plan))
    for field, value in (("source_sha256", "new-effective-body"), ("plan_revision", "b" * 64)):
        changed = plan.model_copy(deep=True)
        setattr(changed, field, value)
        with pytest.raises(ValueError, match="stale"):
            evaluate(complete, {"source": a}, changed)


def test_verified_zero_objects_use_frozen_policy_without_becoming_unbound():
    a, ab = table(rows=[(10, "Section title", None)])
    exclusion = {"row": 2, "reason_type": "non_record", "reason": "Verified title contains no record", "evidence_locator": {"kind": "xlsx_cell", "sheet": "Records", "cell": "B2"}}
    plan = rule()
    plan.rules[0].empty_scope_status = "PASS"
    plan.rules[0].empty_scope_basis = "The effective clause explicitly permits a verified zero-object scope"
    check = evaluate(certificate([ab], {"source": bound(ab, excluded_rows=[exclusion])}), {"source": a}, plan).report.checks[0]
    assert (check.status, check.total, check.unknown) == ("PASS", 1, 0)


def test_formatted_native_numeric_zero_has_one_canonical_text():
    a, ab = table(rows=[(-0.0, "A", "CC")])
    b, bb = table("target", [(0, "A", "CC")])
    plan = rule(related(source_key={"fields": ["code"], "template": "【{code}】"}, target_key={"fields": ["code"], "template": "【{code}】"}))
    check = evaluate(certificate([ab, bb], {"source": bound(ab), "target": bound(bb)}), {"source": a, "target": b}, plan).report.checks[0]
    assert check.status == "PASS"


def test_a_missing_use_keeps_its_unknown_result_with_discovered_physical_bindings():
    a, ab = table()
    trace = AuditTrace(bindings=[ab], uses=[])
    check = evaluate(trace, {"source": a}).report.checks[0]
    assert (check.status, check.unknown) == ("UNABLE", 1)


def test_unused_input_knowledge_cannot_add_an_obligation_to_the_cached_rule():
    a, ab = table()
    trace = certificate([ab], {"source": bound(ab), "unneeded": {"kind": "unresolved", "reason": "Unrelated document is not located"}})
    assert evaluate(trace, {"source": a}).report.checks[0].status == "PASS"


def test_untriggered_relation_does_not_require_a_target_collection():
    a, ab = table(rows=[(10, "A", "ordinary")])
    plan = rule(related(), when={"kind": "value_test", "field": "value", "op": "eq", "value": "SC"})
    plan.rules[0].empty_scope_status = "PASS"
    plan.rules[0].empty_scope_basis = "The effective conditional rule explicitly permits no special characteristics"
    trace = certificate([ab], {"source": bound(ab), "target": {"kind": "unresolved", "reason": "The conditional target is not located"}})
    assert evaluate(trace, {"source": a}, plan).report.checks[0].status == "PASS"


def test_semantic_one_to_one_rejects_two_distinct_source_keys_sharing_a_target():
    a, ab = table(rows=[("A", "A", "CC"), ("B", "B", "CC")])
    b, bb = table("target", [("X", "X", "CC")])
    plan = rule(related(match="one_to_one", key_match="semantic"))
    trace = certificate([ab, bb], {"source": bound(ab), "target": bound(bb)})
    initial = evaluate(trace, {"source": a, "target": b}, plan)
    raw = trace.model_dump(mode="json")
    raw["answers"] = [
        {"need_id": need.id, "value": "false" if "/source_key/" in need.program_path else "true", "reason": "The source identities differ but both point to this target"} for need in initial.needs
    ]
    result = evaluate(AuditTrace.model_validate(raw), {"source": a, "target": b}, plan)
    assert result.report.checks[0].status == "NG"
    assert [record["relation"]["reason"] for record in result.observations] == ["duplicate_or_shared_key"] * 2


@pytest.mark.parametrize("position", ["when", "require", "target_when"])
@pytest.mark.parametrize("structural", [{"kind": "column_exists", "input": "source", "field": "value"}, {"kind": "collection_exists", "input": "source"}])
def test_column_and_collection_existence_cannot_prove_a_row_value(position, structural):
    # This is the compiler failure observed in the frozen pilot: an existing
    # column cannot discharge an obligation about its current cell content.
    misplaced = {"kind": "all", "items": [structural]}
    with pytest.raises(ValueError):
        if position == "require":
            rule(misplaced)
        elif position == "when":
            rule(when=misplaced)
        else:
            rule(related(target_when=misplaced))


def test_document_judgment_field_names_are_search_hints_and_not_row_values():
    document, _ = table()
    raw = rule().model_dump(mode="json")
    raw["rules"][0]["program"] = {"kind": "document", "require": {"kind": "judge", "question": "Are the approval and date fields filled?", "fields": ["approval", "date"]}}
    plan = RulePlan.model_validate(raw)
    trace = certificate([], {})
    initial = evaluate(trace, {"source": document}, plan)
    assert len(initial.needs) == 1
    assert initial.needs[0].context["requested_fields"] == ["approval", "date"]
    assert initial.needs[0].context["source"] is None
    assert initial.report.checks[0].status == "UNABLE"
    assert evaluate(answered(trace, initial), {"source": document}, plan).report.checks[0].status == "PASS"


def test_a_separator_cannot_silently_be_used_as_a_field_template():
    with pytest.raises(ValueError):
        Key(fields=["code", "name"], separator="{code} {name}")


def test_complete_merged_tables_fit_the_work_budget_without_dropping_fields():
    fields = {f"f{column}": chr(65 + column) for column in range(12)}
    document, binding = table(rows=[tuple(range(12))] * 360, fields=fields)
    document["tables"][0]["merges"] = [span for row in range(2, 362) for span in ([row, 1, row, 2], [row, 3, row, 4])]
    before = deepcopy(document)
    trace = certificate([binding], {"source": bound(binding)})
    result = evaluate(trace, {"source": document}, rule({"kind": "value_test", "field": "f0", "op": "ge", "value": 0}))
    assert (result.report.checks[0].status, result.report.checks[0].total, result.report.checks[0].unknown) == ("PASS", 360, 0)
    assert document == before


def test_use_specific_row_evidence_reuses_layout_instead_of_rescanning_all_merges():
    fields = {"f0": "A", "f1": "B", "f2": "C", "f3": "D"}
    document, binding = table(rows=[(0, 1, 2, 3)] * 700, fields=fields)
    document["tables"][0]["merges"] = [[row, 1, row, 2] for row in range(2, 702)]
    raw = binding.model_dump(mode="json")
    raw["columns"] = {"f0": "A"}
    binding = Binding.model_validate(raw)
    plan = rule({"kind": "value_test", "field": "f0", "op": "ge", "value": 0})
    plan.rules[0].empty_scope_status = "NA"
    plan.rules[0].empty_scope_basis = "This synthetic contract explicitly treats a verified zero-object view as not applicable."
    second = plan.rules[0].model_copy(deep=True)
    second.id = "R2"
    plan.rules.append(second)
    claims = [
        {"row": row, "reason_type": "non_record", "reason": "Excluded in this constructed collection view", "evidence_locator": {"kind": "xlsx_cell", "sheet": "Records", "cell": f"A{row}"}}
        for row in range(2, 702)
    ]
    trace = certificate([binding], {"source": bound(binding, excluded_rows=claims)})
    raw_trace = trace.model_dump(mode="json")
    raw_trace["uses"].append({**raw_trace["uses"][0], "rule_id": "R2"})
    result = evaluate(AuditTrace.model_validate(raw_trace), {"source": document}, plan)
    assert [(check.status, check.total, check.unknown) for check in result.report.checks] == [("NA", 700, 0)] * 2


def test_reading_a_tall_merge_does_not_expand_its_unused_rows():
    source = {"rows": 100000, "cols": 1, "merges": [[1, 1, 100000, 1]], "cells": {"A1": {"value": "shared heading", "state": "known"}}}
    reader = TableReader(source, EvidenceBudget(remaining=100))
    assert reader.cell(99999, 1)["value"] == "shared heading"
    assert reader.cell(99999, 1)["anchor"] == "A1"


@pytest.mark.parametrize("fault", ["outside", "missing", "unreadable"])
def test_indexed_row_evidence_still_validates_explicit_source_anchors(fault):
    document, binding = table()
    cells = document["tables"][0]["cells"]
    cells["A3"] = {"value": "untrusted alias copy", "state": "known", "anchor": "Z99" if fault == "outside" else "A2"}
    if fault == "missing":
        cells.pop("A2")
    elif fault == "unreadable":
        cells["A2"] = {"value": None, "state": "unknown_formula_cache"}
    claim = {"row": 3, "reason_type": "footer", "reason": "Claimed outside this view", "evidence_locator": {"kind": "xlsx_cell", "sheet": "Records", "cell": "A3"}}
    with pytest.raises(ValueError):
        evaluate(certificate([binding], {"source": bound(binding, excluded_rows=[claim])}), {"source": document})


def test_binding_work_preserves_source_target_and_context_field_ownership():
    plan = rule(
        {
            "kind": "all",
            "items": [
                {"kind": "value_test", "field": "value", "op": "not_empty"},
                related(
                    source_key={"fields": ["code", "name"]},
                    target_key={"fields": ["step", "description"]},
                    comparisons=[{"source_field": "value", "target_field": "classification", "mode": "exact"}],
                    target_when={"kind": "not", "item": {"kind": "judge", "question": "Is this a heading?", "fields": ["role"], "inputs": ["legend"]}},
                ),
            ],
        },
        precondition={"kind": "column_exists", "input": "source", "field": "frequency"},
        when={"kind": "judge", "question": "Does this record apply?", "fields": ["active"], "inputs": ["reference"]},
    )
    frozen = plan.model_dump_json()
    seed, work = prepare_audit({"source": plan})
    assert work[0]["inputs"] == {
        "source": {"fields": ["active", "code", "frequency", "name", "value"]},
        "reference": {"fields": []},
        "target": {"fields": ["classification", "description", "role", "step"]},
        "legend": {"fields": []},
    }
    assert [(q["record_input"], q["fields"]) for q in work[0]["semantic_questions"]] == [("source", ["active"]), ("target", ["role"])]
    assert all(value.kind == "unresolved" for value in seed.uses[0].inputs.values())
    assert seed.bindings == seed.answers == []
    assert plan.model_dump_json() == frozen


def test_document_search_hints_do_not_become_native_field_bindings():
    raw = rule().model_dump(mode="json")
    raw["rules"][0]["program"] = {"kind": "document", "require": {"kind": "judge", "question": "Are approval and date present?", "fields": ["approval", "date"]}}
    plan = RulePlan.model_validate(raw)
    seed, work = prepare_audit({"source": plan})
    assert seed.uses[0].inputs == work[0]["inputs"] == {}
    assert work[0]["semantic_questions"][0]["record_input"] is None
    assert work[0]["semantic_questions"][0]["fields"] == ["approval", "date"]
    document, _ = table()
    result = evaluate_audit(seed, {"source": document}, {"source": plan})
    assert result.report.checks[0].status == "UNABLE"
    assert len(result.needs) == 1


def test_initial_trace_keeps_each_rule_use_separate_and_cannot_claim_reading():
    first = rule()
    second = rule({"kind": "value_test", "field": "other", "op": "not_empty"})
    seed, work = prepare_audit({"source": first, "second": second})
    assert [(use.audit_id, use.rule_id) for use in seed.uses] == [("source", "R1"), ("second", "R1")]
    assert [entry["inputs"]["source"]["fields"] for entry in work] == [["value"], ["other"]]
    document, _ = table()
    other, _ = table("second")
    result = evaluate_audit(seed, {"source": document, "second": other}, {"source": first, "second": second})
    assert [check.status for check in result.report.checks] == ["UNABLE", "UNABLE"]
    assert all(use.inputs["source"].kind == "unresolved" for use in seed.uses)
