"""Worker oracle JSON fails closed when the report or a row is incomplete."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ppap_corpus_contracts.acceptance_report import load_acceptance_report, write_acceptance_report


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
