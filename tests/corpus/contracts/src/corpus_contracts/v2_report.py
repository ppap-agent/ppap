"""V2 baseline rows come from JSON reports, not Markdown."""

from __future__ import annotations

import json
from pathlib import Path


def build_report_paths(reports_root: Path, report_suffix: str) -> dict[str, Path]:
    return {
        "pdf_markdown": reports_root / f"v2_assets_pdf_rules_report_{report_suffix}.md",
        "pdf_json": reports_root / f"v2_assets_pdf_rules_report_{report_suffix}.json",
        "excel_markdown": reports_root / f"v2_assets_excel_rules_report_{report_suffix}.md",
        "excel_json": reports_root / f"v2_assets_excel_rules_report_{report_suffix}.json",
    }


def ensure_report_files_exist(report_paths: dict[str, Path]) -> None:
    """Fail when a required JSON report is missing. Markdown is not authority."""
    missing = [report_paths[key] for key in ("pdf_json", "excel_json") if not report_paths[key].exists()]
    if not missing:
        return
    formatted = "\n".join(str(path) for path in missing)
    raise FileNotFoundError(f"Missing report files:\n{formatted}")


def parse_rule_rows(json_path: Path) -> list[dict[str, str]]:
    """Read comparable V2 rows. A missing list or an incomplete row fails closed."""
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Report JSON cannot be read: {json_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Report JSON has no rule_results list: {json_path}")
    source_rows = payload.get("rule_results")
    if not isinstance(source_rows, list):
        raise ValueError(f"Report JSON has no rule_results list: {json_path}")

    required = {
        "file_name",
        "rule_index",
        "expected_status",
        "actual_status",
        "manual_reason",
        "agent_suggestions",
    }
    rows: list[dict[str, str]] = []
    for index, source_row in enumerate(source_rows, start=1):
        if not isinstance(source_row, dict) or not required.issubset(source_row):
            raise ValueError(f"Invalid rule_results row {index} in {json_path}")
        file_name = str(source_row["file_name"])
        rows.append(
            {
                "file_name": file_name,
                "file_stem": Path(file_name).stem,
                "rule_index": str(source_row["rule_index"]),
                "expected_status": str(source_row["expected_status"]),
                "actual_status": str(source_row["actual_status"]),
                "manual_reason": str(source_row["manual_reason"]),
                "agent_suggestions": str(source_row["agent_suggestions"]),
            }
        )
    return rows
