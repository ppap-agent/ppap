from copy import deepcopy

import pytest
from ppap_core.rules import normalize_rule_body


def body():
    return {"kind": "engine", "type": "控制计划", "rules": [{"id": "R1", "category": "工序", "rule_to_check": "工序编号一致", "type_of_rule": "table"}]}


def entry():
    return {
        "id": "operation-1",
        "kind": "interpretation",
        "question_id": "question-1",
        "candidate_id": "candidate-1",
        "content": "作业单元编号表示工序编号",
        "scope": {"document_ids": ["cp"], "rule_ids": ["R1"]},
    }


def test_explicit_appendix_survives_normalization_without_mutating_input():
    raw = {**body(), "appendix": [entry()]}
    original = deepcopy(raw)
    assert normalize_rule_body(raw)["appendix"] == [entry()]
    assert raw == original
    assert "appendix" not in normalize_rule_body(body())


def test_appendix_target_rule_pair_is_distinct_from_interpreted_source():
    item = entry()
    item["scope"]["rule_targets"] = [{"document_id": "pfd-target", "rule_id": "R1"}]
    raw = {**body(), "appendix": [item]}
    original = deepcopy(raw)
    assert normalize_rule_body(raw)["appendix"] == [item]
    assert raw == original


@pytest.mark.parametrize(
    "change",
    [
        lambda e: e.update(content=""),
        lambda e: e.update(kind="review"),
        lambda e: e["scope"].update(document_ids=[]),
        lambda e: e["scope"].update(rule_ids=["missing"]),
        lambda e: e["scope"].update(rule_targets=[]),
        lambda e: e["scope"].update(rule_targets=[{"document_id": "pfd", "rule_id": "missing"}]),
        lambda e: e["scope"].update(rule_targets=[{"document_id": "pfd", "rule_id": "R1"}] * 2),
        lambda e: e["scope"].update(rule_targets=[{"document_id": "pfd", "rule_id": "R1", "extra": True}]),
        lambda e: e.update(hidden_history="do not read"),
    ],
)
def test_invalid_or_out_of_rule_appendix_is_rejected(change):
    item = entry()
    change(item)
    with pytest.raises(ValueError, match="invalid_rule_appendix"):
        normalize_rule_body({**body(), "appendix": [item]})
