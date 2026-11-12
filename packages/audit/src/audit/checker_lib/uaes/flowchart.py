"""UAES process flow charts read from the original cells, cell formats and placed drawings.

A flow chart table has a step column and an operation description column; the
activity symbols sit in the lanes between them. The legend is either the lane
headers with their symbol underneath, or note cells after the operations that
pair one symbol with its name.
"""

from __future__ import annotations

import re
import struct
import zipfile
from dataclasses import dataclass, field
from posixpath import dirname, join, normpath
from typing import Any
from xml.etree import ElementTree as ET

from audit.engine.rule_engine import Decision, RuleContext, Sheet, Table

from .excel import compact, unable
from .resources import flowchart as resources

_KEY_MARK = re.compile(resources.KEY_MARK)
_STEP_NUMBER = re.compile(r"[\d.\s/、,，-]+")
_CONNECTORS = {"line", "straightConnector1", *(f"{shape}Connector{number}" for shape in ("bent", "curved") for number in range(2, 6))}
# OfficeArt shape types of .xls drawings: line, straight, bent and curved connectors.
_XLS_CONNECTORS = {20, *range(32, 41)}
_DASHED = {"dashed", "dotted", "dashDot", "dashDotDot", "mediumDashed", "mediumDashDot", "mediumDashDotDot", "slantDashDot", "hair"}


@dataclass
class Operation:
    rows: list[int]
    step: str
    description: str
    marks: list[tuple[int, str]] = field(default_factory=list)
    unread_lane: bool = False

    @property
    def symbols(self):
        return [symbol for _column, symbol in self.marks]


@dataclass(frozen=True)
class Drawing:
    """A placed drawing: its cell range (first row, first column, last row, last column) when known."""

    place: tuple[int, int, int, int] | None
    connector: bool


class FlowChart:
    def __init__(self, ctx: RuleContext, path: str):
        self.ctx, self.path = ctx, path
        sheets = Table(ctx).find_sheets("过程流程图", on_missing=Decision.IGNORE)
        if len(sheets) != 1:
            found = "、".join(f"“{sheet.title}”" for sheet in sheets) or "没有"
            raise unable(f"检查程序未能确定唯一的过程流程图表格（同时有工序号/步骤与过程描述表头的工作表：{found}），需人工复核。")
        self.sheet: Sheet = sheets[0]
        try:
            section = self.sheet.section("流程区")
        except Exception:
            raise unable(f"工作表“{self.sheet.title}”中未能定位工序号与过程描述的表头，检查程序未能读取，需人工复核。") from None
        self.section = section
        self.header = section.resolved.header_row
        self.step, self.operation = section.column("工序号").header_col, section.column("过程描述").header_col
        if self.operation - self.step < 2:
            raise unable(f"过程流程图“{self.sheet.title}”在步骤与过程描述之间没有符号列，检查程序未能读取符号，需人工复核。")
        self.lanes = range(self.step + 1, self.operation)
        identification = ctx.optional_column(section.resolved, "标识")
        self.identification = [] if identification is None else [identification.header_col]
        self._drawings: list[Drawing] | None = None
        self.operations, self.end = self._operations()
        if not self.operations:
            raise unable(f"过程流程图“{self.sheet.title}”没有可确认的工序行，需人工复核。")

    @property
    def pdf(self) -> bool:
        return self.path.lower().endswith(".pdf")

    def merge(self, row: int, column: int):
        return self.ctx.ops.get_detailed_merge_info(self.sheet.ws, row, column)

    def anchor(self, row: int, column: int) -> tuple[int, int]:
        merged = self.merge(row, column)
        return (merged.min_row, merged.min_col) if merged else (row, column)

    def text(self, row: int, column: int) -> str:
        value = self.sheet.cell(row, column).value()
        return "" if value is None else str(value).strip()

    def _operations(self) -> tuple[list[Operation], int]:
        operations: list[Operation] = []
        last = self.header
        for row in self.section.rows("工序号", "过程描述"):
            index = row.row_index
            step = self.text(index, self.step)
            # Notes and sign-off rows follow the last numbered step.
            if step and not _STEP_NUMBER.fullmatch(step):
                break
            description = str(row.value("过程描述") or "").strip()
            if not description:
                continue
            merged = self.merge(index, self.operation)
            rows = list(range(index, (merged.max_row if merged else index) + 1))
            operations.append(Operation(rows, step, description))
            last = rows[-1]
        for operation in operations:
            seen = set()
            for line in operation.rows:
                for column in self.lanes:
                    anchor = self.anchor(line, column)
                    if anchor in seen:
                        continue
                    seen.add(anchor)
                    text = "".join(self.text(*anchor).split())
                    if text and set(text) <= set(resources.SYMBOLS):
                        operation.marks.extend((column, symbol) for symbol in text)
                    # A PDF lane holds drawn arrows the cells do not carry; a sheet cell covered
                    # by a picture or shape (not a connector line) may hide a symbol.
                    operation.unread_lane |= self.pdf or self.drawings_in((line, column, line, column))
        return operations, last

    def operation_rows(self) -> list[int]:
        return sorted(row for operation in self.operations for row in operation.rows)

    def legend(self) -> dict[str, str]:
        """Symbol -> activity name, from the lane headers or the notes after the operations."""
        legend: dict[str, str] = {}

        def define(symbol, name):
            if legend.get(symbol, name) != name:
                raise unable(f"过程流程图“{self.sheet.title}”的图例中符号“{symbol}”有多个含义（{legend[symbol]}、{name}），需人工复核。")
            legend[symbol] = name

        # A header legend sits between the lane headings and the first operation.
        if self.header + 1 < self.operation_rows()[0]:
            for column in self.lanes:
                name, below = self.text(self.header, column), self.text(self.header + 1, column)
                if name and len(below) == 1 and below in resources.SYMBOLS:
                    define(below, name)
        rows, columns = self.extent()
        for cell in self.sheet.scan_non_empty(row_min=self.end + 1, row_max=rows, col_min=1, col_max=columns):
            if self.anchor(cell.row_index, cell.col_index) != (cell.row_index, cell.col_index):
                continue
            text = str(cell.value())
            marks = [char for char in text if char in resources.SYMBOLS]
            if len(marks) == 1 and re.search(r"[一-鿿A-Za-z]", text):
                define(marks[0], " ".join(text.replace(marks[0], " ").split()))
        return legend

    def extent(self) -> tuple[int, int]:
        from .excel import UaesExcelAgent

        return UaesExcelAgent.extent(self.sheet)

    def typed_lanes(self) -> dict[int, str]:
        """Lane columns that each carry their own activity heading."""
        lanes = {}
        for column in self.lanes:
            if self.anchor(self.header, column) == (self.header, column) and (name := self.text(self.header, column)):
                lanes[column] = name
        return lanes if len(lanes) == len(self.lanes) else {}

    def key_operation(self) -> bool | None:
        """True for a key-operation mark; False when none of the rule's mark forms is present; None when only an image could tell."""
        columns = [self.step, *self.lanes, self.operation, *self.identification]
        rows = self.operation_rows()
        for row in rows:
            for column in columns:
                if _KEY_MARK.search(self.text(row, column)):
                    return True
        if self.pdf:
            return self._pdf_marks()
        if self.drawings_in((rows[0], min(columns), rows[-1], max(columns))):
            return None
        return None if self._distinguished(rows, (self.step, self.operation)) else False

    # Placed drawings of a spreadsheet original.

    def drawings_in(self, area: tuple[int, int, int, int]) -> bool:
        """Pictures or shapes other than connector lines over the area (rows and columns, inclusive)."""
        if self.pdf:
            return False
        top, left, bottom, right = area
        for drawing in self.drawings():
            if drawing.connector:
                continue
            place = drawing.place
            if place is None or not (place[0] > bottom or place[2] < top or place[1] > right or place[3] < left):
                return True
        return False

    def has_pictures(self) -> bool:
        """Whether the original carries any picture or shape the cells do not express."""
        if self.pdf:
            import fitz

            with fitz.open(self.path) as document:
                return any(page.get_image_info() or not page.get_text().strip() for page in document)
        return any(not drawing.connector for drawing in self.drawings())

    def drawings(self) -> list[Drawing]:
        if self._drawings is None:
            try:
                self._drawings = _xls_drawings(self.path, self.sheet.title) if self.path.lower().endswith(".xls") else _xlsx_drawings(self.path, self.sheet.title)
            except Exception:
                # Unparsed drawings could cover any cell.
                self._drawings = [Drawing(None, False)]
        return self._drawings

    def _distinguished(self, rows, columns) -> bool:
        """Bold, a fill or a dashed border that sets some operations apart from the others."""
        styles = set()
        for row in rows:
            for column in columns:
                cell = self.sheet.ws.cell(row, column)
                if any(getattr(getattr(cell.border, side), "style", None) in _DASHED for side in ("left", "right", "top", "bottom")):
                    return True
                styles.add((column, bool(cell.font and cell.font.b), _fill(cell)))
        # A column styled the same on every operation marks none of them.
        return len(styles) > len(columns)

    # The PDF page the chart was converted from.

    def _pdf_marks(self) -> bool | None:
        """False when the step and description columns carry no graphic, picture or partial bold; else None."""
        import fitz

        labels = [self.text(self.header, column).splitlines()[0] for column in (self.step, self.operation)]
        stop = next((cell for cell in self.sheet.scan_non_empty(row_min=self.end + 1, row_max=self.extent()[0], col_min=1, col_max=self.extent()[1])), None)
        if not all(labels) or stop is None:
            return None
        stop_label = str(stop.value()).strip().splitlines()[0]
        with fitz.open(self.path) as document:
            pages = [page for page in document if all(_lines(page, label) for label in labels)]
            if len(pages) != 1:
                return None
            page = pages[0]
            heads = [_lines(page, label) for label in labels]
            stops = _lines(page, stop_label)
            if any(len(found) != 1 for found in heads) or not stops:
                return None
            drawings = page.get_drawings()
            horizontal, vertical = _rules(drawings)
            head_bottom = max(found[0].y1 for found in heads)
            stop_top = min(rect.y0 for rect in stops if rect.y0 > head_bottom) if any(rect.y0 > head_bottom for rect in stops) else None
            if stop_top is None:
                return None
            # The operation band lies between the header row's bottom rule and the rule above the notes.
            tops = [rule.y1 for rule in horizontal if rule.y0 >= head_bottom - 0.5 and rule.y1 <= stop_top]
            bottoms = [rule.y0 for rule in horizontal if rule.y1 <= stop_top + 0.5 and rule.y0 >= head_bottom]
            if not tops or not bottoms:
                return None
            band = (min(tops), max(bottoms))
            if band[1] - band[0] < 1:
                return None
            images = [fitz.Rect(info["bbox"]) for info in page.get_image_info()]
            spans = [span for block in page.get_text("dict")["blocks"] for line in block.get("lines", []) for span in line["spans"] if span["text"].strip()]
            for head in heads:
                middle = (head[0].x0 + head[0].x1) / 2
                covering = [rule for rule in vertical if min(rule.y1, band[1]) - max(rule.y0, band[0]) >= (band[1] - band[0]) / 2]
                left = [rule.x1 for rule in covering if rule.x1 <= head[0].x0 + 0.5]
                right = [rule.x0 for rule in covering if rule.x0 >= head[0].x1 - 0.5]
                if not left or not right:
                    return None
                cell = fitz.Rect(max(left), band[0], min(right), band[1])
                if middle < cell.x0 or middle > cell.x1:
                    return None
                if any(_overlaps(image, cell) for image in images):
                    return None
                for drawing in drawings:
                    rect = fitz.Rect(drawing["rect"])
                    dashed = not str(drawing.get("dashes") or "[] 0").startswith("[]")
                    # A fill or box inside the cells, or any dashed stroke, can mark an operation.
                    if _overlaps(rect, cell) or dashed and rect.intersects(cell):
                        return None
                inside = [span for span in spans if cell.contains(fitz.Point((span["bbox"][0] + span["bbox"][2]) / 2, (span["bbox"][1] + span["bbox"][3]) / 2))]
                if len({bool(span["flags"] & 16 or "bold" in span["font"].lower()) for span in inside}) > 1:
                    return None
        return False


def _lines(page, label: str) -> list:
    """Rectangles of the text lines that start with the label."""
    import fitz

    wanted = compact(label)
    found = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = compact("".join(span["text"] for span in line["spans"]))
            if wanted and text.startswith(wanted):
                found.append(fitz.Rect(line["bbox"]))
    return found


def _rules(drawings) -> tuple[list, list]:
    """Thin solid horizontal and vertical strokes: the table's grid lines."""
    import fitz

    horizontal, vertical = [], []
    for drawing in drawings:
        if not str(drawing.get("dashes") or "[] 0").startswith("[]"):
            continue
        for item in drawing["items"]:
            if item[0] == "re":
                rect = fitz.Rect(item[1])
            elif item[0] == "l":
                rect = fitz.Rect(item[1], item[2]).normalize()
            else:
                continue
            if rect.height <= 1.5 and rect.width > 5:
                horizontal.append(rect)
            elif rect.width <= 1.5 and rect.height > 5:
                vertical.append(rect)
    return horizontal, vertical


def _overlaps(rect, cell) -> bool:
    """More than a grid line's width of overlap in both directions."""
    common = rect & cell
    return not common.is_empty and common.width > 1.5 and common.height > 1.5


def _xlsx_drawings(path: str, title: str) -> list[Drawing]:
    """Anchors of the sheet's drawing part: pictures, shapes and connectors."""
    namespaces = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
        "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    }

    def targets(archive, part):
        rels = join(dirname(part), "_rels", part.rsplit("/", 1)[-1] + ".rels")
        if rels not in archive.namelist():
            return {}
        return {item.get("Id"): (item.get("Type", ""), normpath(join(dirname(part), item.get("Target", "")))) for item in ET.fromstring(archive.read(rels)).findall("pkg:Relationship", namespaces)}

    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet_ids = {sheet.get("name"): sheet.get(f"{{{namespaces['rel']}}}id") for sheet in workbook.iter(f"{{{namespaces['main']}}}sheet")}
        sheet_part = targets(archive, "xl/workbook.xml")[sheet_ids[title]][1]
        found = []
        for kind, part in targets(archive, sheet_part).values():
            if not kind.endswith("/drawing"):
                continue
            for node in ET.fromstring(archive.read(part)):
                tag = node.tag.rsplit("}", 1)[-1]
                if tag not in {"twoCellAnchor", "oneCellAnchor", "absoluteAnchor"}:
                    continue
                place = None
                start, end = node.find("xdr:from", namespaces), node.find("xdr:to", namespaces)
                if tag == "twoCellAnchor" and start is not None and end is not None:
                    cells = [int(marker.findtext(f"xdr:{name}", "0", namespaces)) + 1 for marker in (start, end) for name in ("row", "col")]
                    place = (cells[0], cells[1], cells[2], cells[3])
                shapes = {element.get("prst") for element in node.iter() if element.tag.endswith("}prstGeom")}
                connector = any(element.tag.endswith("}cxnSp") for element in node.iter()) or bool(shapes) and shapes <= _CONNECTORS
                pictures = any(element.tag.endswith(("}pic", "}graphicFrame")) for element in node.iter())
                found.append(Drawing(place, connector and not pictures))
        return found


def _xls_drawings(path: str, title: str) -> list[Drawing]:
    """OfficeArt shapes of one BIFF8 sheet: their shape types and client anchors."""
    import xlrd
    from xlrd import compdoc

    with open(path, "rb") as handle:
        document = compdoc.CompDoc(handle.read(), logfile=open("/dev/null", "w"))
    stream = document.get_named_stream("Workbook") or document.get_named_stream("Book")
    names = xlrd.open_workbook(path, on_demand=True).sheet_names()
    records, position = [], 0
    while position + 4 <= len(stream):
        kind, size = struct.unpack("<HH", stream[position : position + 4])
        records.append((kind, stream[position + 4 : position + 4 + size]))
        position += 4 + size
    # Substreams in order: the workbook globals, then one per sheet; drawing data continues over records.
    sheet, depth, previous = -1, 0, None
    data: dict[Any, Any] = {}
    for kind, payload in records:
        if kind == 0x0809:
            # An embedded chart opens a substream inside its sheet's.
            sheet += depth == 0
            depth += 1
        elif kind == 0x000A:
            depth -= 1
        elif kind == 0x00EC or kind == 0x003C and previous == 0x00EC:
            data.setdefault(sheet - 1, bytearray()).extend(payload)
        if kind != 0x003C:
            previous = kind
    blob = bytes(data.get(names.index(title), b""))

    def parse(start, end):
        nodes = []
        while start + 8 <= end:
            header, kind, size = struct.unpack("<HHI", blob[start : start + 8])
            body = (start + 8, min(start + 8 + size, end))
            nodes.append((kind, header >> 4, parse(*body) if header & 0xF == 0xF else blob[body[0] : body[1]]))
            start = body[1]
        return nodes

    def walk(nodes):
        for kind, instance, body in nodes:
            yield kind, instance, body
            if isinstance(body, list):
                yield from walk(body)

    found = []
    for kind, _instance, body in walk(parse(0, len(blob))):
        if kind != 0xF003:
            continue
        # The shape group's children: its own group shape first, then each placed shape or nested group.
        for child_kind, _child_instance, child in body[1:]:
            if not isinstance(child, list):
                continue
            items = list(walk(child)) if child_kind == 0xF003 else list(walk([(child_kind, 0, child)]))
            types = {instance for item_kind, instance, _ in items if item_kind == 0xF00A} - {0}
            anchor = next((payload for item_kind, _, payload in items if item_kind == 0xF010 and len(payload) >= 18), None)
            place = None
            if anchor is not None:
                _flag, left, _dx, top, _dy, right, _dx2, bottom, _dy2 = struct.unpack("<9H", anchor[:18])
                place = (top + 1, left + 1, bottom + 1, right + 1)
            found.append(Drawing(place, bool(types) and types <= _XLS_CONNECTORS))
        break
    return found


def carries_pictures(path: str) -> bool:
    """Whether a spreadsheet original stores any picture: in its media, its picture store or its page headers."""
    if not path.lower().endswith(".xls"):
        with zipfile.ZipFile(path) as archive:
            return any(name.startswith(("xl/media/", "xl/embeddings/", "xl/charts/")) for name in archive.namelist())
    from xlrd import compdoc

    with open(path, "rb") as handle:
        document = compdoc.CompDoc(handle.read(), logfile=open("/dev/null", "w"))
    stream = document.get_named_stream("Workbook") or document.get_named_stream("Book")
    blob, position, previous = bytearray(), 0, None
    while position + 4 <= len(stream):
        kind, size = struct.unpack("<HH", stream[position : position + 4])
        payload = stream[position + 4 : position + 4 + size]
        position += 4 + size
        # A header/footer picture (HFPicture).
        if kind == 0x0866:
            return True
        if kind == 0x00EB or kind == 0x003C and previous == 0x00EB:
            blob.extend(payload)
        if kind != 0x003C:
            previous = kind

    def pictures(start, end):
        while start + 8 <= end:
            header, kind, size = struct.unpack("<HHI", blob[start : start + 8])
            # A BLIP store entry (OfficeArtFBSE) is one stored picture.
            if kind == 0xF007 or header & 0xF == 0xF and pictures(start + 8, min(start + 8 + size, end)):
                return True
            start += 8 + size
        return False

    return pictures(0, len(blob))


def _fill(cell):
    """A visible background colour, or None for no fill and the white page colour."""
    fill = cell.fill
    if fill is None or not fill.fill_type:
        return None
    color = fill.fgColor
    if color.type == "rgb":
        return None if str(color.rgb)[-6:].upper() == "FFFFFF" else str(color.rgb)[-6:].upper()
    if color.type == "theme":
        # Theme colour 0 is the background (white) colour of the workbook theme.
        return None if color.theme == 0 and not color.tint else ("theme", color.theme, color.tint)
    if color.type == "indexed":
        return None if color.indexed in {9, 64} else ("indexed", color.indexed)
    return (color.type, getattr(color, color.type, None))
