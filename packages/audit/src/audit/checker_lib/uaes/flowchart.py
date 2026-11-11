"""UAES process flow charts read from the original cells, cell formats and placed drawings.

A flow chart table has a step column and an operation description column; the
activity symbols sit in the lanes between them. The legend is either the lane
headers with their symbol underneath, or note cells after the operations that
pair one symbol with its name.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from audit.document_tools import ref

from .native import MissingFact, NativeFacts, UnknownFact, blank, header

SYMBOLS = "◇○△□▽"
_STEP = ("工序号", "步骤", "Step", "STP")
_OPERATION = ("过程描述", "工序描述", "工序名称", "Operation description", "Process description")
_IDENTIFICATION = ("标识", "关键工序", "Identification", "Key operation")
# Key operation marks named in the rule remarks: K, K1…, ★, ◆.
_KEY_MARK = re.compile(r"(?<![A-Za-z])K\d*(?![A-Za-z])|[★◆☆]")
_CONNECTORS = {"line", "straightConnector1", *(f"{shape}Connector{number}" for shape in ("bent", "curved") for number in range(2, 6))}
_DASHED = {"dashed", "dotted", "dashDot", "dashDotDot", "mediumDashed", "mediumDashDot", "mediumDashDotDot", "slantDashDot", "hair"}


@dataclass
class Operation:
    rows: list[int]
    step: str
    description: str
    location: tuple[int, int]
    marks: list[tuple[int, str]] = field(default_factory=list)
    unread_lane: bool = False

    @property
    def symbols(self):
        return [symbol for _column, symbol in self.marks]


class FlowChart:
    def __init__(self, facts: NativeFacts):
        self.facts = facts
        found = []
        for table in facts.tables():
            try:
                row, columns = facts.columns(table, {"step": _STEP, "operation": _OPERATION})
            except (UnknownFact, MissingFact):
                continue
            found.append((table, row, columns))
        if len(found) != 1:
            raise UnknownFact("未能确定唯一的过程流程图表格。")
        self.table, self.header, columns = found[0]
        self.step, self.operation = columns["step"], columns["operation"]
        if self.operation - self.step < 2:
            raise UnknownFact("过程流程图在步骤与过程描述之间没有符号列。")
        self.lanes = range(self.step + 1, self.operation)
        self.reader = facts.readers[self.table["id"]]
        self.operations = self._operations()
        if not self.operations:
            raise UnknownFact("过程流程图没有可确认的工序行。")

    def value(self, row, column):
        return self.facts.cell(self.table, row, column)

    def _operations(self):
        operations: list[Operation] = []
        # The step and description headings may be merged over the symbol legend row.
        first = 1 + max(int(re.sub(r"^[A-Z]+", "", str(self.reader.cell(self.header, column).get("merge") or ref(self.header, column)).split(":")[-1])) for column in (self.step, self.operation))
        for row in range(first, self.table["rows"] + 1):
            step = self.reader.cell(row, self.step)
            # Notes and revision rows follow the last numbered step.
            if step.get("state") == "known" and not blank(step.get("value")) and not re.fullmatch(r"[\d.\s/、,，-]+", str(step["value"])):
                break
            item = self.reader.cell(row, self.operation)
            anchor = item.get("anchor", ref(row, self.operation))
            if item.get("state") != "known":
                # Without a step number and outside the previous description, the row is not an operation.
                if step.get("state") == "known" and blank(step.get("value")) and not (operations and operations[-1].location == anchor):
                    continue
                raise UnknownFact(f"过程流程图第{row}行过程描述无法读取。")
            if blank(item.get("value")):
                continue
            if operations and operations[-1].location == anchor:
                operations[-1].rows.append(row)
            else:
                operations.append(Operation([row], str(self.value(row, self.step) or "").strip(), str(self.value(row, self.operation)).strip(), anchor))
        for operation in operations:
            seen = set()
            for row in operation.rows:
                for column in self.lanes:
                    item = self.reader.cell(row, column)
                    anchor = item.get("anchor", ref(row, column))
                    if anchor in seen:
                        continue
                    seen.add(anchor)
                    if item.get("state") != "known":
                        # A spreadsheet cell covered only by connector lines holds no hidden symbol.
                        operation.unread_lane |= self.facts.format == ".pdf" or self._drawings_in((row, column, row, column))
                        continue
                    text = "".join(str(item.get("value") or "").split())
                    if text and set(text) <= set(SYMBOLS):
                        self.value(row, column)
                        operation.marks.extend((column, symbol) for symbol in text)
        return operations

    def operation_rows(self):
        return {row for operation in self.operations for row in operation.rows}

    def legend(self):
        """Symbol -> activity name, from the lane headers or the notes after the operations."""
        legend: dict[str, str] = {}

        def define(symbol, name):
            if legend.get(symbol, name) != name:
                raise UnknownFact(f"图例中符号“{symbol}”有多个含义。")
            legend[symbol] = name

        # A header legend sits between the lane headings and the first operation.
        for column in self.lanes if self.header + 1 < min(self.operation_rows()) else ():
            name, below = self.reader.cell(self.header, column).get("value"), str(self.reader.cell(self.header + 1, column).get("value") or "").strip()
            if not blank(name) and len(below) == 1 and below in SYMBOLS:
                self.value(self.header, column), self.value(self.header + 1, column)
                define(below, str(name).strip())
        last = max(self.operation_rows())
        for address, item in self.table["cells"].items():
            row = int(re.sub(r"^[A-Z]+", "", address))
            if row <= last or item.get("state") != "known" or item.get("anchor", address) != address:
                continue
            text = str(item.get("value") or "")
            marks = [char for char in text if char in SYMBOLS]
            if len(marks) == 1 and re.search(r"[一-鿿A-Za-z]", text):
                self.facts.witness(self.facts.cell_location(self.table, row, column_of(address), item), text, self.facts.version)
                define(marks[0], " ".join(text.replace(marks[0], " ").split()))
        return legend

    def typed_lanes(self):
        """Lane columns that each carry their own activity heading."""
        lanes = {}
        for column in self.lanes:
            item = self.reader.cell(self.header, column)
            if item.get("anchor", ref(self.header, column)) == ref(self.header, column) and not blank(item.get("value")):
                lanes[column] = str(item["value"]).strip()
        return lanes if len(lanes) == len(self.lanes) else {}

    def key_operation(self):
        """True for a key-operation mark; False when none of the rule's mark forms is present; None when only an image could tell."""
        columns = [self.step, *self.lanes, self.operation]
        columns += [column for column in range(1, self.table["cols"] + 1) if column not in columns and header(self.reader.cell(self.header, column).get("value"), _IDENTIFICATION)]
        rows = sorted(self.operation_rows())
        for row in rows:
            for column in columns:
                item = self.reader.cell(row, column)
                if item.get("state") == "known" and _KEY_MARK.search(str(item.get("value") or "")):
                    self.value(row, column)
                    return True
        area = (rows[0], min(columns), rows[-1], max(columns))
        if self._drawings_in(area):
            return None
        distinguished = self._pdf_distinguished if self.facts.format == ".pdf" else self._distinguished
        return None if distinguished(rows, (self.step, self.operation)) else False

    def _drawings_in(self, area):
        """Pictures or shapes other than connector lines over the operation area."""
        top, left, bottom, right = area
        for visual in self.table.get("visual_evidence", []):
            place = visual.get("range") or visual.get("bounds")
            if place is None:
                return True
            if place[0] > bottom or place[2] < top or place[1] > right or place[3] < left:
                continue
            if visual.get("references") or not self._connector(visual["source"]):
                return True
        return False

    def _connector(self, source):
        with zipfile.ZipFile(self.facts.path) as archive:
            node = list(ET.fromstring(archive.read(source["part"])))[source["anchor_index"] - 1]
        shapes = {element.get("prst") for element in node.iter() if element.tag.endswith("}prstGeom")}
        return any(element.tag.endswith("}cxnSp") for element in node.iter()) or bool(shapes) and shapes <= _CONNECTORS

    def _distinguished(self, rows, columns):
        """Bold, a fill or a dashed border that sets some operations apart from the others."""
        from audit.checker_lib.excel import load_workbook

        sheet = load_workbook(self.facts.path)[self.table["id"]]
        styles = set()
        for row in rows:
            for column in columns:
                cell = sheet.cell(row, column)
                if any(getattr(getattr(cell.border, side), "style", None) in _DASHED for side in ("left", "right", "top", "bottom")):
                    return True
                styles.add((column, bool(cell.font and cell.font.b), _fill(cell)))
        # A column styled the same on every operation marks none of them.
        return len(styles) > len(columns)

    def _pdf_distinguished(self, rows, columns):
        """Any graphic inside an operation cell (a fill or box), or bold on some operations only."""
        import fitz

        tolerance = self.table.get("vector_boundary_tolerance", 0)

        def rule(path):
            # A solid straight stroke or hairline bar separates rows; it is not a fill or a box.
            solid = str(path.get("dashes") or "[] 0").startswith("[]")
            return solid and all(item[0] == "l" or item[0] == "re" and min(item[1].width, item[1].height) <= tolerance for item in path["items"])

        bold, seen, spans, drawings = set(), set(), {}, {}
        with fitz.open(self.facts.path) as document:
            for row in rows:
                for column in columns:
                    item = self.reader.cell(row, column)
                    anchor = item.get("anchor", ref(row, column))
                    if anchor in seen:
                        continue
                    seen.add(anchor)
                    source = item.get("source") or {}
                    if item.get("state") != "known" or item.get("image_overlap") or not source.get("page") or not source.get("bbox"):
                        return True
                    if source["page"] not in spans:
                        page = document[source["page"] - 1]
                        drawings[source["page"]] = page.get_drawings()
                        spans[source["page"]] = [span for block in page.get_text("dict")["blocks"] for line in block.get("lines", []) for span in line["spans"] if span["text"].strip()]
                    if not all(rule(drawings[source["page"]][index - 1]) for index in item.get("vector_path_indices", [])):
                        return True
                    inside = [span for span in spans[source["page"]] if fitz.Rect(span["bbox"]).intersects(fitz.Rect(source["bbox"]))]
                    bold.add((column, any(span["flags"] & 16 or "bold" in span["font"].lower() for span in inside)))
        return len(bold) > len(columns)


def column_of(address):
    from openpyxl.utils.cell import coordinate_to_tuple

    return coordinate_to_tuple(address)[1]


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
