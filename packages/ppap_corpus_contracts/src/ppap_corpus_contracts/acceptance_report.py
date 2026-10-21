"""JSON authority and Markdown view for a product worker oracle run."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = {
    "file_name",
    "rule_index",
    "expected_status",
    "actual_status",
    "kind",
}


def _checked_rows(source_rows: Any, path: Path) -> list[dict[str, str]]:
    if not isinstance(source_rows, list):
        raise ValueError(f"Acceptance report has no rule_results list: {path}")
    rows = []
    for index, source_row in enumerate(source_rows, start=1):
        if not isinstance(source_row, dict) or not REQUIRED_FIELDS.issubset(source_row):
            raise ValueError(f"Invalid rule_results row {index} in {path}")
        rows.append({field: str(value) for field, value in source_row.items()})
    return rows


def _summary(rows: list[dict[str, str]]) -> dict[str, int | float]:
    def cases(kind: str | None = None) -> int:
        return len({(row.get("bundle", ""), row["file_name"]) for row in rows if kind is None or row["kind"] == kind})

    counts = {kind: sum(row["kind"] == kind for row in rows) for kind in ("match", "status_mismatch", "missing_rule", "execution_failure", "setup_failure")}
    return {
        "selected_cases": cases(),
        "total_rules": len(rows),
        "passed_rules": counts["match"],
        "mismatches": counts["status_mismatch"],
        "parity_cases": cases("missing_rule"),
        "parity_failed_rules": counts["missing_rule"],
        "execution_failures": cases("execution_failure"),
        "execution_failed_rules": counts["execution_failure"],
        "setup_failures": cases("setup_failure"),
        "setup_failed_rules": counts["setup_failure"],
        "strict_pass_rate": counts["match"] / len(rows) if rows else 0.0,
    }


def _groups(rows: list[dict[str, str]], field: str) -> dict[str, dict[str, int | float]]:
    keys = sorted({row.get(field, "") for row in rows})
    return {key: _summary([row for row in rows if row.get(field, "") == key]) for key in keys}


def _cell(value: Any) -> str:
    return escape(" ".join(str(value).split()), quote=False).replace("|", "\\|")


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    return ["| " + " | ".join(_cell(value) for value in row) + " |" for row in [headers, ["---"] * len(headers), *rows]]


def write_acceptance_report(path: Path, rows: list[dict[str, Any]], *, markdown_path: Path | None = None, run: dict[str, Any] | None = None) -> None:
    """Write observed rule rows; summaries are diagnostic, not an acceptance gate."""
    if markdown_path is not None and markdown_path.resolve() == path.resolve():
        raise ValueError("JSON and Markdown reports require different paths")
    checked = _checked_rows(rows, path)
    summary = _summary(checked)
    by_bundle, by_seq = _groups(checked, "bundle"), _groups(checked, "file_seq")
    payload = {"run": run or {}, "summary": summary, "by_bundle": by_bundle, "by_seq": by_seq, "rule_results": rows}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if markdown_path is None:
        return
    lines = ["# V3 POC Product API / Worker 验收报告", "", "汇总只描述本次提供的逐规则记录，不证明样例清单完整或浏览器用户流程。", "", "## Run", ""]
    lines += _table(["Field", "Value"], [[key, value] for key, value in sorted((run or {}).items())])
    lines += ["", "## Summary", "", f"规则匹配预期（包括预期 NG），不等于文件通过率：{summary['strict_pass_rate']:.2%}", ""]
    lines += _table(["Metric", "Value"], [[key, value] for key, value in summary.items()])
    for title, grouped in (("By Bundle", by_bundle), ("By Seq", by_seq)):
        lines += ["", "## " + title, ""]
        lines += _table(["Group", *summary], [[key, *(values[field] for field in summary)] for key, values in grouped.items()])
    lines += ["", "## Missing Rule Indices", ""]
    lines += _table(["Bundle", "Seq", "File", "Rule"], [[row.get("bundle", ""), row.get("file_seq", ""), row["file_name"], row["rule_index"]] for row in checked if row["kind"] == "missing_rule"])
    fields = ["bundle", "file_seq", "file_name", "rule_index", "expected_status", "actual_status", "kind", "failure_stage", "round_id", "job_id", "rule_desc", "manual_reason", "agent_suggestions"]
    lines += ["", "## Rule Details", ""]
    lines += _table(fields, [[row.get(field, "") for field in fields] for row in checked])
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_acceptance_report(path: Path) -> list[dict[str, str]]:
    """Read worker oracle rows. A missing file or incomplete row fails closed."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"Missing acceptance report: {path}") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Acceptance report cannot be read: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Acceptance report has no rule_results list: {path}")
    return _checked_rows(payload.get("rule_results"), path)
