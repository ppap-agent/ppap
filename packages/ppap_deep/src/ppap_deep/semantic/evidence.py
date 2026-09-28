"""Resolve typed locations to observed content; never search snapshot metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .document_tools import col_number, effective, ref
from .models import ExcelCell, ExcelRange, Locator, PdfBlock, PublishedClause, WordCell, WordParagraph


@dataclass
class EvidenceBudget:
    remaining: int = 1_000_000

    def consume(self, steps: int) -> None:
        self.remaining -= steps
        if self.remaining < 0:
            raise ValueError("Evidence resolution budget exceeded")


@dataclass(frozen=True)
class Observation:
    locator: str
    values: tuple[str, ...]
    known: bool = True


def _address(address: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", address)
    if match is None:
        raise ValueError("Invalid cell address")
    return int(match[2]), col_number(match[1])


def _table(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [table for table in snapshot.get("tables", []) if table["id"] == name]
    if len(matches) != 1:
        raise ValueError("Evidence table missing or duplicated")
    return dict(matches[0])


def _cell(table: dict[str, Any], row: int, column: int) -> tuple[str, bool]:
    if not 1 <= row <= table["rows"] or not 1 <= column <= table["cols"]:
        raise ValueError("Evidence cell outside observed bounds")
    item = effective(table, row, column)
    # XLSX merged cells inherit only within the declared span. DOCX aliases
    # resolve through an existing anchor instead of trusting a duplicate value.
    anchor = str(item["anchor"])
    anchor_row, anchor_column = _address(anchor)
    if not 1 <= anchor_row <= table["rows"] or not 1 <= anchor_column <= table["cols"]:
        raise ValueError("Invalid merged cell anchor")
    if anchor != ref(row, column):
        if anchor not in table["cells"]:
            raise ValueError("Missing merged cell anchor")
        item = table["cells"][anchor]
    value = item.get("value")
    return "" if value is None else str(value), item.get("state") == "known"


def resolve(snapshot: dict[str, Any], locator: Locator, budget: EvidenceBudget | None = None) -> Observation:
    """All page/block/paragraph/table positions are one-based, including PDF."""
    budget = budget or EvidenceBudget()
    file_format = snapshot["source"]["format"]
    if isinstance(locator, PublishedClause):
        if file_format != ".json" or snapshot.get("kind") != "published_rule":
            raise ValueError("Published clause locator requires a published rule snapshot")
        matches = [row for row in snapshot.get("clauses", []) if row.get("id") == locator.clause_id]
        if len(matches) != 1:
            raise ValueError("Published clause missing or duplicated")
        budget.consume(1)
        return Observation(f"published_clause:{locator.clause_id}", (matches[0]["rule_to_check"],))
    if isinstance(locator, (ExcelCell, ExcelRange)):
        if file_format not in {".xlsx", ".xlsm", ".xls"}:
            raise ValueError("Evidence locator does not match document format")
        table = _table(snapshot, locator.sheet)
        start = locator.cell if isinstance(locator, ExcelCell) else locator.start
        end = locator.cell if isinstance(locator, ExcelCell) else locator.end
        r1, c1 = _address(start)
        r2, c2 = _address(end)
        if r2 < r1 or c2 < c1 or (r2 - r1 + 1) * (c2 - c1 + 1) > 4096:
            raise ValueError("Evidence range reversed or too large")
        budget.consume((r2 - r1 + 1) * (c2 - c1 + 1) * (len(table.get("merges", [])) + 1))
        cells = [_cell(table, row, column) for row in range(r1, r2 + 1) for column in range(c1, c2 + 1)]
        address = start if start == end else f"{start}:{end}"
        return Observation(f"{locator.sheet}!{address}", tuple(cell[0] for cell in cells), all(cell[1] for cell in cells))
    if isinstance(locator, WordParagraph):
        if file_format != ".docx" or locator.paragraph > len(snapshot.get("paragraphs", [])):
            raise ValueError("Evidence paragraph unavailable")
        budget.consume(1)
        paragraph = snapshot["paragraphs"][locator.paragraph - 1]
        if paragraph["location"] != f"paragraph:{locator.paragraph}":
            raise ValueError("Evidence paragraph index mismatch")
        return Observation(f"paragraph:{locator.paragraph}", (str(paragraph["text"]),))
    if isinstance(locator, WordCell):
        if file_format != ".docx":
            raise ValueError("Evidence locator does not match document format")
        table = _table(snapshot, f"table:{locator.table}")
        budget.consume(len(table.get("merges", [])) + 1)
        text, known = _cell(table, locator.row, locator.column)
        return Observation(f"table:{locator.table}!{ref(locator.row, locator.column)}", (text,), known)
    if isinstance(locator, PdfBlock):
        pages = snapshot.get("pages", [])
        if file_format != ".pdf" or locator.page > len(pages):
            raise ValueError("Evidence page unavailable")
        budget.consume(1)
        page = pages[locator.page - 1]
        if page["page"] != locator.page or locator.block > len(page["blocks"]):
            raise ValueError("Evidence block unavailable")
        return Observation(f"page:{locator.page}#block:{locator.block}", (str(page["blocks"][locator.block - 1]["text"]),))
    raise ValueError("Unsupported evidence locator")
