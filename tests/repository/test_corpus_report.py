"""The report is computed from the oracle and rule outcomes."""

from collections import Counter

from corpus_contracts.product_round import BLOCKING, KNOWN_GAP, OK

from scripts.corpus_report import coverage, coverage_gaps, main, summarize, unstable_rules, violations


def _row(truth, actual, severity, *, dataset="v2", case="c", file="f.pdf", rule="1"):
    return {"kind": "rule", "dataset": dataset, "case": case, "file": file, "rule": rule, "truth": truth, "actual": actual, "severity": severity}


def test_clause_without_a_failing_sample_is_a_gap():
    matrix = {("v2", "控制计划", "1"): {"pdf": Counter({"PASS": 2})}, ("v2", "控制计划", "2"): {"pdf": Counter({"PASS": 1}), "xlsx": Counter({"NG": 1})}}
    assert coverage_gaps(matrix) == [("v2", "控制计划", "1", "NG")]


def test_any_difference_from_the_truth_fails_the_run():
    assert violations(summarize([_row("NG", "NG", OK)] * 999 + [_row("NG", "UNABLE", BLOCKING)]))
    assert not violations(summarize([_row("NG", "NG", OK), _row("NG", "UNABLE", KNOWN_GAP)]))


def test_false_pass_is_counted_apart():
    assert summarize([_row("NG", "PASS", BLOCKING), _row("PASS", "NG", BLOCKING)])["v2"]["false_pass"] == 1


def test_live_rounds_with_different_answers_are_listed():
    assert unstable_rules([_row("NG", "NG", OK), _row("NG", "UNABLE", BLOCKING), _row("PASS", "PASS", OK, rule="2")]) == [("v2", "c", "f.pdf", "1", {"NG": 1, "UNABLE": 1})]


def test_empty_results_directory_cannot_pass(tmp_path):
    assert main(["--results", str(tmp_path)]) == 1


def test_real_oracle_has_a_matrix(tmp_path):
    from corpus_contracts.oracle import load_oracle

    assert coverage(load_oracle())
    assert main(["--output", str(tmp_path / "report.md")]) == 0
    assert "覆盖矩阵" in (tmp_path / "report.md").read_text()
