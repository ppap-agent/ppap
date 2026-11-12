"""Portable document snapshots, explicit table bindings, and read-only SQL evidence.

This module does not decide audit status or select customer templates.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import posixpath
import re
import sqlite3
import sys
import zipfile
from bisect import bisect_right
from collections.abc import Callable
from contextlib import ExitStack, closing
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET

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
    # Line segments one PDF page may hand to table grid reconstruction; a page past it is skipped.
    # A dense 89k-line capability page reads in about two seconds, so only absurd pages are skipped.
    page_lines: int = 1_000_000
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


def emf_text(data: bytes) -> list[str]:
    """Strings an EMF picture draws as text (EMR_EXTTEXTOUTW records); its shapes stay unread."""
    import struct

    texts, position = [], 0
    while position + 8 <= len(data):
        kind, size = struct.unpack_from("<II", data, position)
        if size < 8 or position + size > len(data):
            break
        if kind == 84 and size >= 76:
            count, offset = struct.unpack_from("<II", data, position + 44)
            if offset + 2 * count <= size:
                texts.append(data[position + offset : position + offset + 2 * count].decode("utf-16-le", "replace"))
        position += size
    return texts


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


_XLSX_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XLSX_SS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
# DrawingML ST_Coordinate, integer EMUs (ISO/IEC 29500-1 §20.1.10.16).
_XLSX_COORDINATE_MIN = -27273042329600
_XLSX_COORDINATE_MAX = 27273042316900
_XLSX_EMU_PER_POINT = 12700
# The narrowest digit of a common default font is 5 pixels at 9525 EMU each.
_XLSX_EMU_PER_CHARACTER = 5 * 9525
_XlsxExtents = tuple[dict[int, int], int, list[tuple[int, int, int]], int]
_XLSX_LIMITS = {"row": 1048576, "column": 16384}
_XLSX_ANCHORS = {"twoCellAnchor", "oneCellAnchor", "absoluteAnchor"}
# Objects whose native placement names cells; their reach can be bounded.
_XLSX_BOUNDED_KINDS = {"twoCellAnchor", "oneCellAnchor", "control", "oleObject", "legacyDrawing"}
# An endpoint walking farther than this is bounded only by the sheet edge.
_XLSX_REACH_CELLS = 1024
_VML = "urn:schemas-microsoft-com:office:excel"
_EMU_PER_PIXEL = 9525


def _archive_target(part: str, target: str) -> str:
    # Relationship targets are archive names, never filesystem paths or URLs.
    if not target or any(char in target for char in ("\\", ":", "?", "#", "%", "\x00")):
        raise ValueError("Invalid visual relationship target")
    result = posixpath.normpath(posixpath.join(posixpath.dirname(part), target)) if not target.startswith("/") else target.lstrip("/")
    if result in (".", "..") or result.startswith("../"):
        raise ValueError("Visual relationship is outside the archive")
    return result


def _xlsx_visual_evidence(path: Path, limits: ReadLimits) -> dict[str, list[dict[str, Any]]]:
    """Keep uninterpreted OOXML objects; no shape-to-business-value mapping.

    A two-cell anchor gives a cell intersection when its offsets fit the
    measured cells. Other placements that name cells get bounds: every cell
    they can reach given lower-bound cell sizes. Placements without cells stay
    explicitly unresolved on their own worksheet.
    Offsets use integer EMUs; unit-qualified lengths are unsupported.
    No archive member is extracted and no external relationship is followed.
    """
    result: dict[str, list[dict[str, Any]]] = {}
    count = 0
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())

        def xml(part: str) -> ET.Element:
            if part not in names:
                raise ValueError("Missing visual source part")
            content = archive.read(part)
            if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                raise ValueError("Unsupported XML entity in visual source")
            try:
                return ET.fromstring(content)
            except ET.ParseError as exc:
                raise ValueError("Invalid visual source XML") from exc

        def relationships(part: str) -> dict[str, dict[str, str]]:
            source = PurePosixPath(part)
            name = str(source.parent / "_rels" / (source.name + ".rels"))
            if name not in names:
                return {}
            rels = {}
            for node in xml(name):
                identity = node.get("Id")
                if not identity or identity in rels or not node.get("Target") or not node.get("Type"):
                    raise ValueError("Invalid visual source relationship")
                rels[identity] = dict(node.attrib)
            return rels

        def related(part: str, rels: dict[str, dict[str, str]], identity: str | None, kind: str | None = None) -> str:
            relation = rels.get(identity or "")
            if relation is None or relation.get("TargetMode", "Internal") != "Internal" or (kind and not relation["Type"].endswith("/" + kind)):
                raise ValueError("Unresolved visual source relationship")
            target = _archive_target(part, relation["Target"])
            if target not in names:
                raise ValueError("Missing visual source part")
            return target

        def add(
            items: list[dict[str, Any]],
            part: str,
            index: int,
            kind: str,
            node: ET.Element,
            *,
            rels: dict[str, dict[str, str]],
            location: ET.Element | None = None,
            extents: _XlsxExtents | None = None,
            markers: tuple[dict[str, int] | None, dict[str, int] | None] | None = None,
        ) -> None:
            nonlocal count
            count += 1
            within(count, limits.blocks, "visual objects")
            item: dict[str, Any] = {"source": {"part": part, "anchor_index": index}, "kind": kind, "content_observed": False}
            location = node if location is None else location
            first, last = markers if markers is not None else (_xlsx_marker(location, "from"), _xlsx_marker(location, "to"))
            extent = _xlsx_extent(location) if kind == "oneCellAnchor" else None
            if first is not None:
                item["from"] = first
            if last is not None:
                item["to"] = last
            if extent is not None:
                item["extent_emu"] = extent
            item["range"] = None
            if kind == "twoCellAnchor" and first is not None and last is not None:
                measured = _xlsx_measure(first, last, extents)
                item["range"] = _xlsx_anchor_range(first, last, measured)
                if item["range"] is not None and _xlsx_has_offset(first, last):
                    item["measured_emu"] = measured
            if item["range"] is None:
                bounded = None
                if kind in _XLSX_BOUNDED_KINDS and first is not None and (last is not None or extent is not None) and extents is not None:
                    bounded = _xlsx_anchor_bounds(first, last, extent, lambda axis, cell: _xlsx_size(extents, axis, cell))
                if bounded is None:
                    item["unresolved_reason"] = "unsupported_anchor_geometry"
                else:
                    item["bounds"], item["bound_emu"] = bounded
                    item["unresolved_reason"] = "imprecise_anchor_geometry"
            references = []
            for child in node.iter():
                for attribute, identity in child.attrib.items():
                    if attribute.startswith("{" + _XLSX_REL + "}") or attribute == "{urn:schemas-microsoft-com:office:office}relid":
                        relation = rels.get(identity, {})
                        if (
                            child.tag == "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
                            and attribute in {f"{{{_XLSX_REL}}}embed", f"{{{_XLSX_REL}}}link"}
                            and relation.get("Type") != _XLSX_REL + "/image"
                        ):
                            raise ValueError("Invalid visual image relationship")
                        if (
                            attribute == f"{{{_XLSX_REL}}}id"
                            and child.tag in {f"{{http://schemas.openxmlformats.org/drawingml/2006/main}}{name}" for name in ("hlinkClick", "hlinkHover")}
                            and relation.get("Type") == _XLSX_REL + "/hyperlink"
                            and relation.get("TargetMode", "Internal") in {"Internal", "External"}
                        ):
                            continue  # Navigation is not visual content; never open its target.
                        if (
                            child.tag == "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
                            and attribute == f"{{{_XLSX_REL}}}link"
                            and relation.get("Type") == _XLSX_REL + "/image"
                            and relation.get("TargetMode") == "External"
                        ):
                            references.append({"relationship_id": identity, "state": "external_unavailable"})
                            continue  # Keep the unavailable source without opening or exposing its target.
                        references.append({"relationship_id": identity, "part": related(part, rels, identity)})
            if references:
                item["references"] = references
            items.append(item)

        workbook = "xl/workbook.xml"
        workbook_rels = relationships(workbook)
        for sheet in xml(workbook).iter(f"{{{_XLSX_SS}}}sheet"):
            sheet_part = related(workbook, workbook_rels, sheet.get(f"{{{_XLSX_REL}}}id"))
            sheet_xml, sheet_rels = xml(sheet_part), relationships(sheet_part)
            items = result.setdefault(str(sheet.attrib["name"]), [])
            extents = _xlsx_sheet_extents(sheet_xml)
            # Follow the original ZIP relationships even when openpyxl drops
            # unsupported shapes. Images/charts remain source references only.
            for drawing in sheet_xml.iter(f"{{{_XLSX_SS}}}drawing"):
                part = related(sheet_part, sheet_rels, drawing.get(f"{{{_XLSX_REL}}}id"), "drawing")
                drawing_xml, drawing_rels = xml(part), relationships(part)
                if drawing_xml.tag != f"{{{_XLSX_DRAWING}}}wsDr":
                    raise ValueError("Invalid spreadsheet drawing root")
                within(sum(1 for _ in drawing_xml.iter()), limits.blocks, "visual XML nodes")
                for index, node in enumerate(drawing_xml, 1):
                    kind = node.tag.rsplit("}", 1)[-1]
                    # Each alternate representation carries its own anchor.
                    placements = [child for child in node.iter() if child.tag.rsplit("}", 1)[-1] in _XLSX_ANCHORS] if kind == "AlternateContent" else []
                    for placement in placements or [node]:
                        add(items, part, index, placement.tag.rsplit("}", 1)[-1], placement, rels=drawing_rels, extents=extents)
            for index, control in enumerate(sheet_xml.iter(), 1):
                kind = control.tag.rsplit("}", 1)[-1]
                if kind not in {"legacyDrawing", "control", "oleObject", "picture"}:
                    continue
                control_identity = control.get(f"{{{_XLSX_REL}}}id")
                if not control_identity:
                    raise ValueError("Missing worksheet visual relationship")
                part = related(sheet_part, sheet_rels, control_identity)
                if kind == "legacyDrawing":
                    legacy, legacy_rels = xml(part), relationships(part)
                    if legacy.tag != "xml":
                        raise ValueError("Invalid spreadsheet VML root")
                    within(sum(1 for _ in legacy.iter()), limits.blocks, "visual XML nodes")
                    # Comment popups are annotations, not cell visual content.
                    kinds = {"shape", "rect", "roundrect", "oval", "arc", "curve", "polyline", "line", "group", "image", "background"}
                    objects = (node for node in legacy.iter() if node.tag.rsplit("}", 1)[-1] in kinds)
                    for shape_index, node in enumerate(objects, 1):
                        if not any(child.get("ObjectType") == "Note" for child in node.iter()):
                            add(items, part, shape_index, "legacyDrawing", node, rels=legacy_rels, extents=extents, markers=_vml_markers(node))
                else:
                    placement = next((node for node in control.iter() if node.tag.rsplit("}", 1)[-1] == "anchor"), control)
                    add(items, sheet_part, index, kind, control, rels=sheet_rels, location=placement, extents=extents)
    return result


def _xlsx_marker(anchor: ET.Element, name: str) -> dict[str, int] | None:
    markers = [node for node in anchor if node.tag.rsplit("}", 1)[-1] == name]
    if not markers:
        return None
    if len(markers) != 1:
        raise ValueError("Duplicate visual anchor marker")
    marker = markers[0]
    values = {node.tag.rsplit("}", 1)[-1]: node.text for node in marker}
    if len(marker) != 4 or set(values) != {"row", "col", "rowOff", "colOff"}:
        raise ValueError("Incomplete visual anchor marker")
    try:
        row, column, row_offset, column_offset = (int(values[key] or "") for key in ("row", "col", "rowOff", "colOff"))
    except ValueError as exc:
        raise ValueError("Invalid visual anchor marker") from exc
    if not (0 <= row < 1048576 and 0 <= column < 16384 and _XLSX_COORDINATE_MIN <= row_offset <= _XLSX_COORDINATE_MAX and _XLSX_COORDINATE_MIN <= column_offset <= _XLSX_COORDINATE_MAX):
        raise ValueError("Invalid visual anchor marker")
    return {"row": row + 1, "column": column + 1, "row_offset_emu": row_offset, "column_offset_emu": column_offset}


def _xlsx_sheet_extents(sheet: ET.Element) -> _XlsxExtents:
    """Stored row heights and column widths in EMU: explicit rows, default row, explicit columns, default column.

    A width counts characters of the workbook's default font. Its digit width is
    not stored, so the narrowest common one gives a lower bound: an offset that
    fits this extent fits the real cell, and an unmeasured size stays zero.
    """
    node = f"{{{_XLSX_SS}}}"
    defaults = next(sheet.iter(node + "sheetFormatPr"), None)
    attributes = defaults.attrib if defaults is not None else {}
    default_row = 0 if attributes.get("zeroHeight") in {"1", "true"} else _xlsx_emu(attributes.get("defaultRowHeight"), _XLSX_EMU_PER_POINT)
    default_column = _xlsx_emu(attributes.get("defaultColWidth") or attributes.get("baseColWidth", "8"), _XLSX_EMU_PER_CHARACTER)
    rows = {}
    for row in sheet.iter(node + "row"):
        if row.get("r", "").isdigit() and (row.get("ht") or row.get("hidden")):
            hidden = row.get("hidden") in {"1", "true"}
            rows[int(row.attrib["r"])] = 0 if hidden else _xlsx_emu(row.get("ht"), _XLSX_EMU_PER_POINT) or default_row
    columns = []
    for column in sheet.iter(node + "col"):
        first, last = column.get("min", ""), column.get("max", "")
        if first.isdigit() and last.isdigit():
            hidden = column.get("hidden") in {"1", "true"}
            columns.append((int(first), int(last), 0 if hidden else _xlsx_emu(column.get("width"), _XLSX_EMU_PER_CHARACTER)))
    return rows, default_row, columns, default_column


def _xlsx_emu(value: str | None, unit: int) -> int:
    try:
        return max(0, int(float(value or 0) * unit))
    except (ValueError, OverflowError):
        return 0


def _xlsx_size(extents: _XlsxExtents, axis: str, number: int) -> int:
    rows, default_row, columns, default_column = extents
    if axis == "row":
        return rows.get(number, default_row)
    return next((width for start, end, width in columns if start <= number <= end), default_column)


def _xlsx_measure(first: dict[str, int], last: dict[str, int], extents: _XlsxExtents | None) -> dict[str, int] | None:
    if extents is None:
        return None
    return {
        "from_row": _xlsx_size(extents, "row", first["row"]),
        "from_column": _xlsx_size(extents, "column", first["column"]),
        "to_row": _xlsx_size(extents, "row", last["row"]),
        "to_column": _xlsx_size(extents, "column", last["column"]),
    }


def _xlsx_extent(anchor: ET.Element) -> dict[str, int] | None:
    nodes = [node for node in anchor if node.tag.rsplit("}", 1)[-1] == "ext"]
    if len(nodes) != 1:
        return None
    try:
        width, height = int(nodes[0].get("cx", "")), int(nodes[0].get("cy", ""))
    except ValueError as exc:
        raise ValueError("Invalid visual anchor extent") from exc
    if not (0 <= width <= _XLSX_COORDINATE_MAX and 0 <= height <= _XLSX_COORDINATE_MAX):
        raise ValueError("Invalid visual anchor extent")
    return {"row": height, "column": width}


def _vml_markers(shape: ET.Element) -> tuple[dict[str, int] | None, dict[str, int] | None]:
    """VML x:Anchor: left column, offset, top row, offset, right column, offset, bottom row, offset (zero-based cells, pixels)."""
    anchors = [node for node in shape.iter(f"{{{_VML}}}Anchor")]
    if len(anchors) != 1:
        return None, None
    try:
        values = [int(value) for value in (anchors[0].text or "").split(",")]
    except ValueError as exc:
        raise ValueError("Invalid VML anchor") from exc
    if len(values) != 8 or any(value < 0 for value in values) or max(values[0], values[4]) >= _XLSX_LIMITS["column"] or max(values[2], values[6]) >= _XLSX_LIMITS["row"]:
        raise ValueError("Invalid VML anchor")

    def marker(column: int, column_offset: int, row: int, row_offset: int) -> dict[str, int]:
        return {"row": row + 1, "column": column + 1, "row_offset_emu": row_offset * _EMU_PER_PIXEL, "column_offset_emu": column_offset * _EMU_PER_PIXEL}

    return marker(*values[:4]), marker(*values[4:])


def _xlsx_reach(cell: int, offset: int, size_of: Callable[[int], int], limit: int) -> tuple[int, list[int]]:
    """Last cell an endpoint `offset` EMU past `cell`'s origin can fall in, with the sizes walked.

    The sizes are lower bounds, so the real endpoint lies no later. A zero size
    is unmeasured and leaves only the sheet edge as the bound.
    """
    sizes: list[int] = []
    while len(sizes) < _XLSX_REACH_CELLS and cell + len(sizes) <= limit:
        size = size_of(cell + len(sizes))
        sizes.append(size)
        if size == 0:
            break
        if offset <= size:
            return cell + len(sizes) - 1, sizes
        offset -= size
    return limit, sizes


def _xlsx_anchor_bounds(first: dict[str, int], last: dict[str, int] | None, extent: dict[str, int] | None, size_of: Callable[[str, int], int]) -> tuple[list[int], dict[str, list[int]]] | None:
    """Every cell a placed object can cover when its offsets do not fit the measured cells.

    The object ends at `last`, or `extent` EMUs past `first`. Neither endpoint
    lies before its own cell, and either may be the later one, so both are
    walked. A negative offset or an end cell before the start has no bound.
    """
    region: dict[str, tuple[int, int]] = {}
    walked: dict[str, list[int]] = {}
    for axis in ("row", "column"):
        offset = f"{axis}_offset_emu"
        start, start_offset = first[axis], first[offset]
        end, end_offset = (last[axis], last[offset]) if last is not None else (start, start_offset + extent[axis])  # type: ignore[index]
        if start_offset < 0 or end_offset < 0 or end < start:
            return None
        walked[axis] = []
        stop = start
        for cell, distance in ((start, start_offset), (end, end_offset)):
            if distance:
                reached, sizes = _xlsx_reach(cell, distance, functools.partial(size_of, axis), _XLSX_LIMITS[axis])
                walked[axis] += sizes
                stop = max(stop, reached)
            elif cell > start:
                stop = max(stop, cell - 1)  # A zero-offset end is the next cell's boundary.
        if not start_offset and not end_offset and end == start:
            start = max(1, start - 1)  # A zero-size object on a boundary touches both cells.
        region[axis] = (start, stop)
    return [region["row"][0], region["column"][0], region["row"][1], region["column"][1]], walked


def _xlsx_has_offset(first: dict[str, int], last: dict[str, int]) -> bool:
    return any(marker[offset] != 0 for marker in (first, last) for offset in ("row_offset_emu", "column_offset_emu"))


def _xlsx_offsets_fit(first: dict[str, int], last: dict[str, int], measured: dict[str, int]) -> bool:
    pairs = (
        (first["row_offset_emu"], measured["from_row"]),
        (first["column_offset_emu"], measured["from_column"]),
        (last["row_offset_emu"], measured["to_row"]),
        (last["column_offset_emu"], measured["to_column"]),
    )
    return all(0 <= offset <= extent for offset, extent in pairs)


def _xlsx_anchor_range(first: dict[str, int], last: dict[str, int], measured: dict[str, int] | None = None) -> list[int] | None:
    # An offset can cross its cell, so its nominal cell is only a navigation
    # hint until the offset is shown to fit inside the measured cell.
    has_offset = _xlsx_has_offset(first, last)
    if has_offset and (measured is None or not _xlsx_offsets_fit(first, last, measured)):
        return None
    if any(last[name] < first[name] for name in ("row", "column")):
        if has_offset:
            return None
        raise ValueError("Reversed visual anchor")
    ends = []
    for name, offset in (("row", "row_offset_emu"), ("column", "column_offset_emu")):
        if last[name] == first[name] and last[offset] <= first[offset]:
            return None  # Native line shapes can have a zero-width or zero-height box.
        # A zero-offset endpoint is the next cell boundary, not part of that cell.
        ends.append(last[name] if last[offset] else last[name] - 1)
    return [first["row"], first["column"], *ends]


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

        visuals = _xlsx_visual_evidence(path, limits)
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
                if visuals.get(sheet.title):
                    table["visual_evidence"] = visuals[sheet.title]
                    # A visual can extend beyond the last stored scalar cell.
                    for visual in table["visual_evidence"]:
                        if visual["range"] is not None:
                            table["rows"] = max(table["rows"], visual["range"][2])
                            table["cols"] = max(table["cols"], visual["range"][3])
                        elif "from" in visual:
                            table["rows"] = max(table["rows"], visual["from"]["row"])
                            table["cols"] = max(table["cols"], visual["from"]["column"])
                result["tables"].append(table)
        result["limitations"] = [
            "Uninterpreted visual sources remain unknown on overlapping empty cells. Two-cell anchors whose offsets fit the measured cells have exact cell coverage; "
            "other cell-anchored objects cover every cell they can reach, and placements without cells affect empty cells on their worksheet. "
            "Linked external images are not fetched; local caches remain source references. "
            "Only integer EMU offsets are supported. Formula caches are not recalculated."
        ]
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
        from audit.pdf_tables import snapshot_pdf

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


def _effective(table: dict[str, Any], row: int, column: int, merges: list, visuals: list[tuple[int, dict[str, Any]]] | None = None) -> dict[str, Any]:
    address = ref(row, column)
    anchor = address
    span = None
    region = [row, column, row, column]
    for r1, c1, r2, c2 in merges:
        if r1 <= row <= r2 and c1 <= column <= c2:
            anchor, span = ref(r1, c1), f"{ref(r1, c1)}:{ref(r2, c2)}"
            region = [r1, c1, r2, c2]
            break
    item = table["cells"].get(anchor, {"value": None, "state": "known"})
    value = item.get("value")
    if item.get("state") == "known" and (value is None or isinstance(value, str) and not value.strip()):
        visual_sources = [
            index for index, visual in (visuals if visuals is not None else enumerate(table.get("visual_evidence", []))) if (cover := visual_region(visual)) is None or _regions_overlap(region, cover)
        ]
        if visual_sources:
            item = {**item, "state": "unknown_visual_evidence", "source": {"visual_evidence": visual_sources}}
    anchor = item.get("anchor", anchor)
    return {**item, "cell": address, "anchor": anchor, "merge": span}


def visual_region(visual: dict[str, Any]) -> list[int] | None:
    """Cells an object covers exactly, else every cell it can reach; None means the whole sheet."""
    return visual["range"] if visual["range"] is not None else visual.get("bounds")


def _regions_overlap(left: list[int], right: list[int]) -> bool:
    return left[0] <= right[2] and right[0] <= left[2] and left[1] <= right[3] and right[1] <= left[3]


def effective(table: dict[str, Any], row: int, column: int) -> dict[str, Any]:
    return _effective(table, row, column, table.get("merges", []))


def source_title_location(data: dict[str, Any], aliases) -> dict[str, Any] | None:
    """Find a complete native title; body references and row data are not titles."""
    import unicodedata

    from audit.kinds import PpapDocumentType as T

    def key(value):
        normalized = unicodedata.normalize("NFKC", str(value if value is not None else "")).casefold()
        return re.sub(r"[\s:：()（）\[\]【】_.。-]", "", normalized)

    # Recognize complete source titles, not prose which refers to a counterpart.
    # Reuse the canonical bilingual names already owned by the document types.
    titles = set(aliases)
    for alias in aliases:
        for kind in T:
            if kind is not T.UNKNOWN and key(alias) in {key(name) for name in kind.iter_aliases()}:
                titles.update(kind.value)
    if "偏差认可汇总" in titles:
        titles.add("偏差认可汇总清单")
    if "kappa" in {key(title) for title in titles}:
        titles.update(("Kappa分析记录", "Kappa分析报告", "Kappa报告"))
    if "grr" in {key(title) for title in titles}:
        titles.update(("GRR分析记录", "GRR分析报告", "GRR报告"))
    if "特殊特性" in titles:
        titles.update((*T.PROCESS_SPECIAL_CHARACTERISTIC_LIST.value, "特殊特性清单"))
    if "PGQP1" in titles:
        titles.add("Purchased Goods Quality Plan")
    if "bubble" in {key(title) for title in titles}:
        titles.add("Bubble drawing")
    names = "|".join(re.escape(title) for title in sorted({key(title) for title in titles}, key=len, reverse=True))
    # A form number and revision can surround a title. Arbitrary words cannot.
    pattern = re.compile(r"(?:(?:apqp|ppap)|[a-z]{0,8}[0-9][0-9/]*[a-z]?)?(?:" + names + r"){1,3}(?:(?:v|rev|revision)[0-9]+[a-z]?)?")

    def matches(value):
        return pattern.fullmatch(key(value)) is not None

    own_title_labels = {"标题", "表名", "图名", "文件名称", "文档名称", "title", "documenttitle", "documentname", "tablename"}

    def heading_context(text, current):
        if not text.strip():
            return current
        if key(text) in own_title_labels:
            return True
        # A company banner can precede a document's own title. Other native
        # text does not prove that a later block is a heading rather than data.
        return current and re.search(r"(?:公司|company|ltd\.?|limited|inc\.?|gmbh)\s*$", text, re.IGNORECASE) is not None

    for table in data.get("tables", []):
        if matches(table["id"]):
            return {"table": table["id"]}
        own_context = True
        for row in range(1, min(5, table["rows"]) + 1):
            cells: dict[str, tuple[int, dict]] = {}
            for column in range(1, table["cols"] + 1):
                cell = effective(table, row, column)
                if cell["state"] != "known" or key(cell.get("value")):
                    cells.setdefault(cell["anchor"], (column, cell))
            for column, observed in cells.values():
                # A title is standalone (merged cells count once), or explicitly
                # labelled as this document's title. Row data is not a title.
                previous = effective(table, row, column - 1) if column > 1 else {}
                own_title = previous.get("state") == "known" and key(previous.get("value")) in own_title_labels
                if (len(cells) == 1 and own_context or own_title) and observed["state"] == "known" and matches(observed.get("value")):
                    return {"table": table["id"], "row": row, "column": column}
            if len(cells) == 1:
                _, observed = next(iter(cells.values()))
                own_context = observed["state"] == "known" and heading_context(str(observed.get("value") or ""), own_context)
            elif cells:
                own_context = False
    # DOCX/PDF titles need their native location, not synthetic spreadsheet cells.
    own_context = True
    for index, paragraph in enumerate(data.get("paragraphs", [])[:3], 1):
        text = paragraph.get("text", "")
        if matches(text) and own_context:
            return {"kind": "docx_paragraph", "paragraph": index}
        own_context = heading_context(text, own_context)
    for page in data.get("pages", [])[:1]:
        own_context = True
        for index, block in enumerate(page.get("blocks", [])[:3], 1):
            text = block.get("text", "")
            if matches(text) and own_context:
                return {"kind": "pdf_block", "page": page.get("page", 1), "block": index}
            own_context = heading_context(text, own_context)
    return None


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
        self.visual_rows: list[int] = []
        self.visuals: list[list[tuple[int, dict[str, Any]]]] = []
        visual_events: dict[int, list[tuple[int, dict[str, Any] | None]]] = {}
        budget.consume(2 * len(table.get("visual_evidence", [])))
        for index, visual in enumerate(table.get("visual_evidence", [])):
            region = visual_region(visual)
            first, last = (region[0], region[2]) if region is not None else (1, table["rows"])
            visual_events.setdefault(first, []).append((index, visual))
            visual_events.setdefault(last + 1, []).append((index, None))
        active_visuals: dict[int, dict[str, Any]] = {}
        for row in sorted(visual_events):
            for index, visual in visual_events[row]:
                if visual is None:
                    active_visuals.pop(index)
                else:
                    active_visuals[index] = visual
            budget.consume(len(active_visuals) * (len(active_visuals).bit_length() + 1) + 1)
            self.visual_rows.append(row)
            self.visuals.append(sorted(active_visuals.items()))

    def cell(self, row: int, column: int) -> dict[str, Any]:
        if not 1 <= row <= self.table["rows"] or not 1 <= column <= self.table["cols"]:
            raise ValueError("Cell outside observed table")
        index = bisect_right(self.rows, row) - 1
        spans = self.merges[index] if index >= 0 else []
        self.budget.consume(len(spans) + 1)
        # Query all visual row segments touched by the effective merged cell.
        # Each object remains one source; empty grid positions are not stored.
        region = next((span for span in spans if span[1] <= column <= span[3]), [row, column, row, column])
        first = bisect_right(self.visual_rows, region[0]) - 1
        last = bisect_right(self.visual_rows, region[2])
        candidates: dict[int, dict[str, Any]] = {}
        for segment in self.visuals[max(0, first) : last]:
            self.budget.consume(len(segment) + 1)
            candidates.update(segment)
        return _effective(self.table, row, column, spans, sorted(candidates.items()))


def table_window(table: dict[str, Any], rows: list[int], columns: list[int], *, budget: EvidenceBudget | None = None) -> dict[str, Any]:
    """Show a source-addressed window without repeating merged observations.

    Ordinary known null cells use an explicit default within the selection.
    All other observed metadata stays on the cell; merges or aliases retain
    each selected position, including anchors outside the requested window.
    This view never changes the snapshot or the evaluator's reading range.
    """
    from .evidence import EvidenceBudget

    rows = list(dict.fromkeys(row for row in rows if 1 <= row <= table["rows"]))
    columns = list(dict.fromkeys(column for column in columns if 1 <= column <= table["cols"]))
    reader = TableReader(table, budget if budget is not None else EvidenceBudget())
    merges = [span for span in table.get("merges", []) if any(span[0] <= row <= span[2] for row in rows) and any(span[1] <= column <= span[3] for column in columns)]
    cells: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    default = {"value": None, "state": "known"}
    for row in rows:
        for column in columns:
            item = reader.cell(row, column)
            address, anchor, merge = item.pop("cell"), item.pop("anchor"), item.pop("merge")
            if item == default and address == anchor and merge is None:
                continue
            # Python dict equality would collapse False/0 and 1/1.0. Display
            # deduplication must preserve the native JSON scalar representation.
            observed_json = json.dumps(item, sort_keys=True)
            if address != anchor and merge is None:
                original_anchor = {key: value for key, value in table["cells"].get(anchor, default).items() if key != "anchor"}
                cells.setdefault(anchor, original_anchor)
                if observed_json != json.dumps(original_anchor, sort_keys=True):
                    cells[address] = {**item, "anchor": anchor}
                    continue
            if anchor not in cells:
                cells[anchor] = item
            elif json.dumps(cells[anchor], sort_keys=True) != observed_json:
                # Preserve unexpected per-position metadata instead of letting
                # grouping silently choose one of two different observations.
                cells[address] = {**item, "anchor": anchor}
                continue
            if address != anchor and merge is None:
                aliases[address] = anchor
    return {
        **{key: value for key, value in table.items() if key not in {"id", "rows", "cols", "cells", "merges"}},
        "table": table["id"],
        "rows": table["rows"],
        "cols": table["cols"],
        "selection": {"rows": rows, "columns": columns},
        "default_cell": default,
        "merges": merges,
        "cells": cells,
        "aliases": aliases,
    }


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
    from audit.bindings import Binding, binding_scope
    from audit.evidence import EvidenceBudget, cell_locator, resolve

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


def _print_view(data: dict[str, Any], args: argparse.Namespace) -> None:
    if args.offset < 0 or args.max_bytes < 4096:
        raise ValueError("View offset must be non-negative and max-bytes at least 4096")
    records: list[dict[str, Any]] = []
    if data.get("pages") and args.table is None:
        pages = sequence(args.pages)
        records.extend({**page, "kind": "page"} for page in data["pages"] if page["page"] in pages)
    else:
        tables = [table for table in data["tables"] if args.table is None or table["id"] == args.table]
        if args.table is not None and not tables:
            raise ValueError(f"Table not found: {args.table}")
        for table in tables:
            columns = list(range(1, table["cols"] + 1))
            if args.columns is not None:
                limits = args.columns.split(":")
                if len(limits) > 2 or any(not re.fullmatch(r"[A-Za-z]{1,3}", item) for item in limits):
                    raise ValueError("Choose one column or an inclusive column range")
                first, last = col_number(limits[0].upper()), col_number(limits[-1].upper())
                if not 1 <= first <= last <= table["cols"]:
                    raise ValueError("Column selection is outside the table")
                columns = list(range(first, last + 1))
            window = table_window(table, sequence(args.rows), columns)
            records.append({"kind": "table", **{key: value for key, value in window.items() if key not in {"merges", "cells", "aliases"}}})
            records.extend({"kind": "merge", "table": table["id"], "range": span} for span in window["merges"])
            records.extend({"kind": "alias", "table": table["id"], "cell": cell, "anchor": anchor} for cell, anchor in window["aliases"].items())
            records.extend({"kind": "cell", "table": table["id"], "cell": cell, **item} for cell, item in window["cells"].items())

    # Stable fragments make even one unusually large cell or PDF page readable
    # within a tool response. Offsets count these display records, never rows.
    # Joining all parts reconstructs the exact original JSON record.
    lines: list[str] = []
    for record_id, record in enumerate(records):
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        if len(line.encode("utf-8")) <= 2048:
            lines.append(line)
            continue
        parts = [line[start : start + 256] for start in range(0, len(line), 256)]
        lines.extend(
            json.dumps({"kind": "json_fragment", "record_id": record_id, "part": part, "parts": len(parts), "text": text}, ensure_ascii=False, separators=(",", ":")) for part, text in enumerate(parts)
        )
    if args.offset > len(lines):
        raise ValueError("View offset is outside the selected records")
    header = {
        "kind": "view",
        "document_id": args.document,
        "source": data["source"],
        "selection": {"table": args.table, "rows": args.rows, "columns": args.columns, "pages": args.pages},
        "offset": args.offset,
        "total_records": len(lines),
    }
    used = max(len(json.dumps({**header, "next_offset": end}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1 for end in (None, len(lines)))
    if used > args.max_bytes:
        raise ValueError("View metadata exceeds max-bytes; narrow the selection or increase max-bytes")
    selected = []
    for line in lines[args.offset :]:
        size = len(line.encode("utf-8")) + 1
        if used + size > args.max_bytes:
            break
        selected.append(line)
        used += size
    if not selected and args.offset < len(lines):
        raise ValueError("View record exceeds remaining max-bytes; increase max-bytes")
    end = args.offset + len(selected)
    header["next_offset"] = end if end < len(lines) else None
    print(json.dumps(header, ensure_ascii=False, separators=(",", ":")))
    for line in selected:
        print(line)


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
    view.add_argument("--offset", type=int, default=0, help="Display record offset from the previous page's next_offset")
    view.add_argument("--max-bytes", type=int, default=16000, help="Maximum UTF-8 stdout bytes including page metadata (minimum 4096)")
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
        _print_view(data, args)
    elif args.command == "bind":
        from audit.bindings import Binding

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
