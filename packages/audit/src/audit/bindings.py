"""Physical source bindings and reviewed row scopes.

A binding never removes a physical row. Only a reviewed scope assigns row roles.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from .document_tools import TableReader, col_number
from .evidence import EvidenceBudget, cell_locator, read_cell, resolve
from .models import Artifact, ExcelCell, PdfCell, WordCell

TableRowLocator = Annotated[ExcelCell | WordCell | PdfCell, Field(discriminator="kind")]
ScopeReason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]


class ExcludedRow(Artifact):
    row: int = Field(ge=1, le=100000, strict=True)
    reason_type: Literal["repeated_header", "footer", "revision_history", "non_record", "other"]
    evidence_locator: TableRowLocator
    reason: ScopeReason


class UnresolvedRow(Artifact):
    row: int = Field(ge=1, le=100000, strict=True)
    evidence_locator: TableRowLocator
    reason: ScopeReason


class FieldHole(Artifact):
    state: Literal["absent", "unresolved"]
    reason: ScopeReason


class Binding(Artifact):
    id: str = Field(min_length=1)
    document_id: str
    source_sha256: str
    table: str
    header_rows: list[int] = Field(max_length=32)
    first_row: int = Field(ge=1, le=100000, strict=True)
    last_row: int = Field(ge=1, le=100000, strict=True)
    columns: dict[str, str] = Field(max_length=64)
    field_holes: dict[str, FieldHole] = Field(default_factory=dict, max_length=64)
    mapping_basis: ScopeReason
    boundary_basis: ScopeReason

    @model_validator(mode="after")
    def distinct_fields(self) -> Binding:
        fields = set(self.columns) | set(self.field_holes)
        if len(fields) > 64 or any(not field.strip() for field in fields):
            raise ValueError("Binding needs bounded named fields")
        if set(self.columns) & set(self.field_holes):
            raise ValueError("A binding field has exactly one state")
        return self


class BindingScope(Artifact):
    binding_id: str = Field(min_length=1)
    excluded_rows: list[ExcludedRow] = Field(default_factory=list, max_length=4096)
    unresolved_rows: list[UnresolvedRow] = Field(default_factory=list, max_length=4096)


def row_claims(items: list[ExcludedRow] | list[UnresolvedRow], binding: Binding, table: dict[str, Any], document: dict[str, Any], budget: EvidenceBudget) -> set[int]:
    rows: set[int] = set()
    file_format = document["source"]["format"]
    reader: TableReader | None = None
    for item in items:
        if item.row in rows:
            raise ValueError("Duplicate row scope claim")
        if not binding.first_row <= item.row <= binding.last_row:
            raise ValueError("Invalid row scope claim")
        locator = item.evidence_locator
        if isinstance(locator, WordCell):
            valid = file_format == ".docx" and binding.table == f"table:{locator.table}"
            row, column = locator.row, locator.column
        elif isinstance(locator, PdfCell):
            valid = file_format == ".pdf" and binding.table == f"page-{locator.page}"
            row, column = locator.row, locator.column
        else:
            valid = file_format in {".xlsx", ".xlsm", ".xls"} and locator.sheet == binding.table
            row, column = int(re.sub("[A-Z]", "", locator.cell)), col_number(re.sub("[0-9]", "", locator.cell))
        if not valid or row != item.row or not 1 <= column <= table["cols"]:
            raise ValueError("Row scope evidence does not identify the claimed record")
        if isinstance(item, ExcludedRow):
            if file_format == ".pdf":
                known = resolve(document, locator, budget).known
            else:
                if reader is None:
                    reader = TableReader(table, budget)
                known = read_cell(table, row, column, reader=reader)["state"] == "known"
            if not known:
                raise ValueError("Confirmed excluded rows require known source evidence; use unresolved_rows")
        rows.add(item.row)
    return rows


def binding_scope(binding: Binding, document: dict[str, Any], budget: EvidenceBudget) -> dict[str, Any]:
    """Validate frozen physical bounds; semantic mapping remains unverified."""
    if document["source"]["sha256"] != binding.source_sha256:
        raise ValueError("Binding source version mismatch")
    matches = [table for table in document.get("tables", []) if table["id"] == binding.table]
    if len(matches) != 1 or not 1 <= binding.first_row <= binding.last_row <= matches[0]["rows"]:
        raise ValueError("Binding outside observed table")
    table = matches[0]
    file_format = document["source"]["format"]
    if file_format == ".pdf":
        page, pages = table.get("page"), document.get("pages", [])
        if type(page) is not int or binding.table != f"page-{page}" or not 1 <= page <= len(pages) or pages[page - 1].get("page") != page:
            raise ValueError("Binding PDF table page mismatch")
    if not binding.header_rows and (file_format != ".pdf" or table["page"] == 1):
        raise ValueError("Only a PDF continuation page may omit header rows")
    columns = {field: col_number(column) for field, column in binding.columns.items()}
    if any(not 1 <= column <= table["cols"] for column in columns.values()):
        raise ValueError("Binding field outside observed table")
    if len(set(binding.header_rows)) != len(binding.header_rows) or any(not 1 <= row <= table["rows"] or binding.first_row <= row <= binding.last_row for row in binding.header_rows):
        raise ValueError("Header rows overlap the body or exceed the table")
    if any(hole.state == "absent" for hole in binding.field_holes.values()):
        if not binding.header_rows:
            raise ValueError("Absent fields require searched header rows")
        reader = TableReader(table, budget)
        for row in binding.header_rows:
            for column in range(1, table["cols"] + 1):
                if reader.cell(row, column)["state"] != "known":
                    raise ValueError("Absent fields require readable searched headers")
                if file_format == ".pdf":
                    resolve(document, cell_locator(file_format, binding.table, row, column), budget)
    return table
