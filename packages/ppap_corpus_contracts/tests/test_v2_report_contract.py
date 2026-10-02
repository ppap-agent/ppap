"""V2 aggregate rows are JSON authority and fail closed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ppap_corpus_contracts.v2_report import (
    build_report_paths,
    ensure_report_files_exist,
    parse_rule_rows,
)


def _rule_row() -> dict[str, object]:
    return {
        "file_name": "sample.xlsx",
        "rule_index": 1,
        "expected_status": "PASS",
        "actual_status": "PASS",
        "manual_reason": "人工依据 | 保留原文",
        "agent_suggestions": "AI 依据 | 保留原文",
    }


def test_baseline_rows_are_read_from_json_without_markdown(tmp_path: Path) -> None:
    json_path = tmp_path / "report.json"
    json_path.write_text(json.dumps({"rule_results": [_rule_row()]}, ensure_ascii=False), encoding="utf-8")

    rows = parse_rule_rows(json_path)

    assert rows == [
        {
            "file_name": "sample.xlsx",
            "file_stem": "sample",
            "rule_index": "1",
            "expected_status": "PASS",
            "actual_status": "PASS",
            "manual_reason": "人工依据 | 保留原文",
            "agent_suggestions": "AI 依据 | 保留原文",
        }
    ]


def test_report_presence_gate_requires_only_json(tmp_path: Path) -> None:
    report_paths = build_report_paths(tmp_path, "4_8")
    report_paths["pdf_json"].write_text("{}", encoding="utf-8")
    report_paths["excel_json"].write_text("{}", encoding="utf-8")

    ensure_report_files_exist(report_paths)


def test_missing_json_report_fails(tmp_path: Path) -> None:
    report_paths = build_report_paths(tmp_path, "4_8")
    report_paths["pdf_json"].write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="Missing report files"):
        ensure_report_files_exist(report_paths)


def test_invalid_json_rule_row_fails_closed(tmp_path: Path) -> None:
    json_path = tmp_path / "report.json"
    json_path.write_text('{"rule_results": [{"file_name": "sample.xlsx"}]}', encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid rule_results row"):
        parse_rule_rows(json_path)
