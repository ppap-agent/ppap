"""Independent fixed oracles over actual source parsing and host evaluation."""

from __future__ import annotations

import importlib
import json
import time
from dataclasses import replace
from pathlib import Path

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


@pytest.mark.parametrize("index", range(9))
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
        relation_kind="association",
        match="any",
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
    from ppap_deep.semantic.models import ValueComparison, ValueTest

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
    assert len(result["runs"]) == 1 and result["not_executed"] == 8
    assert result["runs"][0]["completed"] is False
    assert result["runs"][0]["error"]["type"] == "ValueError"
    assert result["runs"][0]["error"]["frames"]
    assert all(result["layers"][layer] == {"unassessable": 1} for layer in ("concept", "binding", "judgment"))
