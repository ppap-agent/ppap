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
from contextlib import ExitStack, closing
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any


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


def active_text(element: Any) -> str:
    """Read direct OOXML deletions/strikes; inherited style review remains explicit."""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def visit(node: Any) -> str:
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
    result: dict[str, Any] = {"source": {"file": path.name, "sha256": digest(path), "format": suffix}, "tables": [], "limitations": []}
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
                            item["formula"] = cell.value
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
                    table["cells"][ref(xls_row + 1, column + 1)] = {"value": value, "state": "unknown_excel_error" if xls_cell.ctype == xlrd.XL_CELL_ERROR else "known"}
            result["tables"].append(table)
        result["limitations"] = ["XLS exposes cached values; formula freshness and embedded objects are not verified."]
    elif suffix == ".docx":
        from docx import Document

        document = Document(str(path))
        within(len(document.paragraphs), limits.paragraphs, "paragraphs")
        within(len(document.tables), limits.tables, "tables")
        result["paragraphs"] = [{"location": f"paragraph:{i + 1}", "text": active_text(p._p)} for i, p in enumerate(document.paragraphs)]
        for i, word_table in enumerate(document.tables):
            data: dict[str, Any] = {"id": f"table:{i + 1}", "rows": len(word_table.rows), "cols": 0, "cells": {}, "merges": []}
            visited_cells += len(word_table.rows) * len(word_table.columns)
            within(visited_cells, limits.cells, "cell visits")
            anchors: dict[Any, str] = {}
            for r, word_row in enumerate(word_table.rows, 1):
                data["cols"] = max(data["cols"], len(word_row.cells))
                for c, word_cell in enumerate(word_row.cells, 1):
                    address = ref(r, c)
                    anchor = anchors.setdefault(word_cell._tc, address)
                    data["cells"][address] = {"value": active_text(word_cell._tc), "state": "known", "anchor": anchor}
            result["tables"].append(data)
        result["limitations"] = ["Direct strike/deletion markup excluded; inherited styles, drawings, nested tables and document headers/footers need separate inspection when relevant."]
    elif suffix == ".pdf":
        import fitz as pymupdf

        with pymupdf.open(path) as document:
            result["pages"] = []
            within(len(document), limits.pages, "pages")
            block_count = 0
            for page in document:
                blocks = [{"bbox": list(block[:4]), "text": block[4]} for block in page.get_text("blocks") if block[6] == 0]
                block_count += len(blocks)
                within(block_count, limits.blocks, "blocks")
                result["pages"].append(
                    {"page": page.number + 1, "width": page.rect.width, "height": page.rect.height, "text": page.get_text(), "blocks": blocks, "image_count": len(page.get_images())}
                )
        result["limitations"] = ["Text blocks are observations, not table rows; scanned content, checkmarks and ambiguous geometry require page rendering/OCR."]
    else:
        raise ValueError(f"Unsupported input format: {suffix}")
    output_size = 0
    for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(result):
        output_size += len(chunk.encode("utf-8"))
        within(output_size, limits.output_bytes, "snapshot bytes")
    return result


def effective(table: dict[str, Any], row: int, column: int) -> dict[str, Any]:
    address = ref(row, column)
    anchor = address
    span = None
    for r1, c1, r2, c2 in table.get("merges", []):
        if r1 <= row <= r2 and c1 <= column <= c2:
            anchor, span = ref(r1, c1), f"{ref(r1, c1)}:{ref(r2, c2)}"
            break
    item = table["cells"].get(anchor, {"value": None, "state": "known"})
    anchor = item.get("anchor", anchor)
    return {**item, "cell": address, "anchor": anchor, "merge": span}


def sequence(text: str) -> list[int]:
    values: list[int] = []
    for part in text.split(","):
        start, *end = part.split(":")
        first, last = int(start), int(end[0]) if end else int(start)
        if first < 1 or last < first:
            raise ValueError("Rows must be positive and increasing")
        values.extend(range(first, last + 1))
    return list(dict.fromkeys(values))


def bind(data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    table = next(t for t in data["tables"] if t["id"] == config["table"])
    headers, rows = sequence(config["header_rows"]), sequence(config["body_rows"])
    columns = {name: col_number(column) for name, column in config["columns"].items()}
    if not columns or any(not re.fullmatch("[a-z][a-z0-9_]*", name) or name in ("row_id", "evidence_json") for name in columns):
        raise ValueError("Provide non-reserved snake_case semantic columns")
    if max(headers + rows) > table["rows"] or max(columns.values()) > table["cols"]:
        raise ValueError("Binding outside observed table bounds")
    if set(headers) & set(rows):
        raise ValueError("Header and body rows overlap")
    exclusions = config.get("exclude_rows", {})
    if any(not str(reason).strip() or int(r) not in rows for r, reason in exclusions.items()):
        raise ValueError("Each excluded body row needs a reason")
    header_paths = {}
    for name, column in columns.items():
        parts, seen = [], set()
        for row in headers:
            item = effective(table, row, column)
            if item["anchor"] not in seen and item["value"] is not None:
                parts.append(item)
                seen.add(item["anchor"])
        header_paths[name] = parts
    records: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for row in rows:
        if str(row) in exclusions:
            continue
        record: dict[str, Any] = {"row_id": row, "values": {}, "evidence": {}}
        for name, column in columns.items():
            item = effective(table, row, column)
            record["values"][name] = item["value"]
            record["evidence"][name] = item
            if item["state"] != "known":
                unknown.append({"row_id": row, "field": name, "state": item["state"]})
        records.append(record)
    return {
        "source": data["source"],
        "binding": config,
        "header_paths": header_paths,
        "candidate_row_count": len(rows),
        "selected_row_count": len(records),
        "unknown_cells": unknown,
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
        fields = list(table["binding"]["columns"])
        if any(not re.fullmatch("[a-z][a-z0-9_]*", name) or name in ("row_id", "evidence_json") for name in fields):
            raise ValueError("Invalid SQL field")
        column_sql = ",".join(f'"{name}"' for name in fields)
        connection.execute(f'CREATE TABLE "{alias}" (row_id INTEGER, {column_sql}, evidence_json TEXT)')
        for record in table["records"]:
            values = [record["row_id"], *[record["values"][name] for name in fields], json.dumps(record["evidence"], ensure_ascii=False)]
            connection.execute(f'INSERT INTO "{alias}" VALUES ({",".join("?" for _ in values)})', values)
        inventory[alias] = {"source": table["source"], "rows": len(table["records"]), "unknown_cells": table["unknown_cells"]}
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
    view.add_argument("snapshot")
    view.add_argument("--table")
    view.add_argument("--rows", default="1:12")
    view.add_argument("--pages", default="1")
    binding = commands.add_parser("bind", help="Apply a reviewed per-document table binding")
    binding.add_argument("snapshot")
    binding.add_argument("--config", required=True)
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
        if data.get("pages"):
            for page in data["pages"]:
                if page["page"] in sequence(args.pages):
                    print(json.dumps(page, ensure_ascii=False))
        else:
            tables = [t for t in data["tables"] if args.table is None or t["id"] == args.table]
            if args.table is not None and not tables:
                raise ValueError(f"Table not found: {args.table}")
            for table in tables:
                print(json.dumps({"table": table["id"], "merges": table.get("merges", [])}, ensure_ascii=False))
                for row in sequence(args.rows):
                    if row <= table["rows"]:
                        print(json.dumps({"row": row, "cells": {ref(row, c): table["cells"][ref(row, c)] for c in range(1, table["cols"] + 1) if ref(row, c) in table["cells"]}}, ensure_ascii=False))
    elif args.command == "bind":
        data = bind(json.loads(Path(args.snapshot).read_text()), json.loads(Path(args.config).read_text()))
        save(args.out, data)
        print(json.dumps({k: v for k, v in data.items() if k != "records"}, ensure_ascii=False))
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
                    "tables": data["tables"],
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
