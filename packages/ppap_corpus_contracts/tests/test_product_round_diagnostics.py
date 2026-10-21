"""Public comparison reports retain the engine's cause without changing verdicts."""

import pytest
from ppap_corpus_contracts.product_round import compare_file, format_verdicts


def test_status_mismatch_keeps_the_correlated_finding_explanation():
    explanation = "保证书印章文字未以公司结尾，且没有盖有公司章的授权声明。"
    result = {
        "coverage": {"rule_items": [{"file_version_id": "frozen", "id": "3", "coverage": "mapped", "status": "NG", "item_key": "psw:3"}]},
        "findings": [{"item_key": "psw:3", "status": "NG", "suggestions": explanation}],
        "note": "in-repo engine",
    }

    verdicts = compare_file(file_name="psw.pdf", version_id="frozen", expected={3: "PASS"}, result=result, setup_error=None)

    assert len(verdicts) == 1
    assert verdicts[0].kind == "status_mismatch"
    assert verdicts[0].actual == "NG"
    assert explanation in format_verdicts(verdicts)


@pytest.mark.parametrize(
    "findings",
    [
        [{"item_key": "another-item", "status": "NG", "suggestions": "another explanation"}],
        [{"item_key": "psw:3", "status": "NG", "suggestions": "first"}, {"item_key": "psw:3", "status": "NG", "suggestions": "second"}],
        [{"item_key": "psw:3", "status": "PASS", "suggestions": "contradictory result"}],
    ],
    ids=["different-item", "ambiguous-findings", "different-status"],
)
def test_unrelated_or_ambiguous_findings_do_not_become_a_failure_explanation(findings):
    result = {
        "coverage": {"rule_items": [{"file_version_id": "frozen", "id": "3", "coverage": "mapped", "status": "NG", "item_key": "psw:3"}]},
        "findings": findings,
    }

    verdicts = compare_file(file_name="psw.pdf", version_id="frozen", expected={3: "PASS"}, result=result, setup_error=None)

    assert verdicts[0].kind == "status_mismatch"
    assert verdicts[0].actual == "NG"
    assert verdicts[0].detail == ""


@pytest.mark.parametrize("include_finding", [True, False], ids=["finding-explanation", "round-note-fallback"])
def test_execution_failure_keeps_its_explanation_or_existing_round_note(include_finding):
    result = {
        "coverage": {"rule_items": [{"file_version_id": "frozen", "id": "3", "coverage": "mapped", "status": "ERR", "item_key": "psw:3"}]},
        "findings": [{"item_key": "psw:3", "status": "ERR", "suggestions": "模型服务无法完成请求。"}] if include_finding else [],
        "note": "round execution failed",
    }

    verdicts = compare_file(file_name="psw.pdf", version_id="frozen", expected={3: "PASS"}, result=result, setup_error=None)

    assert verdicts[0].kind == "execution_failure"
    assert verdicts[0].actual == "ERR"
    assert verdicts[0].detail == ("模型服务无法完成请求。" if include_finding else "round execution failed")
