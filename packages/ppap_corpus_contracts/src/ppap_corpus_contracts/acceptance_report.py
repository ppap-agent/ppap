"""JSON report for a product worker oracle run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = {
    "file_name",
    "rule_index",
    "expected_status",
    "actual_status",
    "kind",
}


def write_acceptance_report(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"rule_results": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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
    source_rows = payload.get("rule_results")
    if not isinstance(source_rows, list):
        raise ValueError(f"Acceptance report has no rule_results list: {path}")
    rows: list[dict[str, str]] = []
    for index, source_row in enumerate(source_rows, start=1):
        if not isinstance(source_row, dict) or not REQUIRED_FIELDS.issubset(source_row):
            raise ValueError(f"Invalid rule_results row {index} in {path}")
        rows.append({field: str(source_row[field]) for field in sorted(REQUIRED_FIELDS)})
    return rows
