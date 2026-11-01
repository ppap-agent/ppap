"""Read recorded proposals; calculate only the declared native-numeric row slice.

This is a small research check, not execution of the complete customer rules.
It never calls a model, changes an oracle, or fills missing image contents.
"""

from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from cases import build_cases
from openpyxl.utils import column_index_from_string, get_column_letter


def cell_value(case, row, column):
    col = column_index_from_string(column)
    for merged in case["merged_ranges"]:
        if merged["min_row"] <= row <= merged["max_row"] and merged["min_col"] <= col <= merged["max_col"]:
            row, col = merged["min_row"], merged["min_col"]
            break
    position = f"{get_column_letter(col)}{row}"
    value = next((item["value"] for item in case["observations"] if item["cell"] == position), None)
    return position, value


def numeric(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def evaluate(case, holes):
    records = holes.get("records", {})
    result = {"case": case["id"], "scope": "declared native-numeric row slice only", "rows": [], "unassessed_rows": [], "full_document_audit": False}
    for row in case["business_rows"]:
        row_result = {"row": row, "tolerance_and_judgment": "UNABLE", "duplicate_numeric_values": "UNABLE", "sources": {}}
        if records.get("status") != "resolved" or row not in records.get("rows", []):
            row_result["reason"] = "record_not_resolved"
            result["rows"].append(row_result)
            continue
        fields = {}
        for name in ("specification", "measured", "judgment"):
            hole = holes.get(name, {})
            fields[name] = [cell_value(case, row, col) for col in hole.get("columns", [])] if hole.get("status") == "resolved" else []
            row_result["sources"][name] = [{"cell": cell, "value": value} for cell, value in fields[name]]
        values = [numeric(value) for _, value in fields["measured"]]
        specs = {str(value) for _, value in fields["specification"] if value is not None}
        judgments = {str(value).strip().casefold() for _, value in fields["judgment"] if value is not None}
        interval = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*±\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?\s*", next(iter(specs))) if len(specs) == 1 else None
        if values and all(value is not None for value in values):
            row_result["duplicate_numeric_values"] = "NG" if len(set(values)) != len(values) else "PASS"
            if interval and judgments:
                center, tolerance = map(Decimal, interval.groups())
                row_result["interval"] = [str(center - tolerance), str(center + tolerance)]
                row_result["tolerance_and_judgment"] = "PASS" if all(center - tolerance <= value <= center + tolerance for value in values) and judgments <= {"ok", "合格"} else "NG"
        row_result["expected_slice"] = {"tolerance_and_judgment": "NG" if case["id"] == "jifeng_error" else "PASS", "duplicate_numeric_values": "NG"}
        row_result["matches_source_inspection"] = all(row_result[key] == value for key, value in row_result["expected_slice"].items())
        result["rows"].append(row_result)
    result["unassessed_rows"] = [row for row in case["expected"]["records"] if row not in case["business_rows"]]
    result["unassessed_reason"] = "image-derived specifications or qualitative/non-numeric values are outside this evaluator"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--assets-root", type=Path, required=True)
    args = parser.parse_args()
    cases = build_cases(args.assets_root)
    events = [json.loads(line) for line in (args.run / "events.jsonl").read_text().splitlines()]
    results = []
    for case in cases:
        for strategy in ("independent", "shared", "staged"):
            selected = [event for event in events if event["case_id"] == case["id"] and event["strategy"] == strategy]
            if not selected:
                continue
            # Independent R3 and R5 keep their own proposals. Never overwrite a
            # disagreement with the last answer and call it shared understanding.
            calculation_events = [event for event in selected if strategy != "independent" or event["stage"] == "tolerance"]
            holes = {hole["name"]: hole for event in calculation_events if not event["errors"] for hole in event["answer"]["holes"]}
            item = evaluate(case, holes)
            item["strategy"] = strategy
            if strategy == "independent":
                duplicate_events = [event for event in selected if event["stage"] == "duplicates" and not event["errors"]]
                duplicate_holes = {hole["name"]: hole for event in duplicate_events for hole in event["answer"]["holes"]}
                separate = evaluate(case, duplicate_holes)
                for row, other in zip(item["rows"], separate["rows"]):
                    row["independent_duplicate_check"] = other["duplicate_numeric_values"]
                    row["independent_duplicate_sources"] = other["sources"].get("measured", [])
                    row["duplicate_numeric_values"] = other["duplicate_numeric_values"]
                    row["matches_source_inspection"] = all(row[key] == value for key, value in row.get("expected_slice", {}).items()) if row.get("expected_slice") else False
            results.append(item)
    report = {
        "results": results,
        "full_document_acceptance": False,
        "human_binding_gold": False,
        "numeric_rows_checked": sum(len(item["rows"]) for item in results),
        "row_pairs_matching_source_inspection": sum(row.get("matches_source_inspection", False) for item in results for row in item["rows"]),
    }
    target = args.run / "numeric_slice.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({key: report[key] for key in ("numeric_rows_checked", "row_pairs_matching_source_inspection", "full_document_acceptance")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
