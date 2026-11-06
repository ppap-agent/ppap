"""Deterministic suggestions from independently confirmed answers, never root causes."""

from __future__ import annotations

import hashlib
import json
import re

REASON_CATEGORIES = {"unspecified", "missing_value", "incorrect_reading", "rule_application"}


def source_field(locator: dict) -> tuple | None:
    kind = locator.get("kind")
    if kind in {"xlsx_cell", "xlsx_range"}:
        start = re.match(r"^([A-Z]+)[0-9]+$", locator.get("cell", locator.get("start", "")))
        end = re.match(r"^([A-Z]+)[0-9]+$", locator.get("end", locator.get("cell", "")))
        if start and end and start[1] == end[1] and locator.get("sheet"):
            return ("xlsx_column", locator["sheet"], start[1])
    if kind in {"docx_cell", "pdf_cell"}:
        return (kind, locator.get("table", locator.get("page")), locator.get("column"))
    # A whole page or paragraph does not identify the same field across files.
    return None


def group_feedback(items: list[dict], *, tenant_id: str) -> list[dict]:
    groups: dict[tuple, list[str]] = {}
    for item in items:
        category = item.get("reason_category", "unspecified")
        fields = {source_field(evidence["locator"]) for evidence in item["evidence"] if evidence["file_version_id"] == item["file_version_id"]}
        fields.discard(None)
        if category == "unspecified" or category not in REASON_CATEGORIES or len(fields) != 1:
            continue
        key = (tenant_id, item["suite_key"], item["rule_clause_id"], item["original_status"], category, next(iter(fields)))
        groups.setdefault(key, []).append(item["id"])
    return [
        {
            "id": hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:24],
            "rule_clause_id": key[2],
            "original_status": key[3],
            "reason_category": key[4],
            "source_field": " · ".join(str(part) for part in key[5][1:]),
            "feedback_ids": ids,
        }
        for key, ids in groups.items()
        if len(ids) > 1
    ]
