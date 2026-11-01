"""Worker oracle JSON fails closed when the report or a row is incomplete."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from corpus_contracts.acceptance_report import load_acceptance_report, write_acceptance_report


def test_round_trip_keeps_rule_rows(tmp_path: Path) -> None:
    path = tmp_path / "oracle.json"
    write_acceptance_report(
        path,
        [
            {
                "file_name": "a.xlsx",
                "rule_index": 1,
                "expected_status": "NG",
                "actual_status": "NG",
                "kind": "match",
            }
        ],
    )
    assert load_acceptance_report(path) == [
        {
            "actual_status": "NG",
            "expected_status": "NG",
            "file_name": "a.xlsx",
            "kind": "match",
            "rule_index": "1",
        }
    ]


def test_missing_report_fails(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Missing acceptance report"):
        load_acceptance_report(tmp_path / "missing.json")


def test_invalid_row_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "oracle.json"
    path.write_text(json.dumps({"rule_results": [{"file_name": "a.xlsx"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid rule_results row"):
        load_acceptance_report(path)


def test_json_and_markdown_preserve_rule_details_and_failure_groups(tmp_path: Path) -> None:
    rows = [
        {
            "bundle": "A",
            "file_seq": "8",
            "file_name": "a.xlsx",
            "rule_index": 1,
            "expected_status": "NG",
            "actual_status": "NG",
            "kind": "match",
            "manual_reason": "原文 | 原因\n下一行",
            "agent_suggestions": "缺少字段",
        },
        {"bundle": "A", "file_seq": "8", "file_name": "a.xlsx", "rule_index": 2, "expected_status": "PASS", "actual_status": "PASS", "kind": "match"},
        {"bundle": "A", "file_seq": "8", "file_name": "b.xlsx", "rule_index": 1, "expected_status": "PASS", "actual_status": "NG", "kind": "status_mismatch"},
        {"bundle": "A", "file_seq": "8", "file_name": "b.xlsx", "rule_index": 2, "expected_status": "NG", "actual_status": "", "kind": "missing_rule"},
        {
            "bundle": "B",
            "file_seq": "9",
            "file_name": "c.xlsx",
            "rule_index": 1,
            "expected_status": "NG",
            "actual_status": "ERR",
            "kind": "execution_failure",
            "failure_stage": "audit",
            "round_id": "test-round",
            "job_id": "test-job",
        },
        {"bundle": "B", "file_seq": "10", "file_name": "d.xlsx", "rule_index": 1, "expected_status": "PASS", "actual_status": "", "kind": "setup_failure", "failure_stage": "rule_parse"},
    ]
    path, markdown = tmp_path / "oracle.json", tmp_path / "oracle.md"

    write_acceptance_report(path, rows, markdown_path=markdown, run={"candidate_sha": "test-candidate", "boundary": "Product API / Worker"})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["rule_results"] == rows
    assert payload["run"] == {"candidate_sha": "test-candidate", "boundary": "Product API / Worker"}
    assert payload["summary"] == {
        "selected_cases": 4,
        "total_rules": 6,
        "passed_rules": 2,
        "mismatches": 1,
        "parity_cases": 1,
        "parity_failed_rules": 1,
        "execution_failures": 1,
        "execution_failed_rules": 1,
        "setup_failures": 1,
        "setup_failed_rules": 1,
        "strict_pass_rate": 1 / 3,
    }
    assert payload["by_bundle"]["A"]["selected_cases"] == 2
    assert payload["by_bundle"]["B"]["passed_rules"] == 0
    assert payload["by_seq"]["8"]["total_rules"] == 4
    assert payload["by_seq"]["10"]["setup_failures"] == 1
    loaded = load_acceptance_report(path)
    assert loaded[0]["manual_reason"] == "原文 | 原因\n下一行"
    assert loaded[4]["round_id"] == "test-round"
    text = markdown.read_text(encoding="utf-8")
    assert "33.33%" in text
    assert "| b.xlsx | 2 |" in text
    assert "原文 \\| 原因 下一行" in text
    assert "| execution_failure | audit | test-round | test-job |" in text
    assert "规则匹配预期（包括预期 NG），不等于文件通过率" in text


def test_markdown_cannot_overwrite_the_json_authority(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    with pytest.raises(ValueError, match="different paths"):
        write_acceptance_report(path, [], markdown_path=path)
    assert not path.exists()
