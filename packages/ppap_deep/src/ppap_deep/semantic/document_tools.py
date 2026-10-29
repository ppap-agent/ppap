"""Portable document snapshots, explicit table bindings, and read-only SQL evidence.

This module does not decide audit status or select customer templates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import zipfile
from bisect import bisect_right
from contextlib import ExitStack, closing
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .evidence import EvidenceBudget


@dataclass(frozen=True)
class ReadLimits:
    """Parser work budgets, not a replacement for guest resource isolation."""

    source_bytes: int = 64 * 1024 * 1024
    archive_entries: int = 4096
    expanded_bytes: int = 128 * 1024 * 1024
    tables: int = 256
    cells: int = 1_000_000
    merges: int = 4096
    paragraphs: int = 100_000
    pages: int = 1000
    blocks: int = 100_000
    output_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError("Read limits must be positive integers")


class ParserLimitError(ValueError):
    """A deterministic reader budget was exceeded."""


def within(value: int, limit: int, label: str) -> None:
    if value > limit:
        raise ParserLimitError(f"Document read limit exceeded: {label}")


def preflight(path: Path, limits: ReadLimits) -> None:
    within(path.stat().st_size, limits.source_bytes, "source bytes")
    if path.suffix.lower() not in (".xlsx", ".xlsm", ".docx"):
        return
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        within(len(entries), limits.archive_entries, "archive entries")
        within(sum(entry.file_size for entry in entries), limits.expanded_bytes, "expanded bytes")
        if len({entry.filename for entry in entries}) != len(entries):
            raise ValueError("Duplicate archive entry")
        # Read bounded chunks to validate the actual expanded length and CRC before
        # passing the container to a library that may materialize entire XML trees.
        expanded = 0
        for entry in entries:
            with archive.open(entry) as stream:
                while chunk := stream.read(64 * 1024):
                    expanded += len(chunk)
                    within(expanded, limits.expanded_bytes, "expanded bytes")


def save(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def digest(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def col_name(number: int) -> str:
    text = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        text = chr(65 + remainder) + text
    return text


def col_number(text: str | int) -> int:
    if isinstance(text, int) and text > 0:
        return text
    if not re.fullmatch("[A-Z]+", str(text)):
        raise ValueError(f"Invalid column: {text}")
    number = 0
    for letter in str(text):
        number = number * 26 + ord(letter) - 64
    return number


def ref(row: int, column: int) -> str:
    return f"{col_name(column)}{row}"


def scalar(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime, time)) else value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate published rule JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError("Non-finite published rule JSON number")


def active_text(element: Any, *, skip_tables: bool = False) -> str:
    """Read direct OOXML deletions/strikes; inherited style review remains explicit."""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def visit(node: Any) -> str:
        if skip_tables and node.tag == ns + "tbl":
            return ""
        if node.tag == ns + "del":
            return ""
        if node.tag == ns + "r":
            for name in ("strike", "dstrike"):
                mark = node.find(f"{ns}rPr/{ns}{name}")
                if mark is not None and mark.get(ns + "val", "true") not in ("0", "false", "off"):
                    return ""
        if node.tag == ns + "t":
            return str(node.text or "")
        if node.tag in (ns + "br", ns + "cr", ns + "tab"):
            return "\n" if node.tag != ns + "tab" else "\t"
        return "".join(visit(child) for child in node) + ("\n" if node.tag == ns + "p" else "")

    return visit(element).strip()


def snapshot(path: str | Path, *, limits: ReadLimits | None = None) -> dict[str, Any]:
    path = Path(path)
    limits = limits or ReadLimits()
    preflight(path, limits)
    visited_cells = 0
    suffix = path.suffix.lower()
    result: dict[str, Any] = {"source": {"file": path.name, "sha256": digest(path), "format": suffix}, "reader_parameters": dict(vars(limits)), "tables": [], "limitations": []}
    if suffix == ".json":
        try:
            published = json.loads(path.read_bytes(), object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
        except (UnicodeError, ValueError) as exc:
            raise ValueError("Invalid published rule JSON") from exc
        if not isinstance(published, dict) or published.get("kind") != "published_rule":
            raise ValueError("Expected a published rule source")
        body = published.get("rule_body")
        clauses = published.get("clauses")
        version = published.get("rule_version_id")
        if (
            not isinstance(version, str)
            or not version.strip()
            or not isinstance(body, dict)
            or not isinstance(body.get("rules"), list)
            or not isinstance(clauses, list)
            or not 1 <= len(clauses) <= 256
            or clauses != body["rules"]
        ):
            raise ValueError("Published rule body and clauses differ")
        ids: set[str] = set()
        for clause in clauses:
            if not isinstance(clause, dict):
                raise ValueError("Invalid published rule clause")
            clause_id, text = clause.get("id"), clause.get("rule_to_check")
            if not isinstance(clause_id, str) or not clause_id.strip() or clause_id in ids or not isinstance(text, str) or not text.strip():
                raise ValueError("Invalid published rule clause")
            ids.add(clause_id)
        result.update(kind="published_rule", rule_version_id=version, rule_body=body, clauses=clauses)
        if "origin" in published:
            origin = published["origin"]
            if not isinstance(origin, dict) or not isinstance(origin.get("file_version_id"), str) or not origin["file_version_id"].strip():
                raise ValueError("Invalid published rule provenance")
            result["origin"] = origin
    elif suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula

        with ExitStack() as stack:
            values = stack.enter_context(closing(load_workbook(path, data_only=True)))
            formulas = stack.enter_context(closing(load_workbook(path, data_only=False)))
            within(len(values.worksheets), limits.tables, "tables")
            for sheet in values.worksheets:
                visited_cells += sheet.max_row * sheet.max_column
                within(visited_cells, limits.cells, "cell visits")
                within(len(sheet.merged_cells.ranges), limits.merges, "merges")
                table: dict[str, Any] = {"id": sheet.title, "rows": sheet.max_row, "cols": sheet.max_column, "cells": {}, "merges": [], "hidden_rows": [], "hidden_columns": []}
                for row in formulas[sheet.title]:
                    for cell in row:
                        if cell.value is None:
                            continue
                        value = scalar(sheet[cell.coordinate].value)
                        item: dict[str, Any] = {"value": value, "state": "known"}
                        if cell.data_type == "f":
                            formula = cell.value
                            if isinstance(formula, (ArrayFormula, DataTableFormula)):
                                # These are formula descriptors, not scalar cell
                                # values. Match openpyxl's native OOXML writer.
                                item["formula"] = formula.text if isinstance(formula, ArrayFormula) else None
                                item["formula_attributes"] = dict(formula)
                            else:
                                item["formula"] = formula
                            if value is None:
                                item["state"] = "unknown_formula_cache"
                        if cell.data_type == "e":
                            item["state"] = "unknown_excel_error"
                        table["cells"][cell.coordinate] = item
                table["merges"] = [[m.min_row, m.min_col, m.max_row, m.max_col] for m in sheet.merged_cells.ranges]
                table["hidden_rows"] = [r for r, dim in sheet.row_dimensions.items() if dim.hidden]
                table["hidden_columns"] = [c for c, dim in sheet.column_dimensions.items() if dim.hidden]
                result["tables"].append(table)
        result["limitations"] = ["Images, drawings and checkbox objects require separate inspection; formula caches are not recalculated."]
    elif suffix == ".xls":
        import xlrd

        book = xlrd.open_workbook(str(path), formatting_info=True)
        within(book.nsheets, limits.tables, "tables")
        for xls_sheet in book.sheets():
            visited_cells += xls_sheet.nrows * xls_sheet.ncols
            within(visited_cells, limits.cells, "cell visits")
            within(len(xls_sheet.merged_cells), limits.merges, "merges")
            table = {"id": xls_sheet.name, "rows": xls_sheet.nrows, "cols": xls_sheet.ncols, "cells": {}, "merges": [[r1 + 1, c1 + 1, r2, c2] for r1, r2, c1, c2 in xls_sheet.merged_cells]}
            for xls_row in range(xls_sheet.nrows):
                for column in range(xls_sheet.ncols):
                    xls_cell = xls_sheet.cell(xls_row, column)
                    if xls_cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        continue
                    value = xls_cell.value
                    if xls_cell.ctype == xlrd.XL_CELL_DATE:
                        value = xlrd.xldate_as_datetime(value, book.datemode).isoformat()
                    elif xls_cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        value = bool(value)
                    table["cells"][ref(xls_row + 1, column + 1)] = {"value": value, "state": "unknown_excel_error" if xls_cell.ctype == xlrd.XL_CELL_ERROR else "known", "source_type": xls_cell.ctype}
            result["tables"].append(table)
        result["limitations"] = ["XLS exposes cached values; formula freshness and embedded objects are not verified."]
    elif suffix == ".docx":
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table

        document = Document(str(path))
        for paragraph_count, _ in enumerate(document.element.body.iter(qn("w:p")), 1):
            within(paragraph_count, limits.paragraphs, "paragraphs")
        result["paragraphs"] = [{"location": f"paragraph:{i + 1}", "text": active_text(p._p)} for i, p in enumerate(document.paragraphs)]
        cell_locations: dict[Any, dict[str, str]] = {}
        # Physical XML order also numbers tables in rule_sources. A nested
        # table is a distinct source, not text repeated in its enclosing cell.
        for i, table_element in enumerate(document.element.body.iter(qn("w:tbl")), 1):
            within(i, limits.tables, "tables")
            word_table = Table(table_element, document._body)
            visited_cells += len(word_table.rows) * len(word_table.columns)
            within(visited_cells, limits.cells, "cell visits")
            # Keep physical indices and budgets, but do not revive deleted
            # tables by reading cells without their enclosing revision markup.
            if any(ancestor.tag == qn("w:del") for ancestor in table_element.iterancestors()):
                continue
            data: dict[str, Any] = {"id": f"table:{i}", "rows": len(word_table.rows), "cols": 0, "cells": {}, "merges": []}
            parent = table_element.getparent()
            while parent is not None and parent.tag != qn("w:tc"):
                parent = parent.getparent()
            if parent is not None:
                if parent not in cell_locations:
                    raise ValueError("Nested table is inside an unobserved Word cell")
                data["parent_cell"] = cell_locations[parent]
            anchors: dict[Any, str] = {}
            for r, word_row in enumerate(word_table.rows, 1):
                data["cols"] = max(data["cols"], len(word_row.cells))
                for c, word_cell in enumerate(word_row.cells, 1):
                    address = ref(r, c)
                    anchor = anchors.setdefault(word_cell._tc, address)
                    value = active_text(word_cell._tc, skip_tables=True) if anchor == address else data["cells"][anchor]["value"]
                    data["cells"][address] = {"value": value, "state": "known", "anchor": anchor}
                    cell_locations.setdefault(word_cell._tc, {"table": data["id"], "cell": anchor})
            result["tables"].append(data)
        result["limitations"] = ["Direct strike/deletion markup excluded; inherited styles, drawings and document headers/footers need separate inspection when relevant."]
    elif suffix == ".pdf":
        from ppap_deep.semantic.pdf_tables import snapshot_pdf

        raw = path.read_bytes()
        within(len(raw), limits.source_bytes, "source bytes")
        if hashlib.sha256(raw).hexdigest() != result["source"]["sha256"]:
            raise ValueError("PDF source changed during snapshot")
        result.update(snapshot_pdf(raw, limits))
    else:
        raise ValueError(f"Unsupported input format: {suffix}")
    output_size = 0
    for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(result):
        output_size += len(chunk.encode("utf-8"))
        within(output_size, limits.output_bytes, "snapshot bytes")
    return result


def _effective(table: dict[str, Any], row: int, column: int, merges: list) -> dict[str, Any]:
    address = ref(row, column)
    anchor = address
    span = None
    for r1, c1, r2, c2 in merges:
        if r1 <= row <= r2 and c1 <= column <= c2:
            anchor, span = ref(r1, c1), f"{ref(r1, c1)}:{ref(r2, c2)}"
            break
    item = table["cells"].get(anchor, {"value": None, "state": "known"})
    anchor = item.get("anchor", anchor)
    return {**item, "cell": address, "anchor": anchor, "merge": span}


def effective(table: dict[str, Any], row: int, column: int) -> dict[str, Any]:
    return _effective(table, row, column, table.get("merges", []))


class TableReader:
    """Index layout once per reader; retain the original first-match semantics.

    The index holds merge references between row boundaries, not copied cells
    or one entry per covered row. Index construction remains budgeted.
    The frozen snapshot is never changed.
    """

    def __init__(self, table: dict[str, Any], budget: EvidenceBudget) -> None:
        self.table, self.budget = table, budget
        self.rows: list[int] = []
        self.merges: list[list] = []
        events: dict[int, list[tuple[int, list | None]]] = {}
        budget.consume(2 * len(table.get("merges", [])))
        for index, span in enumerate(table.get("merges", [])):
            first, last = max(1, span[0]), min(table["rows"], span[2])
            if first <= last:
                events.setdefault(first, []).append((index, span))
                events.setdefault(last + 1, []).append((index, None))
        active: dict[int, list] = {}
        for row in sorted(events):
            for index, span in events[row]:
                if span is None:
                    active.pop(index)
                else:
                    active[index] = span
            budget.consume(len(active) * (len(active).bit_length() + 1) + 1)
            self.rows.append(row)
            self.merges.append([active[index] for index in sorted(active)])

    def cell(self, row: int, column: int) -> dict[str, Any]:
        if not 1 <= row <= self.table["rows"] or not 1 <= column <= self.table["cols"]:
            raise ValueError("Cell outside observed table")
        index = bisect_right(self.rows, row) - 1
        spans = self.merges[index] if index >= 0 else []
        self.budget.consume(len(spans) + 1)
        return _effective(self.table, row, column, spans)


def sequence(text: str) -> list[int]:
    values: list[int] = []
    for part in text.split(","):
        start, *end = part.split(":")
        first, last = int(start), int(end[0]) if end else int(start)
        if first < 1 or last < first:
            raise ValueError("Rows must be positive and increasing")
        values.extend(range(first, last + 1))
    return list(dict.fromkeys(values))


def bind(data: dict[str, Any], config: dict[str, Any], *, budget: EvidenceBudget | None = None) -> dict[str, Any]:
    """Materialize the same Binding used by AuditTrace, including unknowns.

    Imports are local because trace/evidence also use this module's cell readers.
    No second SQL binding schema or row-selection policy is introduced here.
    """
    from ppap_deep.semantic.bindings import Binding, binding_scope
    from ppap_deep.semantic.evidence import EvidenceBudget, cell_locator, resolve

    binding = Binding.model_validate(config)
    budget = budget if budget is not None else EvidenceBudget()
    table = binding_scope(binding, data, budget)
    reader = TableReader(table, budget)
    rows = range(binding.first_row, binding.last_row + 1)
    columns = {name: col_number(column) for name, column in binding.columns.items()}
    header_paths = {}
    for name, column in columns.items():
        parts, seen = [], set()
        for row in binding.header_rows:
            item = reader.cell(row, column)
            if item["anchor"] not in seen and item["value"] is not None:
                parts.append(item)
                seen.add(item["anchor"])
        header_paths[name] = parts
    records: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for row in rows:
        budget.consume(1)
        record: dict[str, Any] = {"row_id": row, "scope_state": "known", "values": {}, "states": {}, "evidence": {}}
        for name, column in columns.items():
            item = reader.cell(row, column)
            locator = cell_locator(data["source"]["format"], binding.table, row, column)
            if data["source"]["format"] == ".pdf" and item["state"] == "known":
                resolve(data, locator, budget)
            record["values"][name] = item["value"]
            record["states"][name] = item["state"]
            record["evidence"][name] = {**item, "citation": {"document_id": binding.document_id, "locator": locator.model_dump(mode="json")}}
            if item["state"] != "known":
                unknown.append({"row_id": row, "field": name, "state": item["state"]})
        for name, hole in binding.field_holes.items():
            record["values"][name] = None
            record["states"][name] = hole.state
            unknown.append({"row_id": row, "field": name, "state": hole.state})
        records.append(record)
    return {
        "source": data["source"],
        "binding": binding.model_dump(mode="json"),
        "header_paths": header_paths,
        "candidate_row_count": len(rows),
        "selected_row_count": len(records),
        "unknown_cells": unknown,
        "unresolved_rows": [],
        "records": records,
    }


def run_queries(tables: dict[str, Any], queries: list[dict[str, Any]]) -> dict[str, Any]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.create_function("empty", 1, lambda v: int(v is None or not str(v).strip()))
    connection.create_function("norm", 1, lambda v: " ".join(str(v).split()).casefold() if v is not None else None)
    inventory = {}
    for alias, table in tables.items():
        if not re.fullmatch("[a-z][a-z0-9_]*", alias):
            raise ValueError("SQL table alias must be snake_case")
        fields = [*table["binding"]["columns"], *table["binding"]["field_holes"]]
        invalid_names = any(not name or "\x00" in name or name.casefold() in ("row_id", "evidence_json", "states_json", "scope_state") for name in fields)
        if invalid_names or len({name.casefold() for name in fields}) != len(fields):
            raise ValueError("Invalid SQL field")
        column_sql = ",".join('"' + name.replace('"', '""') + '"' for name in fields)
        connection.execute(f'CREATE TABLE "{alias}" (row_id INTEGER, {column_sql}, evidence_json TEXT, states_json TEXT, scope_state TEXT)')
        for record in table["records"]:
            values = [
                record["row_id"],
                *[record["values"][name] for name in fields],
                json.dumps(record["evidence"], ensure_ascii=False),
                json.dumps(record["states"], ensure_ascii=False),
                record["scope_state"],
            ]
            connection.execute(f'INSERT INTO "{alias}" VALUES ({",".join("?" for _ in values)})', values)
        inventory[alias] = {"source": table["source"], "rows": len(table["records"]), "unknown_cells": table["unknown_cells"], "unresolved_rows": table["unresolved_rows"]}
    connection.commit()
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
    connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
    results = []
    for query in queries:
        ticks = 0

        def progress() -> bool:
            nonlocal ticks
            ticks += 1
            return ticks > 10000

        connection.set_progress_handler(progress, 1000)
        try:
            cursor = connection.execute(query["sql"])
            results.append({"id": query["id"], "sql": query["sql"], "rows": [dict(row) for row in cursor], "error": None})
        except sqlite3.Error as error:
            results.append({"id": query["id"], "sql": query["sql"], "rows": None, "error": str(error)})
    connection.close()
    return {"tables": inventory, "queries": results, "warning": "SQL rows are evidence only. Check binding, prerequisites, unknown cells and empty-scope policy before assigning audit status."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Read files once into source-addressed JSON snapshots")
    inspect.add_argument("files", nargs="+")
    inspect.add_argument("--out-dir", required=True)
    view = commands.add_parser("view", help="Print only requested snapshot rows or PDF pages")
    view.add_argument("snapshot", help="A single snapshot, or the frozen documents.json index")
    view.add_argument("--document", help="Exact document_id when reading the frozen index")
    view.add_argument("--table")
    view.add_argument("--rows", default="1:12")
    view.add_argument("--columns", help="One column or an inclusive column range, such as A or B:F")
    view.add_argument("--pages", default="1")
    binding = commands.add_parser("bind", help="Apply a reviewed per-document table binding")
    binding.add_argument("documents", help="Frozen documents.json, keyed by document_id")
    binding.add_argument("--binding", required=True, help="One AuditTrace Binding as JSON")
    binding.add_argument("--out", required=True)
    query = commands.add_parser("query", help="Execute SELECT queries on bound tables; never assigns PASS")
    query.add_argument("--table", action="append", required=True, help="alias=bound-table.json")
    query.add_argument("--queries", required=True, help="JSON array of {id,sql}")
    query.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.command == "inspect":
        for filename in args.files:
            data = snapshot(filename)
            output = Path(args.out_dir) / (Path(filename).name + ".json")
            if output.exists() and json.loads(output.read_text())["source"] != data["source"]:
                raise ValueError("Output name collision: use a separate output directory")
            save(output, data)
            print(
                json.dumps(
                    {
                        "snapshot": str(output),
                        "source": data["source"],
                        "tables": [{k: t[k] for k in ("id", "rows", "cols")} for t in data["tables"]],
                        "pages": len(data.get("pages", [])),
                        "limitations": data["limitations"],
                    },
                    ensure_ascii=False,
                )
            )
    elif args.command == "view":
        data = json.loads(Path(args.snapshot).read_text())
        if args.document is not None:
            selected_document = data.get(args.document) if isinstance(data, dict) else None
            if not isinstance(selected_document, dict) or not isinstance(selected_document.get("tables"), list) or not isinstance(selected_document.get("source"), dict):
                raise ValueError("Document identity is not in the frozen index")
            data = selected_document
        elif not isinstance(data, dict) or not isinstance(data.get("tables"), list) or not isinstance(data.get("source"), dict):
            raise ValueError("Choose a frozen document_id with --document")
        if data.get("pages") and args.table is None:
            for page in data["pages"]:
                if page["page"] in sequence(args.pages):
                    print(json.dumps(page, ensure_ascii=False))
        else:
            tables = [t for t in data["tables"] if args.table is None or t["id"] == args.table]
            if args.table is not None and not tables:
                raise ValueError(f"Table not found: {args.table}")
            for table in tables:
                rows = [row for row in sequence(args.rows) if row <= table["rows"]]
                columns = list(range(1, table["cols"] + 1))
                if args.columns is not None:
                    limits = args.columns.split(":")
                    if len(limits) > 2 or any(not re.fullmatch(r"[A-Za-z]{1,3}", item) for item in limits):
                        raise ValueError("Choose one column or an inclusive column range")
                    first, last = col_number(limits[0].upper()), col_number(limits[-1].upper())
                    if not 1 <= first <= last <= table["cols"]:
                        raise ValueError("Column selection is outside the table")
                    columns = list(range(first, last + 1))
                merges = [span for span in table.get("merges", []) if any(span[0] <= row <= span[2] for row in rows) and any(span[1] <= column <= span[3] for column in columns)]
                print(json.dumps({"document_id": args.document, "source": data["source"], "table": table["id"], "rows": table["rows"], "cols": table["cols"], "merges": merges}, ensure_ascii=False))
                for row in rows:
                    print(json.dumps({"row": row, "cells": {ref(row, column): effective(table, row, column) for column in columns}}, ensure_ascii=False))
    elif args.command == "bind":
        from ppap_deep.semantic.bindings import Binding

        selected = Binding.model_validate_json(Path(args.binding).read_text())
        documents = json.loads(Path(args.documents).read_text())
        if selected.document_id not in documents:
            raise ValueError("Binding document outside frozen submission")
        data = bind(documents[selected.document_id], selected.model_dump(mode="json"))
        save(args.out, data)
        print(
            json.dumps(
                {
                    "output": args.out,
                    "binding_id": data["binding"]["id"],
                    "rows": data["selected_row_count"],
                    "unknown_cells": len(data["unknown_cells"]),
                    "unresolved_rows": len(data["unresolved_rows"]),
                },
                ensure_ascii=False,
            )
        )  # noqa: E501
    else:
        query_tables = {}
        for item in args.table:
            alias, filename = item.split("=", 1)
            if alias in query_tables:
                raise ValueError(f"Duplicate table alias: {alias}")
            query_tables[alias] = json.loads(Path(filename).read_text())
        data = run_queries(query_tables, json.loads(Path(args.queries).read_text()))
        save(args.out, data)
        print(
            json.dumps(
                {
                    "output": args.out,
                    "tables": {name: {"rows": item["rows"], "unknown_cells": len(item["unknown_cells"]), "unresolved_rows": len(item["unresolved_rows"])} for name, item in data["tables"].items()},
                    "queries": [{"id": q["id"], "row_count": len(q["rows"]) if q["rows"] is not None else None, "error": q["error"]} for q in data["queries"]],
                },
                ensure_ascii=False,
            )
        )
        if any(q["error"] for q in data["queries"]):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
