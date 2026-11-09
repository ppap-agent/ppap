"""The report is computed from the oracle and rule outcomes."""

import json
import os
import subprocess
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
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


def _complete_round(monkeypatch, directory, rows=None):
    expected = {"1": SimpleNamespace(status="PASS"), "2": SimpleNamespace(status="NG")}
    cases = [SimpleNamespace(dataset="customer", case_id="bundle", files=[SimpleNamespace(role="target", filename="part.xlsx", checker_group="suite", path=Path("part.xlsx"), expected=expected)])]
    monkeypatch.setattr("scripts.corpus_report.load_oracle", lambda: cases)
    rows = rows if rows is not None else [_row(item.status, item.status, OK, dataset="customer", case="bundle", file="part.xlsx", rule=rule) for rule, item in expected.items()]
    directory.mkdir()
    (directory / "rules.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    return rows


@pytest.mark.parametrize("change", ["missing", "duplicate", "extra", "wrong-case", "wrong-truth"])
def test_complete_live_round_rejects_incomplete_or_misbound_results(tmp_path, monkeypatch, change):
    directory = tmp_path / "round"
    rows = _complete_round(monkeypatch, directory)
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows.append(rows[0])
    elif change == "extra":
        rows.append({**rows[0], "rule": "other"})
    elif change == "wrong-case":
        rows[0]["case"] = "other"
    else:
        rows[0]["truth"] = "NG"
    (directory / "rules.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert main(["--results", str(directory), "--require-complete"]) == 1


def test_each_live_round_must_be_complete_not_just_the_combined_rows(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    rows = _complete_round(monkeypatch, first)
    _complete_round(monkeypatch, second, rows[1:])
    (first / "rules.jsonl").write_text(json.dumps(rows[0]) + "\n")
    assert main(["--results", str(first), "--results", str(second), "--require-complete"]) == 1


def test_complete_round_allows_equivalent_order_and_reports_business_failures(tmp_path, monkeypatch):
    directory = tmp_path / "round"
    rows = _complete_round(monkeypatch, directory)
    (directory / "rules.jsonl").write_text("".join(json.dumps(row) + "\n" for row in reversed(rows)))
    assert main(["--results", str(directory), "--require-complete"]) == 0
    rows[0].update(actual="NG", severity=BLOCKING)
    (directory / "rules.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    output = tmp_path / "report.md"
    assert main(["--results", str(directory), "--require-complete", "--output", str(output)]) == 1
    assert "阻断" in output.read_text()


def test_missing_or_repeated_live_round_cannot_count_as_another_completed_round(tmp_path, monkeypatch):
    directory = tmp_path / "first"
    _complete_round(monkeypatch, directory)
    assert main(["--results", str(directory), "--results", str(tmp_path / "missing"), "--require-complete"]) == 1
    assert main(["--results", str(directory), "--results", str(directory), "--require-complete"]) == 1


@pytest.mark.parametrize("status", [0, 1, 124])
def test_live_workflow_retains_execution_failure_and_separates_round_evidence(tmp_path, status):
    workflow = yaml.load((Path(__file__).resolve().parents[2] / ".github/workflows/corpus-eval.yml").read_text(), Loader=yaml.BaseLoader)
    step = next(step for step in workflow["jobs"]["evaluate"]["steps"] if step.get("name") == "Run every oracle case against the live model")
    command = tmp_path / "python3"
    command.write_text('#!/bin/sh\nprintf "%s\\n" "$PPAP_CORPUS_REPORT_DIR" >> "$OBSERVED"\nif test "$(wc -l < "$OBSERVED")" -eq 1; then exit "$TEST_EXIT"; fi\n')
    command.chmod(0o755)
    observed = tmp_path / "observed"
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "ROUNDS": "2", "OBSERVED": str(observed), "TEST_EXIT": str(status)}
    result = subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert (result.returncode == 0) == (status == 0), result.stderr
    paths = observed.read_text().splitlines()
    assert len(paths) == len(set(paths)) == 2 and all(paths)
    report = next(step for step in workflow["jobs"]["evaluate"]["steps"] if step.get("name") == "Report differences from the human truth")
    assert report["if"] == "always()"
    command = tmp_path / "uv"
    command.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$OBSERVED"\n')
    command.chmod(0o755)
    result = subprocess.run(["bash", "-c", report["run"]], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    arguments = observed.read_text().splitlines()
    assert "--require-complete" in arguments
    assert [arguments[index + 1] for index, argument in enumerate(arguments) if argument == "--results"] == paths
