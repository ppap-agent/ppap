"""Positive deterministic recovery of existing frozen original facts."""

from copy import deepcopy

from audit.fact_questions import BooleanFactEvaluation, FactQuestion
from product.store.fact_answers import _questions, _recompute, _results


def machine():
    question = FactQuestion(file_version_id="original", field="visual.sop", prompt="SOP是否选中？", answer_type="boolean", locator={"kind": "pdf_page", "page": 1})
    evaluation = BooleanFactEvaluation(file_version_id="original", facts={"visual.sop": None, "visual.signature": True}, description="SOP和签字")
    return [
        {
            "item_key": "original:R1",
            "status": "UNABLE",
            "suggestions": "请核对",
            "locator": {"file_version_id": "original", "rule_item_id": "R1", "semantic": {"fact_questions": [question.model_dump()], "fact_evaluation": evaluation.model_dump()}},
        },
        {"item_key": "original:R2", "status": "PASS", "suggestions": "已核实", "locator": {"file_version_id": "original", "rule_item_id": "R2"}},
    ]


def test_one_answer_only_recomputes_its_dependent_result():
    findings = machine()
    original = deepcopy(findings)
    question = _questions(findings, {"original"})[0]
    answer = {
        "operation_id": "answer-1",
        "file_version_id": "original",
        "field": question["field"],
        "value": True,
        "answered_by": "alice",
        "answered_at": "2026-11-07T00:00:00+00:00",
        "locator": question["locator"],
    }
    results = _recompute(findings, question, [answer])
    assert len(results) == 1
    assert results[0]["status"] == "PASS"
    assert results[0]["item_key"] == "original:R1"
    assert results[0]["confirmed_by"] == ["alice"]
    assert results[0]["sources"] == [{key: answer[key] for key in ("file_version_id", "field", "locator", "answered_by", "answered_at", "value")}]
    assert _results([{"results": results}]) == results
    assert findings == original
