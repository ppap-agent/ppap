"""Resolve typed locations to observed content; never search snapshot metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .document_tools import TableReader, col_number, effective, ref
from .models import ExcelCell, ExcelRange, Locator, PdfBlock, PdfCell, PublishedClause, WordCell, WordParagraph
from .pdf_tables import valid_pdf_bbox


class EvidenceLimitError(ValueError):
    """Host work limit; changing a model's evidence claims cannot repair it."""


@dataclass
class EvidenceBudget:
    remaining: int = 1_000_000

    def consume(self, steps: int) -> None:
        self.remaining -= steps
        if self.remaining < 0:
            raise EvidenceLimitError("Evidence resolution budget exceeded")


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


def cell_locator(source_format: str, table: str, row: int, column: int) -> ExcelCell | WordCell | PdfCell:
    """Use the same cell address for query, row claims and local checks."""
    if source_format in {".xlsx", ".xlsm", ".xls"}:
        return ExcelCell(kind="xlsx_cell", sheet=table, cell=ref(row, column))
    if source_format == ".docx" and re.fullmatch(r"table:[1-9][0-9]*", table):
        return WordCell(kind="docx_cell", table=int(table.removeprefix("table:")), row=row, column=column)
    if source_format == ".pdf" and re.fullmatch(r"page-[1-9][0-9]*", table):
        return PdfCell(kind="pdf_cell", page=int(table.removeprefix("page-")), row=row, column=column)
    raise ValueError("Cell locator requires a supported source table")


def read_cell(table: dict[str, Any], row: int, column: int, *, reader: TableReader | None = None) -> dict[str, Any]:
    if not 1 <= row <= table["rows"] or not 1 <= column <= table["cols"]:
        raise ValueError("Evidence cell outside observed bounds")
    if reader is not None and reader.table is not table:
        raise ValueError("Cell reader belongs to a different source table")
    item = reader.cell(row, column) if reader is not None else effective(table, row, column)
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
    return {**item, "anchor": anchor}


def _cell(table: dict[str, Any], row: int, column: int) -> tuple[str, bool]:
    item = read_cell(table, row, column)
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
    if isinstance(locator, PdfCell):
        pages = snapshot.get("pages", [])
        if file_format != ".pdf" or locator.page > len(pages):
            raise ValueError("Evidence PDF page unavailable")
        page = pages[locator.page - 1]
        table = _table(snapshot, f"page-{locator.page}")
        if type(page.get("page")) is not int or page["page"] != locator.page or type(table.get("page")) is not int or table["page"] != locator.page:
            raise ValueError("Evidence PDF table page mismatch")
        if table.get("coordinate_system") != "pdf_points_top_left" or page.get("rotation") != 0 or page.get("crop_origin") != [0.0, 0.0]:
            raise ValueError("Evidence PDF grid uses an unsupported page transform")
        budget.consume(len(table.get("merges", [])) + 1)
        item = read_cell(table, locator.row, locator.column)
        spans = [span for span in table.get("merges", []) if span[0] <= locator.row <= span[2] and span[1] <= locator.column <= span[3]]
        expected_anchor = ref(spans[0][0], spans[0][1]) if spans else ref(locator.row, locator.column)
        if len(spans) > 1 or item["anchor"] != expected_anchor or expected_anchor not in table["cells"]:
            raise ValueError("Invalid PDF cell anchor")
        source = item.get("source")
        bbox = source.get("bbox") if isinstance(source, dict) else None
        if not isinstance(source, dict) or type(source.get("page")) is not int or source.get("page") != locator.page or not valid_pdf_bbox(bbox, page.get("width"), page.get("height")):
            raise ValueError("Evidence PDF cell geometry is invalid or outside the page")
        value = item.get("value")
        return Observation(f"page:{locator.page}!{ref(locator.row, locator.column)}", ("" if value is None else str(value),), item.get("state") == "known")
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


def resolve_optional(snapshot: dict[str, Any], locator: Locator | None, budget: EvidenceBudget) -> Observation | None:
    """Unusable optional citations are gaps, not changes to a business verdict.

    Callers must first check the authorized document identity. Required rule
    sources and query execution continue to use strict validation.
    """
    if locator is None or budget.remaining < 0:
        return None
    try:
        observed = resolve(snapshot, locator, budget)
    except ValueError:
        return None
    return observed if observed.known else None
