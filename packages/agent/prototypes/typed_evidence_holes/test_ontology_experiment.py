"""Independent fixed oracles over actual source parsing and host evaluation."""

from __future__ import annotations

import hashlib
import importlib
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def experiment(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    return importlib.import_module("ontology_experiment")


def executed(experiment, case, directory):
    directory.mkdir()
    spec = experiment.prepare(case, directory, "a" * 64)
    runner = experiment.ControlledRunner(case, directory / "inputs", directory / "runner")
    with runner:
        bundle = experiment.guest.execute(directory / "inputs", directory / "work", runner, time.monotonic() + 60)
    return bundle, spec


@pytest.mark.parametrize("index", range(11))
def test_counterexamples_use_real_guest_sources_and_hand_worked_oracle(experiment, tmp_path, index):
    case = experiment.cases()[index]
    bundle, spec = executed(experiment, case, tmp_path / "case")
    scores = experiment.score(case, bundle, spec)
    assert {layer: scores[layer] for layer in ("concept", "binding", "judgment")} == {
        "concept": {"state": "correct", "errors": []},
        "binding": {"state": "correct", "errors": []},
        "judgment": {"state": "correct", "errors": [], "expected": case.status, "actual": case.status},
    }
    assert len(bundle.documents) == 2
    assert case.clarification == bool(scores["clarifications"])
    assert scores["refinements"] == int(case.refinement)
    if case.refinement:
        # The pure rule cache retains the initial plan, while this run alone
        # uses document-supported product+operation identity.
        assert bundle.plans[next(iter(spec.targets.values()))].ontology.entities[0].identity == ["code"]
        if case.status == "PASS":
            # Same operation number across products is legal. The initial
            # code-only interpretation yields a false duplicate NG; document-
            # supported compound identity corrects it without dropping rows.
            initial = bundle.trace.model_copy(update={"plan_refinements": []})
            target = next(iter(spec.targets))
            original = bundle.plans[spec.targets[target]]
            before = experiment.evaluate_audit(initial, bundle.documents, {target: original})
            assert before.report.checks[0].status == "NG"
            assert bundle.report.checks[0].status == "PASS"
    # Input directories expose source documents, not expected statuses, scorer
    # code or a prebuilt model plan/report.
    names = {path.name for path in (tmp_path / "case" / "inputs").iterdir()}
    assert names == {"manifest.json", "document-0.xlsx", "document-1.xlsx", "rule-0.docx"}


def test_concept_binding_and_final_errors_are_scored_separately(experiment, tmp_path):
    case = experiment.cases()[1]
    bundle, spec = executed(experiment, case, tmp_path / "case")
    rule_id = next(iter(spec.targets.values()))
    concept = bundle.model_copy(deep=True)
    for entity in concept.plans[rule_id].ontology.entities:
        entity.identity = ["classification"]
    relation = concept.plans[rule_id].rules[0].program.require
    relation.source_key.fields = relation.target_key.fields = ["classification"]
    scores = experiment.score(case, concept, spec)
    assert scores["concept"]["state"] == "incorrect"
    assert scores["binding"]["state"] == scores["judgment"]["state"] == "correct"

    binding = bundle.model_copy(deep=True)
    binding.trace.bindings[1].last_row = 2
    binding.trace.uses[0].inputs["target"].scopes[0].objects = binding.trace.uses[0].inputs["target"].scopes[0].objects[:1]
    scores = experiment.score(case, binding, spec)
    assert scores["binding"]["state"] == "incorrect"
    assert scores["concept"]["state"] == scores["judgment"]["state"] == "correct"

    judgment = bundle.model_copy(deep=True)
    judgment.report.checks[0].status = "PASS"
    scores = experiment.score(case, judgment, spec)
    assert scores["judgment"]["state"] == "incorrect"
    assert scores["concept"]["state"] == scores["binding"]["state"] == "correct"


def test_changing_oracle_does_not_change_controlled_model_proposal(experiment, tmp_path):
    case = experiment.cases()[1]
    altered = replace(
        case,
        tables=tuple(replace(table, identity_columns=("B",), objects=((2, 3),)) for table in case.tables),
        status="PASS",
        relation_kinds=("association",),
        match="any",
        comparison_nonempty=not case.comparison_nonempty,
    )
    bundle, spec = executed(experiment, altered, tmp_path / "case")
    # The controlled proposal follows the rule/source fixture, never expected
    # identity columns, grouping, relation kind, match policy or verdict.
    plan = bundle.plans[next(iter(spec.targets.values()))]
    assert plan.ontology.relationships[0].kind == "same_instance"
    assert plan.rules[0].program.require.match == "one_to_one"
    assert bundle.report.checks[0].status == "NG"
    scores = experiment.score(altered, bundle, spec)
    assert all(scores[layer]["state"] == "incorrect" for layer in ("concept", "binding", "judgment"))


@pytest.mark.parametrize("change,index", [("wrong_comparison", 2), ("extra_comparison", 2), ("reverse_projection", 6), ("foreign_normalization", 4), ("source_filter", 1), ("target_filter", 1)])
def test_wrong_concept_cannot_hide_behind_a_coincidentally_correct_verdict(experiment, tmp_path, change, index):
    from audit.models import ValueComparison, ValueTest

    case = experiment.cases()[index]
    bundle, spec = executed(experiment, case, tmp_path / "case")
    plan = bundle.plans[next(iter(spec.targets.values()))]
    program = plan.rules[0].program
    relation = program.require
    if change == "wrong_comparison":
        relation.comparisons[0].source_field = "code"
    elif change == "extra_comparison":
        relation.comparisons.append(ValueComparison(source_field="code", target_field="code", mode="exact"))
    elif change == "reverse_projection":
        relation.source_key.fields = ["name", "code"]
    elif change == "foreign_normalization":
        relation.source_key.normalization = "whitespace"
    elif change == "source_filter":
        program.when = ValueTest(kind="value_test", field="code", op="not_empty")
    else:
        relation.target_when = ValueTest(kind="value_test", field="code", op="not_empty")
    # Recompute rather than retaining an old report. These source values happen
    # to leave the final status unchanged, while the rule meaning is wrong.
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {next(iter(spec.targets)): plan}).report
    scores = experiment.score(case, bundle, spec)
    assert scores["concept"]["state"] == "incorrect"
    assert scores["binding"]["state"] == scores["judgment"]["state"] == "correct"


def test_grouping_without_member_evidence_is_a_binding_error(experiment, tmp_path):
    case = experiment.cases()[2]
    bundle, spec = executed(experiment, case, tmp_path / "case")
    bundle.trace.uses[0].inputs["target"].scopes[0].objects[0].evidence = []
    scores = experiment.score(case, bundle, spec)
    assert scores["binding"]["state"] == "incorrect"
    assert "lacks readable member evidence" in scores["binding"]["errors"][0]


def test_file_candidates_may_include_other_authorized_inputs(experiment, tmp_path):
    case = experiment.cases()[4]
    bundle, spec = executed(experiment, case, tmp_path / "case")
    files = bundle.trace.uses[0].inputs["target"]
    files.document_ids.append(case.identity(case.target))
    plan = bundle.plans[next(iter(spec.targets.values()))]
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {next(iter(spec.targets)): plan}).report
    scores = experiment.score(case, bundle, spec)
    assert all(scores[layer]["state"] == "correct" for layer in ("concept", "binding", "judgment"))
    files.document_ids = [case.identity(case.target)]
    scores = experiment.score(case, bundle, spec)
    assert scores["binding"]["state"] == "incorrect"
    assert "required file entity is missing" in scores["binding"]["errors"][0]
    files.document_ids = [case.identity(case.tables[0].filename), "not-an-authorized-input"]
    with pytest.raises(ValueError, match="outside frozen"):
        experiment.score(case, bundle, spec)
    concept = next(entity for entity in plan.ontology.entities if entity.id == "target")
    _, errors = experiment._collection(case.tables[0], case, bundle.trace, files, concept, "target", bundle.documents)
    assert any("outside current inputs" in error for error in errors)


def test_actual_local_prompt_and_readable_stage_inputs_hide_case_key(experiment, tmp_path, monkeypatch):
    from local_grok_runner import LocalGrokRunner

    monkeypatch.setattr(experiment, "preserve_code", lambda output: (output / "code").mkdir() or "a" * 64)
    captured = []

    class Observed(experiment.ControlledRunner):
        def __init__(self, case, inputs, output):
            super().__init__(case, inputs, output)
            self.original_inputs = inputs

        def run(self, work, prompt, schema, deadline, *, reasoning_effort):
            stage = self.output / ("prompt-" + str(len(captured)))
            stage.mkdir()
            local = LocalGrokRunner(SimpleNamespace(llm_api_key="test-key", llm_base_url="http://127.0.0.1"), self.output / "unused", "/usr/bin/false", input_root=self.original_inputs)
            local.helpers = self.output / "python" / "agent" / "semantic"
            actual, _ = local._stage_prompt(work, prompt, stage)
            originals = stage / "sources"
            local._stage_sources(work, originals)
            if (work / "rule-source.json").exists():
                files = list(originals.iterdir())
                assert len(files) == 1 and files[0].name == "rule-0.docx"
                source = json.loads((work / "rule-source.json").read_text())
                assert hashlib.sha256(files[0].read_bytes()).hexdigest() == source["source"]["sha256"]
            else:
                assert {path.name for path in originals.iterdir()} == {"manifest.json", "document-0.xlsx", "document-1.xlsx", "rule-0.docx"}
            readable = actual + (stage / "inputs.json").read_text()
            readable += "".join(str(path) + path.read_text() for path in work.glob("*.json"))
            readable += "".join(str(path) for path in originals.iterdir())
            assert self.case.key not in readable
            assert "expected_status" not in readable
            assert "identity_columns" not in readable
            captured.append(schema.__name__)
            return super().run(work, prompt, schema, deadline, reasoning_effort=reasoning_effort)

    result = experiment.run((experiment.cases()[6],), tmp_path / "run", Observed, seconds=60, live=False)
    assert result["runs"][0]["case"] == "literal-concatenation-differs"
    assert len(captured) == 2
    directories = [path for path in (tmp_path / "run").iterdir() if path.is_dir() and path.name != "code"]
    assert len(directories) == 1 and directories[0].name.startswith("unit-")
    assert result["layers"]["judgment"] == {"correct": 1}


@pytest.mark.parametrize("change", ["identity", "comparison", "oracle"])
def test_form_properties_in_one_column_keep_distinct_source_points(experiment, tmp_path, change):
    case = experiment.cases()[9]
    if change == "oracle":
        case = replace(case, tables=tuple(replace(table, identity_cells=("B2",), comparison_cells=("B3",)) for table in case.tables))
    bundle, spec = executed(experiment, case, tmp_path / "case")
    plan = bundle.plans[next(iter(spec.targets.values()))]
    relation = next(experiment.related_nodes(plan.rules[0].program.require))
    if change == "identity":
        for entity in plan.ontology.entities:
            entity.identity = ["name"]
        relation.source_key.fields = relation.target_key.fields = ["name"]
    elif change == "comparison":
        relation.comparisons[0].source_field = relation.comparisons[0].target_field = "code"
    # All values are in physical column B; checking just the column would miss
    # both wrong identities and a comparison of the wrong property.
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {next(iter(spec.targets)): plan}).report
    scores = experiment.score(case, bundle, spec)
    assert scores["concept"]["state"] == "incorrect"
    assert scores["binding"]["state"] == scores["judgment"]["state"] == "correct"
    for scope in bundle.trace.uses[0].inputs["source"].scopes:
        assert scope.objects[0].fields["code"][0].cell == "B3"
        assert scope.objects[0].fields["name"][0].cell == "B2"


@pytest.mark.parametrize("index", [5, 6])
@pytest.mark.parametrize("identity", [["code"], ["code", "name"]])
def test_literal_query_does_not_invent_an_unstated_source_identity(experiment, tmp_path, index, identity):
    case = experiment.cases()[index]
    assert case.tables[0].identity_columns is None
    bundle, spec = executed(experiment, case, tmp_path / "case")
    plan = bundle.plans[next(iter(spec.targets.values()))]
    source = next(entity for entity in plan.ontology.entities if entity.id == plan.rules[0].program.source)
    source.identity = identity
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {next(iter(spec.targets)): plan}).report
    scores = experiment.score(case, bundle, spec)
    assert all(scores[layer]["state"] == "correct" for layer in ("concept", "binding", "judgment"))


@pytest.mark.parametrize("change", ["projection", "missing_row", "wrong_source"])
def test_unspecified_literal_identity_still_requires_exact_projection_and_source_binding(experiment, tmp_path, change):
    case = experiment.cases()[5]
    bundle, spec = executed(experiment, case, tmp_path / "case")
    plan = bundle.plans[next(iter(spec.targets.values()))]
    if change == "projection":
        plan.rules[0].program.require.source_key.fields = ["name", "code"]
    elif change == "missing_row":
        bundle.trace.bindings[0].last_row = 2
        bundle.trace.uses[0].inputs["source"].scopes[0].objects = bundle.trace.uses[0].inputs["source"].scopes[0].objects[:1]
    else:
        source, target = bundle.trace.bindings
        source.document_id, source.source_sha256, source.table = target.document_id, target.source_sha256, target.table
        source.columns = {"code": "A", "name": "A"}
        for item in bundle.trace.uses[0].inputs["source"].scopes[0].objects:
            for locator in item.evidence:
                locator.sheet = target.table
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {next(iter(spec.targets)): plan}).report
    scores = experiment.score(case, bundle, spec)
    assert scores["concept" if change == "projection" else "binding"]["state"] == "incorrect"


def test_nonliteral_association_must_use_the_rules_explicit_identifier_key(experiment, tmp_path):
    case = replace(
        experiment.cases()[1],
        key="identifier-index-association",
        rule="基准清单每个工序的工序编号必须在控制标签索引的编号列一一对应。标签索引记录不表示工序实体；这是按编号建立的索引关联，分类不能代替编号。每个数据行是独立实例。",
        relation_kinds=("association",),
    )
    bundle, spec = executed(experiment, case, tmp_path / "case")
    plan = bundle.plans[next(iter(spec.targets.values()))]
    plan.ontology.relationships[0].kind = "association"
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {next(iter(spec.targets)): plan}).report
    assert all(experiment.score(case, bundle, spec)[layer]["state"] == "correct" for layer in ("concept", "binding", "judgment"))
    relation = plan.rules[0].program.require
    relation.source_key.fields = relation.target_key.fields = ["classification"]
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {next(iter(spec.targets)): plan}).report
    scores = experiment.score(case, bundle, spec)
    assert scores["concept"]["state"] == "incorrect"
    assert "explicit identifier key" in scores["concept"]["errors"][0]
    assert scores["binding"]["state"] == scores["judgment"]["state"] == "correct"


@pytest.mark.parametrize("key", ["classification-is-not-identity", "duplicates-remain-instances"])
@pytest.mark.parametrize("kind", ["association", "same_instance"])
def test_both_kinds_preserve_the_two_rules_obligations(experiment, tmp_path, key, kind):
    case = next(case for case in experiment.cases() if case.key == key)
    bundle, spec = executed(experiment, case, tmp_path / "case")
    target, rule = next(iter(spec.targets.items()))
    plan = bundle.plans[rule]
    plan.ontology.relationships[0].kind = kind
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {target: plan}).report
    scores = experiment.score(case, bundle, spec)
    assert scores["concept"] == {"state": "correct", "errors": []}
    assert scores["binding"]["state"] == scores["judgment"]["state"] == "correct"


@pytest.mark.parametrize("index", [2, 9, 10])
def test_two_rules_kind_choice_does_not_relax_other_concepts(experiment, tmp_path, index):
    case = experiment.cases()[index]
    bundle, spec = executed(experiment, case, tmp_path / "case")
    target, rule = next(iter(spec.targets.items()))
    plan = bundle.plans[rule]
    plan.ontology.relationships[0].kind = "association"
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {target: plan}).report
    scores = experiment.score(case, bundle, spec)
    assert scores["concept"] == {"state": "incorrect", "errors": ["relation meaning differs"]}
    assert scores["binding"]["state"] == scores["judgment"]["state"] == "correct"


@pytest.mark.parametrize("key", ["classification-is-not-identity", "duplicates-remain-instances"])
@pytest.mark.parametrize("change", ["extra_classification", "classification_instead_of_code", "projection", "match_any", "quantifier_exists", "precondition_gate"])
def test_association_does_not_relax_the_two_rules_obligations(experiment, tmp_path, key, change):
    from audit.models import ColumnExists

    case = next(case for case in experiment.cases() if case.key == key)
    bundle, spec = executed(experiment, case, tmp_path / "case")
    target, rule = next(iter(spec.targets.items()))
    plan = bundle.plans[rule]
    plan.ontology.relationships[0].kind = "association"
    program = plan.rules[0].program
    relation = program.require
    if change == "extra_classification":
        relation.source_key.fields = relation.target_key.fields = ["code", "classification"]
    elif change == "classification_instead_of_code":
        relation.source_key.fields = relation.target_key.fields = ["classification"]
    elif change == "projection":
        relation.source_key.template = relation.target_key.template = "{code}-X"
    elif change == "match_any":
        relation.match = "any"
    elif change == "quantifier_exists":
        program.quantifier = "exists"
    else:
        program.precondition = ColumnExists(kind="column_exists", input=program.source, field="code")
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {target: plan}).report
    scores = experiment.score(case, bundle, spec)
    assert scores["concept"]["state"] == "incorrect"
    assert "relation meaning differs" not in scores["concept"]["errors"]
    assert scores["binding"]["state"] == "correct"


def test_association_cannot_hide_deduplicated_occurrences(experiment, tmp_path):
    case = next(case for case in experiment.cases() if case.key == "duplicates-remain-instances")
    bundle, spec = executed(experiment, case, tmp_path / "case")
    plan = bundle.plans[next(iter(spec.targets.values()))]
    plan.ontology.relationships[0].kind = "association"
    scope = bundle.trace.uses[0].inputs["target"].scopes[0]
    scope.objects = [scope.objects[0], scope.objects[2]]
    with pytest.raises(ValueError, match="Logical objects must cover exactly"):
        experiment.score(case, bundle, spec)


@pytest.mark.parametrize("change", ["correct", "wrong_column", "no_question", "no_confirmation_requirement", "source_unbound", "not_expected_to_clarify"])
def test_only_grounded_target_identity_questions_allow_an_unresolved_mapping(experiment, tmp_path, change):
    from audit.bindings import FieldHole

    case = experiment.cases()[7]
    bundle, spec = executed(experiment, case, tmp_path / "case")
    source, target = bundle.trace.bindings
    target.columns.pop("code")
    target.field_holes["code"] = FieldHole(state="unresolved", reason="The work-unit meaning remains unconfirmed.")
    if change == "wrong_column":
        target.field_holes.pop("code")
        target.columns["code"] = "B"
    elif change == "no_question":
        target.interpretations = []
    elif change == "no_confirmation_requirement":
        target.interpretations[0].requires_confirmation = False
    elif change == "source_unbound":
        source.columns.pop("code")
        source.field_holes["code"] = FieldHole(state="unresolved", reason="A missing source identity is not the declared target ambiguity.")
        question = target.interpretations[0].model_copy(deep=True)
        question.id = "source-question"
        question.evidence[0].document_id = source.document_id
        question.evidence[0].locator.sheet = source.table
        source.interpretations = [question]
    elif change == "not_expected_to_clarify":
        case = replace(case, clarification=False)
    plan = bundle.plans[next(iter(spec.targets.values()))]
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {next(iter(spec.targets)): plan}).report
    scores = experiment.score(case, bundle, spec)
    assert scores["judgment"]["state"] == scores["binding"]["state"] == "correct"
    if change == "correct":
        assert scores["concept"]["state"] == "correct"
        assert scores["pending_identity_mappings"] == {"target": ["code"]}
        assert scores["clarifications"] == 1
    else:
        assert scores["concept"]["state"] == "incorrect"


@pytest.mark.parametrize("change", ["or_bypass", "double_not", "precondition_gate", "extra_eq", "extra_nonempty", "conditional_or"])
def test_boolean_obligation_structure_is_checked_independently_of_status(experiment, tmp_path, change):
    from audit.models import ColumnExists, RequireAll, RequireAny, RequireNot, ValueTest

    case = experiment.cases()[8]
    bundle, spec = executed(experiment, case, tmp_path / "case")
    target = next(iter(spec.targets))
    plan = experiment._effective_plans(bundle, spec)[target].model_copy(deep=True)
    bundle.plans[spec.targets[target]] = plan
    bundle.trace.plan_refinements = []
    program = plan.rules[0].program
    relation = next(experiment.related_nodes(program.require))
    code = ValueTest(kind="value_test", field="code", op="not_empty")
    if change == "or_bypass":
        program.require = RequireAny(kind="any", items=[relation, code])
    elif change == "double_not":
        program.require = RequireNot(kind="not", item=RequireNot(kind="not", item=relation))
    elif change == "precondition_gate":
        program.precondition = ColumnExists(kind="column_exists", input=program.source, field="code")
    elif change == "extra_eq":
        program.require = RequireAll(kind="all", items=[relation, ValueTest(kind="value_test", field="code", op="eq", value="010")])
    elif change == "extra_nonempty":
        program.require = RequireAll(kind="all", items=[relation, ValueTest(kind="value_test", field="classification", op="not_empty")])
    else:
        program.require = RequireAll(kind="all", items=[relation, RequireAny(kind="any", items=[code, ValueTest(kind="value_test", field="product", op="not_empty")])])
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {target: plan}).report
    scores = experiment.score(case, bundle, spec)
    assert scores["concept"]["state"] == ("correct" if change == "double_not" else "incorrect")
    assert scores["binding"]["state"] == scores["judgment"]["state"] == "correct"


def test_positive_allows_only_entailed_and_guards(experiment, tmp_path):
    from audit.models import RequireAll, ValueTest

    case = experiment.cases()[8]
    bundle, spec = executed(experiment, case, tmp_path / "case")
    target = next(iter(spec.targets))
    plan = experiment._effective_plans(bundle, spec)[target].model_copy(deep=True)
    bundle.plans[spec.targets[target]] = plan
    bundle.trace.plan_refinements = []
    program = plan.rules[0].program
    program.require = RequireAll(
        kind="all", items=[ValueTest(kind="value_test", field="code", op="not_empty"), RequireAll(kind="all", items=[ValueTest(kind="value_test", field="product", op="not_empty"), program.require])]
    )
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {target: plan}).report
    assert all(experiment.score(case, bundle, spec)[layer]["state"] == "correct" for layer in ("concept", "binding", "judgment"))


def test_literal_projection_does_not_imply_nonempty_components(experiment, tmp_path):
    from audit.models import RequireAll, ValueTest

    case = experiment.cases()[5]
    bundle, spec = executed(experiment, case, tmp_path / "case")
    target = next(iter(spec.targets))
    plan = bundle.plans[spec.targets[target]]
    program = plan.rules[0].program
    program.require = RequireAll(kind="all", items=[ValueTest(kind="value_test", field="name", op="not_empty"), program.require])
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {target: plan}).report
    scores = experiment.score(case, bundle, spec)
    assert scores["concept"]["state"] == "incorrect"
    assert scores["judgment"]["state"] == "correct"


def test_metadata_nonempty_name_is_mandatory_and_not_taken_from_oracle(experiment, tmp_path):
    case = replace(experiment.cases()[9], comparison_nonempty=False)
    bundle, spec = executed(experiment, case, tmp_path / "case")
    target = next(iter(spec.targets))
    plan = bundle.plans[spec.targets[target]]
    assert any(getattr(item, "field", None) == "name" and getattr(item, "op", None) == "not_empty" for item in plan.rules[0].program.require.items)
    case = replace(case, comparison_nonempty=True)
    plan.rules[0].program.require = next(experiment.related_nodes(plan.rules[0].program.require))
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {target: plan}).report
    scores = experiment.score(case, bundle, spec)
    assert scores["concept"]["state"] == "incorrect"
    assert scores["judgment"]["state"] == "correct"


def test_two_blank_form_names_cannot_pass_by_equal_values(experiment, tmp_path):
    source = experiment.cases()[9]
    tables = tuple(replace(table, rows=tuple((row[0], None) if index == 1 else row for index, row in enumerate(table.rows))) for table in source.tables)
    case = replace(source, tables=tables, status="NG")
    bundle, spec = executed(experiment, case, tmp_path / "case")
    assert all(experiment.score(case, bundle, spec)[layer]["state"] == "correct" for layer in ("concept", "binding", "judgment"))
    target = next(iter(spec.targets))
    plan = bundle.plans[spec.targets[target]]
    plan.rules[0].program.require = next(experiment.related_nodes(plan.rules[0].program.require))
    bundle.report = experiment.evaluate_audit(bundle.trace, bundle.documents, {target: plan}).report
    assert bundle.report.checks[0].status == "PASS"
    scores = experiment.score(case, bundle, spec)
    assert scores["concept"]["state"] == scores["judgment"]["state"] == "incorrect"


def test_experiment_records_controlled_usage_as_unknown_not_real_tokens(experiment, tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "preserve_code", lambda output: (output / "code").mkdir() or "a" * 64)
    result = experiment.run(experiment.cases()[:2], tmp_path / "run", experiment.ControlledRunner, seconds=60, live=False)
    assert result["layers"] == {layer: {"correct": 2} for layer in ("concept", "binding", "judgment")}
    assert result["training_eligible"] is result["human_gold"] is False
    for row in result["runs"]:
        assert row["usage_source"] == "controlled"
        assert row["usage"]["input_tokens"] is row["usage"]["output_tokens"] is None
        assert row["usage"]["requests_started"] == 2
        assert row["source_unchanged"]
    persisted = json.loads((tmp_path / "run" / "report.json").read_text())
    assert persisted == result


def test_unknown_provider_consumption_stops_before_another_case(experiment, tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "preserve_code", lambda output: (output / "code").mkdir() or "a" * 64)

    class Uncertain(experiment.ControlledRunner):
        @property
        def usage(self):
            return super().usage | {"uncertain": True}

    result = experiment.run(experiment.cases(), tmp_path / "run", Uncertain, seconds=60, live=False)
    assert len(result["runs"]) == 1 and result["not_executed"] == 10
    assert result["runs"][0]["completed"] is False
    assert result["runs"][0]["error"]["type"] == "ValueError"
    assert result["runs"][0]["error"]["frames"]
    assert all(result["layers"][layer] == {"unassessable": 1} for layer in ("concept", "binding", "judgment"))
