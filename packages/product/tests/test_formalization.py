"""Offline clause formalization: the check of the model's rewrite and the CLI. No model and no database."""

import io
import json
from types import SimpleNamespace

import pytest
from product.formalization.__main__ import main, run
from product.formalization.text import FormalizationError, accept_text_points, condition_questions, text_prompts

_POINTS = {"sections": [{"title": "检查", "items": ["“批号”“数量”条件成立 → 通过；否则 → 不通过"]}], "review": [], "suggested_clause": ""}


@pytest.mark.parametrize(
    "clause_text",
    [
        "当“批号”列下内容不为空，且同行的“数量”内容不为空则通过，否则不通过。",
        "如果报告类型为年度，并且报告上有签名则合格，否则不合格",
    ],
)
def test_a_leading_condition_is_reported_with_both_readings(clause_text):
    [question] = condition_questions(clause_text)
    assert [item["id"] for item in question["choices"]] == ["required", "scope"]
    assert question["choices"][0]["counterexample"] != question["choices"][1]["counterexample"]


@pytest.mark.parametrize("clause_text", ["当“批号”列下内容不为空则通过，否则不通过。", "当报告类型为年度，且报告上有签名则通过，否则不适用。", "报告类型为年度且有签名"])
def test_a_clause_without_a_separable_condition_is_not_asked(clause_text):
    assert condition_questions(clause_text) == []


def test_a_rewrite_that_drops_a_quoted_term_or_number_is_refused():
    clause = "“批号”不为空且“数量”大于 10"
    with pytest.raises(FormalizationError) as caught:
        accept_text_points(_POINTS, clause)
    assert caught.value.code == "meaning_changed" and "10" in caught.value.detail
    with pytest.raises(FormalizationError):
        accept_text_points("not json", clause)


def test_a_suggestion_stands_only_beside_a_review_question():
    clause = "“批号”“数量”非空"
    plain = accept_text_points({**_POINTS, "suggested_clause": "改写"}, clause)
    assert plain["suggested_clause"] == "" and plain["sections"][0]["title"] == "检查"
    asked = accept_text_points({**_POINTS, "review": ["空表判什么？"], "suggested_clause": "  空表不通过  "}, clause)
    assert asked["review"] == ["空表判什么？"] and asked["suggested_clause"] == "空表不通过"
    assert json.loads(text_prompts(clause)[1].split("\n", 1)[1]) == {"clause_text": clause}


def test_the_cli_writes_one_line_per_clause_and_fails_when_any_clause_is_refused(tmp_path):
    source = tmp_path / "rules.json"
    rules = [{"id": "c1", "rule_to_check": "“批号”“数量”非空"}, {"id": "c2", "rule_to_check": "“签名”存在"}, {"id": "c3", "rule_to_check": ""}]
    source.write_text(json.dumps({"body": {"rules": rules}}, ensure_ascii=False), encoding="utf-8")
    calls = []

    def ai(task, **kwargs):
        calls.append(task)
        return SimpleNamespace(output=_POINTS)

    output = tmp_path / "out.jsonl"
    assert main([str(source), "--output", str(output)], ai=ai) == 1
    lines = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [(line["clause_id"], line["state"]) for line in lines] == [("c1", "formalized"), ("c2", "refused"), ("c3", "failed")]
    assert calls == ["rule_formalization", "rule_formalization"]

    out = io.StringIO()
    assert run([rules[0]], out, None) == 0
    assert json.loads(out.getvalue())["state"] == "skipped"
