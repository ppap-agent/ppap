"""Physical source bindings and use-specific collection membership.

A binding never removes a physical row. Only a collection use assigns row roles.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from .document_tools import TableReader, col_number
from .evidence import EvidenceBudget, cell_locator, read_cell, resolve
from .models import Artifact, Citation, Diagnostic, ExcelCell, PdfCell, WordCell
from .values import scalar_key

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


class InterpretationCandidate(Artifact):
    id: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=1024)
    explanation: str = Field(min_length=1, max_length=4096)


class InterpretationRequirement(Artifact):
    id: str = Field(min_length=1, max_length=256)
    field: str = Field(min_length=1, max_length=256)
    question: str = Field(min_length=1, max_length=4096)
    candidates: list[InterpretationCandidate] = Field(min_length=1, max_length=8)
    evidence: list[Citation] = Field(min_length=1, max_length=16)
    requires_confirmation: bool = True

    @model_validator(mode="after")
    def distinct_candidates(self) -> "InterpretationRequirement":
        if len({candidate.id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("Duplicate interpretation candidate")
        return self


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
    interpretations: list[InterpretationRequirement] = Field(default_factory=list, max_length=32)
    boundary_basis: ScopeReason

    @model_validator(mode="after")
    def distinct_fields(self) -> Binding:
        fields = set(self.columns) | set(self.field_holes)
        if len(fields) > 64 or any(not field.strip() for field in fields):
            raise ValueError("Binding needs bounded named fields")
        if set(self.columns) & set(self.field_holes):
            raise ValueError("A binding field has exactly one state")
        return self


class LogicalObject(Artifact):
    id: str = Field(min_length=1, max_length=256)
    rows: list[Annotated[int, Field(strict=True, ge=1, le=100000)]] = Field(min_length=1, max_length=4096)
    # A claimed business grouping keeps its public explanation and member sources.
    basis: ScopeReason | None = None
    evidence: list[TableRowLocator] = Field(default_factory=list, max_length=4096)
    # Explicit source fields allow more than one business object in a row.
    fields: dict[str, list[TableRowLocator]] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def sourced_fields(self) -> "LogicalObject":
        if any(not field.strip() or not 1 <= len(locators) <= 64 for field, locators in self.fields.items()):
            raise ValueError("Logical attributes require bounded nonempty source locations")
        return self


class ObjectChoice(Artifact):
    id: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=1024)
    objects: list[LogicalObject] = Field(min_length=1, max_length=100000)


class BindingScope(Artifact):
    binding_id: str = Field(min_length=1)
    objects: list[LogicalObject] = Field(default_factory=list, max_length=100000)
    object_choices: list[ObjectChoice] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def one_object_representation(self) -> "BindingScope":
        if self.objects and self.object_choices or len({choice.id for choice in self.object_choices}) != len(self.object_choices):
            raise ValueError("Choose direct logical objects or distinct unresolved object candidates")
        return self

    excluded_rows: list[ExcludedRow] = Field(default_factory=list, max_length=4096)
    unresolved_rows: list[UnresolvedRow] = Field(default_factory=list, max_length=4096)


class BoundCollection(Artifact):
    kind: Literal["bound"]
    scopes: list[BindingScope] = Field(min_length=1, max_length=256)
    unresolved_scopes: list[ScopeReason] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def distinct_reasons(self) -> BoundCollection:
        if len(self.unresolved_scopes) != len(set(self.unresolved_scopes)):
            raise ValueError("Duplicate unresolved scope reason")
        return self


class AbsentCollection(Artifact):
    kind: Literal["absent"]
    searched_documents: list[str] = Field(min_length=1, max_length=256)
    reason: ScopeReason


class UnresolvedCollection(Artifact):
    kind: Literal["unresolved"]
    reason: ScopeReason
    diagnostic: Diagnostic = "unresolved_relation"


class FileCollection(Artifact):
    kind: Literal["files"]
    document_ids: list[str] = Field(min_length=1, max_length=64)


CollectionUse = Annotated[BoundCollection | AbsentCollection | UnresolvedCollection | FileCollection, Field(discriminator="kind")]


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


def grouping_support(item: LogicalObject, binding: Binding, document: dict[str, Any], budget: EvidenceBudget) -> dict[str, Any]:
    """Validate source support, without claiming to prove a natural-language grouping."""
    covered: set[int] = set()
    citations = []
    readable = True
    member_rows = set(item.rows)
    allowed_rows = member_rows | set(binding.header_rows)
    for locator in item.evidence:
        if isinstance(locator, ExcelCell):
            valid = locator.sheet == binding.table
            row = int(re.sub("[A-Z]", "", locator.cell))
        elif isinstance(locator, WordCell):
            valid = binding.table == f"table:{locator.table}"
            row = locator.row
        else:
            valid = binding.table == f"page-{locator.page}"
            row = locator.row
        if not valid or row not in allowed_rows:
            raise ValueError("Grouping evidence outside claimed object sources")
        observed = resolve(document, locator, budget)
        readable = readable and observed.known
        if row in member_rows:
            covered.add(row)
        citations.append({"document_id": binding.document_id, "locator": locator.model_dump(mode="json")})
    supported = bool(item.basis) and readable and covered == member_rows
    return {"origin": "model", "basis": item.basis, "evidence": citations, "state": "supported" if supported else "unresolved"}


def scoped_records(materialized: dict[str, Any], binding: Binding, scope: BindingScope, document: dict[str, Any], budget: EvidenceBudget) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    table = next(table for table in document["tables"] if table["id"] == binding.table)
    excluded = row_claims(scope.excluded_rows, binding, table, document, budget)
    unresolved = row_claims(scope.unresolved_rows, binding, table, document, budget)
    if excluded & unresolved:
        raise ValueError("A row cannot be both excluded and unresolved")
    records = [{**row, "binding_id": binding.id, "scope_state": "unresolved" if row["row_id"] in unresolved else "known"} for row in materialized["records"] if row["row_id"] not in excluded]
    for record in records:
        record["scope_evidence"] = [{"document_id": binding.document_id, "locator": claim.evidence_locator.model_dump(mode="json")} for claim in scope.unresolved_rows if claim.row == record["row_id"]]
    exclusions = [{"binding_id": binding.id, **item.model_dump(mode="json")} for item in scope.excluded_rows]
    if scope.objects:
        if len({item.id for item in scope.objects}) != len(scope.objects):
            raise ValueError("Duplicate logical object identity")
        indexed = {item["row_id"]: item for item in records}
        covered = {row for item in scope.objects for row in item.rows}
        if covered != indexed.keys() or any(len(set(item.rows)) != len(item.rows) for item in scope.objects):
            raise ValueError("Logical objects must cover exactly the declared physical records")
        # Explicit fields can partition one row into several objects. A source
        # already assigned to another object stays there; an unassigned native
        # member observation cannot disappear through selective field locators.
        owners: dict[tuple, set[str]] = defaultdict(set)
        for item in scope.objects:
            for field in set(binding.columns) | set(binding.field_holes) | set(item.fields):
                if field in item.fields:
                    locations = [locator.model_dump(mode="json") for locator in item.fields[field]]
                else:
                    locations = [indexed[row]["evidence"][field]["citation"]["locator"] for row in item.rows if field in indexed[row]["evidence"]]
                for source_location in locations:
                    owners[field, tuple(sorted(source_location.items()))].add(item.id)
        logical = []
        for item in scope.objects:
            budget.consume(len(item.rows))
            members = [indexed[row] for row in item.rows]
            grouping = grouping_support(item, binding, document, budget)
            if not binding.columns:
                # A pure point mapping cannot claim extra member rows while
                # selecting attributes only from the favorable first row.
                contributed = set()
                for point_locations in item.fields.values():
                    for location in point_locations:
                        row_number = int(re.sub("[A-Z]", "", location.cell)) if isinstance(location, ExcelCell) else location.row
                        contributed.add(row_number)
                if not set(item.rows) <= contributed:
                    grouping = {**grouping, "state": "unresolved"}
            values, states, evidence, observations = {}, {}, {}, {}
            for field in sorted(set(binding.columns) | set(binding.field_holes) | set(item.fields)):
                seen = []
                if field in item.fields and field in binding.field_holes and binding.field_holes[field].state == "absent":
                    raise ValueError("An absent field cannot also have an explicit object source")
                for row in members:
                    # A mapping hole is not an unreadable native value. An
                    # explicit source can resolve it without erasing history.
                    if field in item.fields and field in binding.field_holes and not row["evidence"].get(field):
                        continue
                    if field not in row["states"] and field not in row["values"]:
                        continue
                    source = row["evidence"].get(field)
                    assigned = owners.get((field, tuple(sorted(source["citation"]["locator"].items()))), set()) if source else set()
                    if assigned and item.id not in assigned:
                        continue
                    seen.append({"value": row["values"].get(field), "state": row["states"].get(field, "unresolved"), "evidence": source})
                if field in item.fields:
                    for locator in item.fields[field]:
                        # Explicit object fields stay inside the frozen binding rows/table.
                        if isinstance(locator, ExcelCell):
                            valid = locator.sheet == binding.table
                            source_row = int(re.sub("[A-Z]", "", locator.cell))
                            column = col_number(re.sub("[0-9]", "", locator.cell))
                        elif isinstance(locator, WordCell):
                            valid = binding.table == f"table:{locator.table}"
                            source_row, column = locator.row, locator.column
                        else:
                            valid = binding.table == f"page-{locator.page}"
                            source_row, column = locator.row, locator.column
                        if not valid or source_row not in item.rows:
                            raise ValueError("Logical attribute source outside object")
                        result = resolve(document, locator, budget)
                        seen.append(
                            {
                                "value": read_cell(table, source_row, column)["value"],
                                "state": "known" if result.known else "unresolved",
                                "evidence": {"citation": {"document_id": binding.document_id, "locator": locator.model_dump(mode="json")}},
                            }
                        )
                # A repeated citation is one observation; distinct occurrences
                # remain distinct even when their values happen to be equal.
                unique: dict[tuple, dict[str, Any]] = {}
                for index, observation in enumerate(seen):
                    source = observation["evidence"]
                    key = tuple(sorted(source["citation"]["locator"].items())) if source else (index,)
                    unique.setdefault(key, observation)
                seen = list(unique.values())
                budget.consume(len(seen))
                observations[field] = seen
                nonblank = [value for value in seen if value["state"] == "known" and value["value"] not in (None, "")]
                distinct = {scalar_key(value["value"]) for value in nonblank}
                chosen = (nonblank or seen)[0] if seen else {"value": None, "evidence": None}
                values[field] = chosen["value"]
                states[field] = "unknown_conflicting_values" if len(distinct) > 1 else "unresolved" if any(value["state"] != "known" for value in seen) else "known"
                if chosen["evidence"]:
                    evidence[field] = chosen["evidence"]
            logical.append(
                {
                    "binding_id": binding.id,
                    "row_id": "object:" + item.id,
                    "values": values,
                    "states": states,
                    "evidence": evidence,
                    "source_rows": item.rows,
                    "grouping": grouping,
                    "scope_evidence": [*grouping["evidence"], *[source for member in members for source in member.get("scope_evidence", [])]],
                    "observations": observations,
                    "scope_state": "known" if grouping["state"] == "supported" and all(row["scope_state"] == "known" for row in members) else "unresolved",
                }
            )
        records = logical
    return records, exclusions
