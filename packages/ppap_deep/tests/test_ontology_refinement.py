"""Document evidence refines identity without changing frozen rule obligations."""

import hashlib
import json
import time
from copy import deepcopy
from io import BytesIO

import pytest
from openpyxl import Workbook
from ppap_deep.auditor import semantic_inputs
from ppap_deep.protocol import ExecuteRequest, FrozenFile
from ppap_deep.semantic.document_tools import snapshot
from ppap_deep.semantic.guest import Manifest, execute
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.refinement import PlanRefinement, effective_plans, plan_identity
from ppap_deep.semantic.trace import AuditTrace, Judgment, evaluate_audit
from ppap_deep.semantic.validation import validate_bundle

PFD = "00000000-0000-4000-8000-000000000010"
CP = "00000000-0000-4000-8000-000000000020"
OTHER = "00000000-0000-4000-8000-000000000021"
RULE = "00000000-0000-4000-8000-000000000030"
ENGINE = "a" * 64
CLAUSES = [
    {"id": "R1", "rule_to_check": "各产品内按原始工序编号在流程图和控制计划中一一对应，且分类相同，否则不通过。"},
    {"id": "R2", "rule_to_check": "流程图中的工序编号均须非空，否则不通过。"},
]


def native_file(kind, conflict=False, duplicate=False):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = kind
    for row in [["产品", "工序编号", "分类"], ["A", "010", "SC" if kind == "CP" and conflict else "CC"], ["A" if duplicate else "B", "010", "SC"]]:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def proposed_refinement(plan, target=CP):
    proposed = plan.model_dump(mode="json")
    for entity in proposed["ontology"]["entities"]:
        entity.update(grain="一个产品内的一个工序；同号不同产品保留为不同对象", identity=["product", "code"])
    relation = proposed["rules"][0]["program"]["require"]
    for side in ["source_key", "target_key"]:
        relation[side]["fields"] = ["product", "code"]
    return {
        "audit_id": target,
        "base_plan_id": plan_identity(plan),
        "plan": proposed,
        "reason": "规则明确限定各产品，当前两个原件均显示同号010分别属于产品A和B；按产品与工序号共同建立身份。",
        "rule_evidence": [rule.source_locator.model_dump(mode="json") for rule in plan.rules],
        "document_evidence": [{"document_id": identity, "locator": {"kind": "xlsx_range", "sheet": kind, "start": "A1", "end": "B3"}} for identity, kind in [(PFD, "PFD"), (target, "CP")]],
    }


class RefinedModel:
    input_tokens = output_tokens = 0

    def __init__(self, refine=True, clarify=False):
        self.refine, self.clarify, self.calls = refine, clarify, []

    def run(self, work, prompt, schema, deadline, *, reasoning_effort):
        self.calls.append(work.name)
        assert reasoning_effort == "medium"
        if issubclass(schema, RulePlan):
            supplied = prompt.split("identity and ordered clause IDs must match exactly: ", 1)[1]
            identity, _ = json.JSONDecoder().raw_decode(supplied)
            relation = {
                "kind": "related",
                "relationship_id": "corresponds",
                "target": "cp",
                "source_key": {"fields": ["code"]},
                "target_key": {"fields": ["code"]},
                "match": "one_to_one",
                "comparisons": [{"source_field": "classification", "target_field": "classification", "mode": "exact"}],
            }
            # This intentionally incomplete rule-only identity is a controlled
            # compiler proposal, never an expected finding injected into core.
            return schema.model_validate(
                {
                    **{key: identity[key] for key in ["source_sha256", "plan_revision"]},
                    "ontology": {
                        "entities": [
                            {"id": name, "grain": "一个工序编号对应一个工序", "attributes": {"product": "string", "code": "string", "classification": "string"}, "identity": ["code"]}
                            for name in ["pfd", "cp"]
                        ],
                        "relationships": [{"id": "corresponds", "kind": "same_instance", "source": "pfd", "target": "cp", "meaning": "相同产品内的相同工序在两个原件中的表达"}],
                    },
                    "rules": [
                        {
                            "id": clause["id"],
                            "source_locator": {"kind": "published_clause", "clause_id": clause["id"]},
                            "program": {"kind": "records", "source": "pfd", "quantifier": "forall", "require": required},
                            "empty_scope_status": "UNABLE",
                            "empty_scope_basis": "原文没有授予空范围通过。",
                        }
                        for clause, required in zip(CLAUSES, [relation, {"kind": "value_test", "field": "code", "op": "not_empty"}], strict=True)
                    ],
                }
            )
        assert schema is AuditTrace
        documents = json.loads((work / "documents.json").read_text())
        plans = {key: RulePlan.model_validate(value) for key, value in json.loads((work / "plans.json").read_text()).items()}
        targets = json.loads((work / "targets.json").read_text())
        bindings = []
        for document_id, document in documents.items():
            kind = document["tables"][0]["id"]
            binding = {
                "id": document_id,
                "document_id": document_id,
                "source_sha256": document["source"]["sha256"],
                "table": kind,
                "header_rows": [1],
                "first_row": 2,
                "last_row": 3,
                "columns": {"product": "A", "code": "B", "classification": "C"},
                "mapping_basis": "原件首行明确的产品、工序编号与分类字段。",
                "boundary_basis": "原件完整的两条正文记录。",
            }
            if self.clarify and kind == "CP":
                binding["interpretations"] = [
                    {
                        "id": "ambiguous-product-context",
                        "field": "product",
                        "question": "产品A和B是否必须分别建立工序身份？",
                        "candidates": [{"id": "separate", "label": "产品内身份", "explanation": "产品与工序编号共同标识工序。"}],
                        "evidence": [{"document_id": document_id, "locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "A1"}}],
                    }
                ]
            bindings.append(binding)
        uses = [
            {"audit_id": target, "rule_id": rule.id, "inputs": {"pfd": {"kind": "bound", "scopes": [{"binding_id": PFD}]}, "cp": {"kind": "bound", "scopes": [{"binding_id": target}]}}}
            for target, plan_id in targets.items()
            for rule in plans[plan_id].rules
        ]
        return AuditTrace.model_validate(
            {"bindings": bindings, "uses": uses, "plan_refinements": [proposed_refinement(plans[plan_id], target) for target, plan_id in targets.items() if self.refine and target == CP]}
        )


def run_case(path, *, refine=True, conflict=False, clarify=False, previous=None, other=False, duplicate=False):
    inputs = path / "inputs"
    inputs.mkdir(parents=True)
    names, files = {}, []
    for identity, kind in [(PFD, "PFD"), (CP, "CP"), *([(OTHER, "CP")] if other else [])]:
        raw = native_file(kind, conflict, duplicate)
        name = identity + ".xlsx"
        (inputs / name).write_bytes(raw)
        names[identity] = name
        files.append(
            FrozenFile(
                file_version_id=identity,
                filename=name,
                sha256=hashlib.sha256(raw).hexdigest(),
                role="reference" if identity == PFD else "target",
                rule_version_id=None if identity == PFD else RULE,
                rule_body=None if identity == PFD else {"kind": "engine", "rules": CLAUSES},
            )
        )
    request = ExecuteRequest(protocol_revision="ppap-deep-v2", tenant_id="ontology-test", round_id="00000000-0000-4000-8000-000000000001", files=files, executor="ppap-deep", executor_revision=ENGINE)
    spec, packets = semantic_inputs(request, ENGINE)
    for identity, raw in packets.items():
        (inputs / (identity + ".json")).write_bytes(raw)
    cached = {}
    if previous:
        for identity, plan in previous.plans.items():
            name = "cached-" + identity + ".json"
            (inputs / name).write_text(plan.model_dump_json())
            cached[identity] = name
    manifest = Manifest(spec=spec, documents=names, rules={identity: identity + ".json" for identity in packets}, cached_snapshots={}, cached_plans=cached)
    (inputs / "manifest.json").write_text(manifest.model_dump_json())
    model = RefinedModel(refine, clarify)
    bundle = execute(inputs, path / "work", model, time.monotonic() + 60)
    return bundle, spec, model


def test_real_guest_refines_compound_identity_and_preserves_rule_cache_and_native_observations(tmp_path):
    initial, _, _ = run_case(tmp_path / "initial", refine=False)
    assert [check.status for check in initial.report.checks] == ["NG", "PASS"]
    refined, spec, model = run_case(tmp_path / "refined", previous=initial)
    assert model.calls == ["audit"]  # The same rule-only plan is reused.
    assert [check.status for check in refined.report.checks] == ["PASS", "PASS"]
    assert [check.total for check in refined.report.checks] == [2, 2]
    assert refined.plans[RULE].ontology.entities[0].identity == ["code"]
    assert refined.trace.plan_refinements[0].plan.ontology.entities[0].identity == ["product", "code"]
    assert refined.documents[PFD]["tables"][0]["cells"]["B2"]["value"] == "010"
    assert refined.documents[PFD]["tables"][0]["cells"]["A3"]["value"] == "B"
    validate_bundle(refined, spec)
    replay, _, replay_model = run_case(tmp_path / "replay-base", refine=False, previous=refined)
    assert replay_model.calls == ["audit"]
    assert [check.status for check in replay.report.checks] == ["NG", "PASS"]
    assert replay.trace.plan_refinements == []


def test_refinement_retains_classification_conflict_and_does_not_change_other_target(tmp_path):
    refined, _, _ = run_case(tmp_path / "conflict", conflict=True)
    assert [check.status for check in refined.report.checks] == ["NG", "PASS"]
    assert refined.documents[PFD]["tables"][0]["cells"]["C2"]["value"] == "CC"
    assert refined.documents[CP]["tables"][0]["cells"]["C2"]["value"] == "SC"
    paired, _, _ = run_case(tmp_path / "paired", other=True)
    statuses = {(check.audit_id, check.rule_id): check.status for check in paired.report.checks}
    assert statuses == {(CP, "R1"): "PASS", (CP, "R2"): "PASS", (OTHER, "R1"): "NG", (OTHER, "R2"): "PASS"}


def test_refinement_cannot_bypass_an_interpretation_that_requires_user_confirmation(tmp_path):
    bundle, _, _ = run_case(tmp_path / "ambiguous", clarify=True)
    assert [check.status for check in bundle.report.checks] == ["UNABLE", "PASS"]
    result = evaluate_audit(bundle.trace, bundle.documents, {CP: bundle.plans[RULE]})
    assert result.clarifications[0]["id"] == "ambiguous-product-context"
    assert bundle.plans[RULE].appendix == []


def test_strengthened_identity_does_not_erase_duplicate_instances(tmp_path):
    bundle, _, _ = run_case(tmp_path / "duplicates", duplicate=True)
    assert [check.status for check in bundle.report.checks] == ["NG", "PASS"]
    assert [check.total for check in bundle.report.checks] == [2, 2]
    result = evaluate_audit(bundle.trace, bundle.documents, {CP: bundle.plans[RULE]})
    assert len([row for row in result.observations if row["rule_id"] == "R1" and row["role"] == "applicable"]) == 2


@pytest.mark.parametrize("fault", ["quantifier", "matching_policy", "comparison", "relationship", "weak_identity", "wrong_base", "wrong_rule_evidence", "wrong_document", "missing_document_location"])
def test_refinement_rejects_changes_to_obligations_identity_or_source_scope(tmp_path, fault):
    bundle, _, _ = run_case(tmp_path / fault)
    original = bundle.plans[RULE]
    candidate = proposed_refinement(original)
    relation = candidate["plan"]["rules"][0]["program"]["require"]
    if fault == "quantifier":
        candidate["plan"]["rules"][0]["program"]["quantifier"] = "exists"
    elif fault == "matching_policy":
        relation["source_key"]["normalization"] = "whitespace"
    elif fault == "comparison":
        relation["comparisons"] = []
    elif fault == "relationship":
        candidate["plan"]["ontology"]["relationships"][0]["meaning"] = "只按相同分类即可对应。"
    elif fault == "weak_identity":
        for entity in candidate["plan"]["ontology"]["entities"]:
            entity["identity"] = ["product"]
        relation["source_key"]["fields"] = relation["target_key"]["fields"] = ["product"]
    elif fault == "wrong_base":
        candidate["base_plan_id"] = "f" * 64
    elif fault == "wrong_rule_evidence":
        candidate["rule_evidence"] = [{"kind": "published_clause", "clause_id": "R9"}]
    elif fault == "wrong_document":
        candidate["document_evidence"][0]["document_id"] = "outside-input"
    else:
        candidate["document_evidence"][0]["locator"] = None
    with pytest.raises(ValueError, match="Refinement"):
        effective_plans([PlanRefinement.model_validate(candidate)], bundle.documents, {CP: original})


def test_refined_ontology_invalidates_prior_local_answer_identity(tmp_path):
    bundle, _, _ = run_case(tmp_path / "answers", refine=False)
    raw = bundle.plans[RULE].model_dump(mode="json")
    raw["rules"][1]["program"]["require"] = {"kind": "judge", "question": "当前原件中的工序编号是否清晰？", "fields": ["code"]}
    plan = RulePlan.model_validate(raw)
    trace = deepcopy(bundle.trace)
    needs = evaluate_audit(trace, bundle.documents, {CP: plan}).needs
    assert needs
    trace.answers = [Judgment(need_id=need.id, value="unknown", reason="受控回答仍未知。") for need in needs]
    evaluate_audit(trace, bundle.documents, {CP: plan})
    trace.plan_refinements = [PlanRefinement.model_validate(proposed_refinement(plan))]
    with pytest.raises(ValueError, match="Unknown or stale"):
        evaluate_audit(trace, bundle.documents, {CP: plan})
    trace.answers = []
    refreshed = evaluate_audit(trace, bundle.documents, {CP: plan})
    assert not {need.id for need in needs} & {need.id for need in refreshed.needs}


def test_refinement_rejects_duplicate_or_unrelated_targets(tmp_path):
    bundle, _, _ = run_case(tmp_path / "targets")
    candidate = bundle.trace.plan_refinements[0]
    for refinements in [[candidate, candidate], [candidate.model_copy(update={"audit_id": OTHER})]]:
        with pytest.raises(ValueError, match="Duplicate or unknown"):
            effective_plans(refinements, bundle.documents, {CP: bundle.plans[RULE]})


def classification_conflict_case(tmp_path, alias=None):
    documents, bindings = {}, []
    for identity, kind, rows in [(PFD, "PFD", [["A", "010", "SC"]]), (CP, "CP", [["A", "010", "SC"], ["A", "010", "CC"]])]:
        book = Workbook()
        sheet = book.active
        sheet.title = kind
        for row in [["产品", "工序编号", "分类", "附件"], *[[*row, row[-1]] for row in rows]]:
            sheet.append(row)
        if alias == "merged":
            for row in range(2, len(rows) + 2):
                sheet.merge_cells(start_row=row, start_column=3, end_row=row, end_column=4)
        path = tmp_path / (kind + ".xlsx")
        book.save(path)
        book.close()
        document = documents[identity] = snapshot(path)
        bindings.append(
            {
                "id": identity,
                "document_id": identity,
                "source_sha256": document["source"]["sha256"],
                "table": kind,
                "header_rows": [1],
                "first_row": 2,
                "last_row": len(rows) + 1,
                "columns": {"product": "A", "code": "B", "classification": "C", "attachment": "C" if alias == "column" else "D"},
                "mapping_basis": "原始产品、工序和分类表头。",
                "boundary_basis": "完整正文范围。",
            }
        )
    base = RulePlan.model_validate(
        {
            "source_sha256": "a" * 64,
            "plan_revision": "b" * 64,
            "ontology": {
                "entities": [
                    {
                        "id": name,
                        "grain": "产品内按编号识别的工序",
                        "attributes": {"product": "string", "code": "string", "classification": "string", "attachment": "string"},
                        "identity": ["product", "code"],
                    }
                    for name in ["pfd", "cp"]
                ],
                "relationships": [{"id": "corresponds", "kind": "same_instance", "source": "pfd", "target": "cp", "meaning": "同产品同编号的工序对应"}],
            },
            "rules": [
                {
                    "id": "R1",
                    "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                    "program": {
                        "kind": "records",
                        "source": "pfd",
                        "quantifier": "forall",
                        "require": {
                            "kind": "related",
                            "relationship_id": "corresponds",
                            "target": "cp",
                            "source_key": {"fields": ["product", "code"]},
                            "target_key": {"fields": ["product", "code"]},
                            "match": "all",
                            "comparisons": [{"source_field": "classification", "target_field": "classification", "mode": "exact"}],
                        },
                    },
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "没有空范围授权。",
                }
            ],
        }
    )
    trace = AuditTrace.model_validate(
        {
            "bindings": bindings,
            "uses": [{"audit_id": CP, "rule_id": "R1", "inputs": {"pfd": {"kind": "bound", "scopes": [{"binding_id": PFD}]}, "cp": {"kind": "bound", "scopes": [{"binding_id": CP}]}}}],
        }
    )
    if alias == "object":
        raw = trace.model_dump(mode="json")
        for name, identity, kind in [("pfd", PFD, "PFD"), ("cp", CP, "CP")]:
            scope = raw["uses"][0]["inputs"][name]["scopes"][0]
            scope["objects"] = [
                {
                    "id": f"row-{row}",
                    "rows": [row],
                    "basis": "原件独立正文行。",
                    "evidence": [{"kind": "xlsx_cell", "sheet": kind, "cell": f"B{row}"}],
                    "fields": {"attachment": [{"kind": "xlsx_cell", "sheet": kind, "cell": f"C{row}"}]},
                }
                for row in range(2, documents[identity]["tables"][0]["rows"] + 1)
            ]
        trace = AuditTrace.model_validate(raw)
    return documents, base, trace


def test_identity_refinement_cannot_filter_out_a_real_classification_conflict(tmp_path):
    documents, base, trace = classification_conflict_case(tmp_path)
    assert evaluate_audit(trace, documents, {CP: base}).report.checks[0].status == "NG"
    unsafe = base.model_dump(mode="json")
    for entity in unsafe["ontology"]["entities"]:
        entity["identity"].append("classification")
    for side in ["source_key", "target_key"]:
        unsafe["rules"][0]["program"]["require"][side]["fields"].append("classification")
    candidate = RulePlan.model_validate(unsafe)
    # This is the exact false acceptance mechanism: putting the tested
    # difference into identity removes the CC counterexample from candidates.
    assert evaluate_audit(trace, documents, {CP: candidate}).report.checks[0].status == "PASS"
    trace.plan_refinements = [
        PlanRefinement(
            audit_id=CP,
            base_plan_id=plan_identity(base),
            plan=candidate,
            reason="拟把分类也纳入身份。",
            rule_evidence=[base.rules[0].source_locator],
            document_evidence=[{"document_id": CP, "locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "C1"}}],
        )
    ]
    with pytest.raises(ValueError, match="identity must be independent"):
        evaluate_audit(trace, documents, {CP: base})
    trace.plan_refinements = []
    assert evaluate_audit(trace, documents, {CP: base}).report.checks[0].status == "NG"


@pytest.mark.parametrize("alias", ["column", "merged", "object"])
def test_identity_refinement_cannot_hide_comparison_sources_under_an_alias(tmp_path, alias):
    documents, base, trace = classification_conflict_case(tmp_path, alias)
    assert evaluate_audit(trace, documents, {CP: base}).report.checks[0].status == "NG"
    proposed = base.model_dump(mode="json")
    for entity in proposed["ontology"]["entities"]:
        entity["identity"].append("attachment")
    for side in ["source_key", "target_key"]:
        proposed["rules"][0]["program"]["require"][side]["fields"].append("attachment")
    candidate = RulePlan.model_validate(proposed)
    # Different field names (or different merged/explicit locators) still
    # depend on the original classification cells being audited.
    assert evaluate_audit(trace, documents, {CP: candidate}).report.checks[0].status == "PASS"
    trace.plan_refinements = [
        PlanRefinement(
            audit_id=CP,
            base_plan_id=plan_identity(base),
            plan=candidate,
            reason="拟用附件属性澄清身份。",
            rule_evidence=[base.rules[0].source_locator],
            document_evidence=[{"document_id": CP, "locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "D1"}}],
        )
    ]
    with pytest.raises(ValueError, match="identity uses original data under comparison"):
        evaluate_audit(trace, documents, {CP: base})


def test_identity_provenance_reads_compared_fields_even_for_an_independent_rule(tmp_path):
    bundle, _, _ = run_case(tmp_path / "independent-source")
    result = evaluate_audit(bundle.trace, bundle.documents, {CP: bundle.plans[RULE]})
    reading = next(reading for reading in result.readings if reading["rule_id"] == "R2" and reading["binding_id"] == PFD)
    assert reading["fields"] == ["classification", "code", "product"]
    assert [check.status for check in result.report.checks] == ["PASS", "PASS"]


def test_unknown_identity_provenance_preserves_native_blank_and_independent_ng(tmp_path):
    documents, base, trace = classification_conflict_case(tmp_path)
    book = Workbook()
    sheet = book.active
    sheet.title = "PFD"
    sheet.append(["产品", "工序编号", "分类", "附件"])
    sheet.append(["A", "010", '= "SC"', None])
    path = tmp_path / "unknown-classification.xlsx"
    book.save(path)
    book.close()
    documents[PFD] = snapshot(path)
    trace.bindings[0].source_sha256 = documents[PFD]["source"]["sha256"]
    raw = base.model_dump(mode="json")
    raw["rules"].append(
        {
            "id": "R2",
            "source_locator": {"kind": "published_clause", "clause_id": "R2"},
            "program": {"kind": "records", "source": "pfd", "quantifier": "forall", "require": {"kind": "value_test", "field": "attachment", "op": "not_empty"}},
            "empty_scope_status": "UNABLE",
            "empty_scope_basis": "没有空范围授权。",
        }
    )
    base = RulePlan.model_validate(raw)
    trace.uses.append(trace.uses[0].model_copy(update={"rule_id": "R2"}))
    assert [check.status for check in evaluate_audit(trace, documents, {CP: base}).report.checks] == ["UNABLE", "NG"]
    proposed = base.model_dump(mode="json")
    for entity in proposed["ontology"]["entities"]:
        entity["identity"].append("attachment")
    for side in ["source_key", "target_key"]:
        proposed["rules"][0]["program"]["require"][side]["fields"].append("attachment")
    trace.plan_refinements = [
        PlanRefinement(
            audit_id=CP,
            base_plan_id=plan_identity(base),
            plan=RulePlan.model_validate(proposed),
            reason="拟加强身份，比较属性的公式缓存未知。",
            rule_evidence=[rule.source_locator for rule in base.rules],
            document_evidence=[{"document_id": PFD, "locator": {"kind": "xlsx_cell", "sheet": "PFD", "cell": "D1"}}],
        )
    ]
    result = evaluate_audit(trace, documents, {CP: base})
    assert [check.status for check in result.report.checks] == ["UNABLE", "NG"]
    assert documents[PFD]["tables"][0]["cells"]["C2"]["state"] == "unknown_formula_cache"
