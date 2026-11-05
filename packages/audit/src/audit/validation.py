"""Validate decoded snapshots before promotion; no Office/PDF parser runs here."""

from __future__ import annotations

import re
from typing import Any

from .evidence import EvidenceBudget


def _measured(visual: dict[str, Any]) -> dict[str, int] | None:
    measured = visual.get("measured_emu")
    if measured is None:
        return None
    if not isinstance(measured, dict) or set(measured) != {"from_row", "from_column", "to_row", "to_column"} or any(type(value) is not int or value < 0 for value in measured.values()):
        raise ValueError("Invalid spreadsheet visual measurement")
    return measured


def _validate_xlsx_visuals(table: dict[str, Any], budget: EvidenceBudget) -> None:
    from .document_tools import _XLSX_COORDINATE_MAX, _XLSX_COORDINATE_MIN, _archive_target, _regions_overlap, _xlsx_anchor_range, col_number

    def part(value: Any) -> bool:
        return isinstance(value, str) and bool(value) and not value.startswith("/") and _archive_target("", value) == value

    def marker(value: Any) -> bool:
        return (
            isinstance(value, dict)
            and set(value) == {"row", "column", "row_offset_emu", "column_offset_emu"}
            and all(type(number) is int for number in value.values())
            and 1 <= value["row"] <= 1048576
            and 1 <= value["column"] <= 16384
            and _XLSX_COORDINATE_MIN <= value["row_offset_emu"] <= _XLSX_COORDINATE_MAX
            and _XLSX_COORDINATE_MIN <= value["column_offset_emu"] <= _XLSX_COORDINATE_MAX
        )

    visuals = table.get("visual_evidence", [])
    if not isinstance(visuals, list) or len(visuals) > 100000:
        raise ValueError("Invalid spreadsheet visual sources")
    budget.consume(len(visuals))
    for visual in visuals:
        if (
            not isinstance(visual, dict)
            or set(visual) - {"source", "kind", "content_observed", "range", "from", "to", "measured_emu", "unresolved_reason", "references"}
            or not {"source", "kind", "content_observed", "range"} <= set(visual)
            or visual["content_observed"] is not False
            or not isinstance(visual["kind"], str)
            or not re.fullmatch("[A-Za-z][A-Za-z0-9]{0,63}", visual["kind"])
        ):
            raise ValueError("Invalid spreadsheet visual observation")
        source = visual["source"]
        if not isinstance(source, dict) or set(source) != {"part", "anchor_index"} or not part(source["part"]) or type(source["anchor_index"]) is not int or source["anchor_index"] < 1:
            raise ValueError("Invalid spreadsheet visual provenance")
        if any(name in visual and not marker(visual[name]) for name in ("from", "to")):
            raise ValueError("Invalid spreadsheet visual marker")
        region = visual["range"]
        if region is None:
            if visual.get("unresolved_reason") != "unsupported_anchor_geometry" or (
                visual["kind"] == "twoCellAnchor" and "from" in visual and "to" in visual and _xlsx_anchor_range(visual["from"], visual["to"]) is not None
            ):
                raise ValueError("Invalid unresolved spreadsheet visual")
        elif (
            not isinstance(region, list)
            or len(region) != 4
            or any(type(number) is not int for number in region)
            or not (1 <= region[0] <= region[2] <= table["rows"] and 1 <= region[1] <= region[3] <= table["cols"])
            or visual["kind"] != "twoCellAnchor"
            or "from" not in visual
            or "to" not in visual
            or region != _xlsx_anchor_range(visual["from"], visual["to"], _measured(visual))
            or "unresolved_reason" in visual
        ):
            raise ValueError("Invalid spreadsheet visual range")
        references = visual.get("references", [])
        if not isinstance(references, list):
            raise ValueError("Invalid spreadsheet visual references")
        budget.consume(len(references))
        for reference in references:
            if (
                not isinstance(reference, dict)
                or not isinstance(reference.get("relationship_id"), str)
                or not reference["relationship_id"]
                or not (set(reference) == {"relationship_id", "part"} and part(reference["part"]) or set(reference) == {"relationship_id", "state"} and reference["state"] == "external_unavailable")
            ):
                raise ValueError("Invalid spreadsheet visual reference")
    for address, cell in table["cells"].items():
        if cell.get("state") != "unknown_visual_evidence":
            continue
        budget.consume(len(visuals) + len(table.get("merges", [])) + 1)
        match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", address)
        if match is None:
            raise ValueError("Invalid spreadsheet visual cell")
        row, column = int(match[2]), col_number(match[1])
        region = next((span for span in table.get("merges", []) if span[0] <= row <= span[2] and span[1] <= column <= span[3]), [row, column, row, column])
        indices = [index for index, visual in enumerate(visuals) if visual["range"] is None or _regions_overlap(region, visual["range"])]
        value = cell.get("value")
        if (
            not (1 <= row <= table["rows"] and 1 <= column <= table["cols"])
            or not indices
            or not (value is None or isinstance(value, str) and not value.strip())
            or cell.get("source") != {"visual_evidence": indices}
            or "formula" in cell
            or "formula_attributes" in cell
        ):
            raise ValueError("Invalid spreadsheet visual cell observation")


def validate_snapshot(snapshot: dict[str, Any], sha256: str, file_format: str) -> None:
    source = snapshot.get("source", {})
    if not isinstance(source, dict) or source.get("sha256") != sha256 or source.get("format") != file_format:
        raise ValueError("Snapshot source version mismatch")
    if file_format == ".json":
        body = snapshot.get("rule_body")
        clauses = snapshot.get("clauses")
        if (
            snapshot.get("kind") != "published_rule"
            or not isinstance(snapshot.get("rule_version_id"), str)
            or not snapshot["rule_version_id"].strip()
            or not isinstance(body, dict)
            or not isinstance(body.get("rules"), list)
            or not isinstance(clauses, list)
            or not 1 <= len(clauses) <= 256
            or clauses != body["rules"]
        ):
            raise ValueError("Invalid published rule snapshot")
        ids = set()
        for row in clauses:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("id"), str)
                or not row["id"].strip()
                or not isinstance(row.get("rule_to_check"), str)
                or not row["rule_to_check"].strip()
                or row["id"] in ids
            ):  # noqa: E501
                raise ValueError("Invalid published rule clause")
            ids.add(row["id"])
        if "origin" in snapshot and (not isinstance(snapshot["origin"], dict) or not isinstance(snapshot["origin"].get("file_version_id"), str) or not snapshot["origin"]["file_version_id"].strip()):
            raise ValueError("Invalid published rule provenance")
        return
    tables = snapshot.get("tables", [])
    if not isinstance(tables, list) or len(tables) > 256 or len({table["id"] for table in tables}) != len(tables):
        raise ValueError("Invalid snapshot tables")
    cells = 0
    visual_budget = EvidenceBudget()
    cell_states = {"known", "unknown_visual_evidence", "unknown_uncovered_grid", "unknown_geometry"} if file_format == ".pdf" else {"known", "unknown_formula_cache", "unknown_excel_error"}
    if file_format in {".xlsx", ".xlsm"}:
        cell_states.add("unknown_visual_evidence")
    for table in tables:
        rows, cols = table["rows"], table["cols"]
        if type(rows) is not int or type(cols) is not int or not 0 <= rows <= 100000 or not 0 <= cols <= 16384:
            raise ValueError("Invalid snapshot dimensions")
        if not isinstance(table["cells"], dict):
            raise ValueError("Invalid snapshot cells")
        cells += len(table["cells"])
        if cells > 1000000:
            raise ValueError("Snapshot cell limit")
        if len(table.get("merges", [])) > 4096:
            raise ValueError("Snapshot merge limit")
        for cell in table["cells"].values():
            if cell.get("state") not in cell_states or not isinstance(cell.get("value"), (str, int, float, bool, type(None))):
                raise ValueError("Invalid cell observation")
        for span in table.get("merges", []):
            r1, c1, r2, c2 = span
            if any(type(value) is not int for value in span) or not (1 <= r1 <= r2 <= rows and 1 <= c1 <= c2 <= cols):
                raise ValueError("Invalid merge span")
        if file_format in {".xlsx", ".xlsm"}:
            _validate_xlsx_visuals(table, visual_budget)
        elif "visual_evidence" in table:
            raise ValueError("Spreadsheet visual sources require XLSX")
    pages = snapshot.get("pages", [])
    if len(pages) > 1000 or len(snapshot.get("paragraphs", [])) > 100000:
        raise ValueError("Snapshot page/paragraph limit")
    for index, page in enumerate(pages, 1):
        if page["page"] != index or len(page["blocks"]) > 100000 or any(not isinstance(block["text"], str) for block in page["blocks"]):
            raise ValueError("Invalid PDF blocks")
    if file_format == ".pdf":
        from .pdf_tables import validate_pdf_tables

        validate_pdf_tables(snapshot)
