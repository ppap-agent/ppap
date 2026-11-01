"""Research scoring must not hide missing work or convert mismatches to passes."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("research_score_corpus", Path(__file__).with_name("score_corpus.py"))
assert SPEC is not None and SPEC.loader is not None
score = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = score
SPEC.loader.exec_module(score)


def case(tmp_path: Path, name: str, expected: dict[int, str], content: bytes | None = None):
    source = tmp_path / name
    source.write_bytes(content if content is not None else name.encode())
    return score.OracleCase(name, source, "1", expected)


def artifact(tmp_path: Path, name: str, original, checks):
    path = tmp_path / name
    path.write_text(json.dumps({"document_sha256": score.sha256(original.file_path), "checks": checks}))
    return path


def test_strict_statuses_missing_rules_and_unexecuted_scope_have_separate_denominators(tmp_path):
    first = case(tmp_path, "first.xlsx", {1: "NG", 2: "PASS", 3: "PASS", 4: "NG", 5: "PASS", 6: "NG", 7: "PASS"})
    absent = case(tmp_path, "not-run.pdf", {1: "PASS", 2: "NG"})
    supplied = artifact(tmp_path, "execution.json", first, [{"rule_id": f"R{index}", "status": status} for index, status in enumerate(["PASS", "NG", "UNABLE", "ERR", "NA", "NG"], 1)])
    rows, metrics = score.score_artifacts([supplied], [first, absent])
    assert len(rows) == 7  # A missing result stays in the submitted-case denominator.
    assert metrics["counts"] == {"match": 1, "false_pass": 1, "false_ng": 1, "unable": 1, "err": 1, "na": 1, "missing_rule": 1}
    assert metrics["scope"] == {"cases": 2, "expected_rules": 9}
    assert metrics["submitted"] == {"cases": 1, "expected_rules": 7, "present_rules": 6}
    assert metrics["not_executed"] == {"cases": 1, "expected_rules": 2}
    assert metrics["rule_coverage"] == 6 / 9
    assert metrics["strict_full_scope_match_rate"] == 1 / 9
    assert next(row for row in rows if row["rule_index"] == 4)["kind"] == "execution_failure"


@pytest.mark.parametrize(
    "checks",
    [
        [{"rule_id": "R1", "status": "NG"}, {"rule_id": "R1", "status": "PASS"}],
        [{"rule_id": "R2", "status": "PASS"}],
        [{"rule_id": "R01", "status": "PASS"}],
        [{"rule_id": "R1", "status": "unknown"}],
        [{"rule_id": 1, "status": "PASS"}],
        {"R1": "PASS"},
    ],
)
def test_malformed_or_extra_results_are_rejected_instead_of_filtered(tmp_path, checks):
    original = case(tmp_path, "source.xlsx", {1: "PASS"})
    supplied = artifact(tmp_path, "execution.json", original, checks)
    with pytest.raises(ValueError):
        score.score_artifacts([supplied], [original])


def test_unknown_and_ambiguous_hashes_do_not_select_a_convenient_oracle(tmp_path):
    first = case(tmp_path, "first.xlsx", {1: "PASS"}, b"same original")
    alias = case(tmp_path, "alias.xlsx", {1: "NG"}, b"same original")
    other = case(tmp_path, "other.xlsx", {1: "PASS"}, b"other original")
    supplied = artifact(tmp_path, "execution.json", first, [{"rule_id": "R1", "status": "PASS"}])
    with pytest.raises(ValueError, match="unknown or ambiguous"):
        score.score_artifacts([supplied], [other])
    with pytest.raises(ValueError, match="unknown or ambiguous"):
        score.score_artifacts([supplied], [first, alias])


def test_duplicate_submissions_cannot_inflate_accuracy(tmp_path):
    original = case(tmp_path, "source.xlsx", {1: "PASS"})
    one = artifact(tmp_path, "one.json", original, [{"rule_id": "R1", "status": "PASS"}])
    two = artifact(tmp_path, "two.json", original, [{"rule_id": "R1", "status": "PASS"}])
    with pytest.raises(ValueError, match="More than one artifact"):
        score.score_artifacts([one, two], [original])


def test_duplicate_json_keys_do_not_overwrite_a_failed_result(tmp_path):
    original = case(tmp_path, "source.xlsx", {1: "PASS"})
    supplied = tmp_path / "execution.json"
    supplied.write_text('{"document_sha256":"' + score.sha256(original.file_path) + '","checks":[{"rule_id":"R1","status":"NG","status":"PASS"}]}')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        score.score_artifacts([supplied], [original])


def test_cli_reports_subset_without_full_pass_or_overwriting_prior_evidence(tmp_path, monkeypatch):
    first = case(tmp_path, "first.xlsx", {1: "PASS"})
    second = case(tmp_path, "second.pdf", {1: "NG"})
    supplied = artifact(tmp_path, "execution.json", first, [{"rule_id": "R1", "status": "PASS"}])
    output = tmp_path / "score.json"
    monkeypatch.setattr(score, "load_cases", lambda *_: ([first, second], {"corpus": "v2"}))
    monkeypatch.setattr(sys, "argv", ["score_corpus.py", "--corpus", "v2", "--output", str(output), str(supplied)])
    assert score.main() == 1
    payload = json.loads(output.read_text())
    assert payload["summary"]["strict_pass_rate"] == 1
    assert payload["run"]["strict_full_scope_match_rate"] == 0.5
    assert payload["run"]["not_executed"] == {"cases": 1, "expected_rules": 1}
    assert payload["run"]["product_acceptance"] is False
    saved = output.read_bytes()
    with pytest.raises(SystemExit) as error:
        score.main()
    assert error.value.code == 2
    assert output.read_bytes() == saved
