"""UAES part submission warrant (PSW).

A scanned warrant (CFG-1001 with some typed overlays) holds its fields, boxes and signature only as an image:
its one page is transcribed once into a fixed JSON schema by the image model, including whether an ink seal is seen.
A binary Excel warrant (F-PU-206) is read into the same schema from its cells and form checkboxes; only whether
a picture on it is a handwritten signature or a seal is asked of the image model. Every clause decides in code.
A text-layer or other warrant, whose layout this checker has not declared, is not guessed at.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.engine.rule_engine import GuardStop, RuleContext

from .excel import UaesExcelAgent, compact, unable
from .resources import combine
from .resources import warrant as resources

_OTHER = ("other", "其它", "其他")


class _Reading:
    """One clause's decision over the transcription: definite violations, and what could not be read."""

    def __init__(self, agent: UaesWarrantAgent):
        self.agent, self.facts = agent, agent.transcription()
        self.failed: list[str] = []
        self.unknown: list[str] = []

    def present(self, name: str, label: str) -> bool:
        present = (self.facts.get(name) or {}).get("present")
        if present is False:
            self.failed.append(f"缺少{label}")
        elif present is None:
            self.unknown.append(label)
        return present is True

    def field(self, name: str, label: str) -> str | None:
        if not self.present(name, label):
            return None
        value = self.facts[name].get("value")
        if value is None:
            self.unknown.append(label)
            return None
        if not value.strip():
            self.failed.append(f"{label}为空")
            return None
        self.agent.note(name, value)
        return value

    def choice(self, name: str, label: str) -> list[dict[str, Any]] | None:
        """The checked options of a choice; None when the choice or any of its marks cannot be read."""
        if not self.present(name, label):
            return None
        options = self.facts[name].get("options") or []
        if not options or any(option.get("checked") is None for option in options):
            self.unknown.append(f"{label}的勾选")
            return None
        checked = [option for option in options if option["checked"] is True]
        self.agent.note(name, "、".join(str(option.get("label") or "") for option in checked) or "未勾选")
        return checked

    def one_of(self, name: str, label: str) -> None:
        checked = self.choice(name, label)
        if checked is not None and len(checked) != 1:
            self.failed.append(f"{label}{'未勾选' if not checked else '勾选了多项'}")

    def decided(self, passed: str) -> tuple[str, str]:
        if self.failed:
            return "NG", "零件提交保证书" + "；".join(self.failed) + "。"
        if self.unknown:
            raise unable(f"未能从零件提交保证书原件辨识{'、'.join(self.unknown)}，需人工复核。")
        return "PASS", passed


@dataclass
class _Shape:
    """A drawing object on a worksheet: its BIFF object type, one-based cell anchor, checkbox state and caption, picture bytes."""

    kind: int
    anchor: tuple[int, int, int, int]
    checked: bool | None = None
    caption: str = ""
    picture: bytes | None = None


_CHECKBOX, _PICTURE = 0x0B, 0x08


def _records(stream: bytes):
    position = 0
    while position + 4 <= len(stream):
        kind, size = struct.unpack_from("<HH", stream, position)
        yield position, kind, stream[position + 4 : position + 4 + size]
        position += 4 + size


def _art(blob: bytes, start: int, end: int):
    """OfficeArt records in blob[start:end]: (instance, type, payload start, payload end); containers are not entered."""
    while start + 8 <= end:
        header, kind, size = struct.unpack_from("<HHI", blob, start)
        yield header >> 4, kind, start + 8, min(start + 8 + size, end), header & 0xF == 0xF
        start += 8 + size


def _blips(blob: bytes) -> list[bytes | None]:
    """The picture store of a workbook, in store order (pictures are referenced by one-based index); None when not embedded."""
    found: list[bytes | None] = []

    def walk(start: int, end: int) -> None:
        for _instance, kind, begin, finish, container in _art(blob, start, end):
            if container:
                walk(begin, finish)
            elif kind == 0xF007 and finish - begin >= 36:
                name = blob[begin + 33]
                embedded = next(iter(_art(blob, begin + 36 + name, finish)), None)
                if embedded is None or embedded[1] not in (0xF01D, 0xF01E):  # JPEG, PNG
                    found.append(None)
                else:
                    instance, _kind, data, stop, _ = embedded
                    found.append(bytes(blob[data + 16 * (1 + (instance & 1)) + 1 : stop]))

    walk(0, len(blob))
    return found


def _sheet_shapes(path: str, sheet: str) -> list[_Shape]:
    """Every drawing object of one worksheet of a BIFF8 workbook, in drawing order."""
    import olefile

    with olefile.OleFileIO(path) as document:
        stream = document.openstream("Workbook" if document.exists("Workbook") else "Book").read()
    names, group, previous = {}, bytearray(), None
    drawing, objects, current = bytearray(), [], None
    for position, kind, data in _records(stream):
        if kind == 0x0085:  # BOUNDSHEET: the sheet's BOF offset and its name
            length, wide = data[6], data[7]
            names[struct.unpack_from("<I", data)[0]] = data[8 : 8 + length * (2 if wide else 1)].decode("utf-16-le" if wide else "latin-1")
        elif kind == 0x00EB or kind == 0x003C and previous == 0x00EB:  # MSODRAWINGGROUP and its continuation
            group.extend(data)
        elif kind == 0x0809:  # BOF
            current = names.get(position)
        elif kind == 0x000A:  # EOF
            current = None
        elif current == sheet and kind == 0x00EC:  # MSODRAWING
            drawing.extend(data)
        elif current == sheet and kind == 0x005D:  # OBJ: its subrecords, the checkbox state among them
            subrecords, offset = {}, 0
            while offset + 4 <= len(data):
                code, size = struct.unpack_from("<HH", data, offset)
                subrecords[code] = data[offset + 4 : offset + 4 + size]
                offset += 4 + size
                if code in (0x0000, 0x0013):
                    break
            state = struct.unpack_from("<H", subrecords[0x0012])[0] if len(subrecords.get(0x0012, b"")) >= 2 else None
            objects.append([struct.unpack_from("<H", data, 4)[0], {0: False, 1: True}.get(state) if state is not None else None, ""])
        elif current == sheet and kind == 0x003C and previous == 0x01B6 and objects and data:  # TXO's first continuation: the last object's caption
            objects[-1][2] = data[1:].decode("utf-16-le" if data[0] else "latin-1", "replace").strip()
        # A TXO's text is its first continuation only; its formatting runs follow.
        previous = None if kind == 0x003C and previous == 0x01B6 else previous if kind == 0x003C else kind
    store = _blips(bytes(group))
    shapes: list[tuple[tuple[int, int, int, int] | None, int | None]] = []

    def walk(start: int, end: int) -> None:
        for _instance, kind, begin, finish, container in _art(bytes(drawing), start, end):
            if kind == 0xF004:  # one shape
                anchor, picture, client = None, None, False
                for _i, part, data, stop, inner in _art(bytes(drawing), begin, finish):
                    if inner:
                        walk(data, stop)
                    elif part == 0xF010 and stop - data >= 18:
                        _flag, left, _dx, top, _dy, right, _dx2, bottom, _dy2 = struct.unpack_from("<9H", drawing, data)
                        anchor = (top + 1, left + 1, bottom + 1, right + 1)
                    elif part == 0xF00B:
                        for index in range(_i):
                            code, value = struct.unpack_from("<HI", drawing, data + 6 * index)
                            if code & 0x3FFF == 0x0104:
                                picture = value
                    elif part == 0xF011:
                        client = True
                if client:
                    shapes.append((anchor, picture))
            elif container:
                walk(begin, finish)

    walk(0, len(drawing))
    if len(shapes) != len(objects) or any(anchor is None for anchor, _ in shapes):
        raise ValueError("drawing objects and their records do not pair")
    return [
        _Shape(kind, anchor, checked, caption, store[picture - 1] if kind == _PICTURE and picture and picture <= len(store) else None)
        for (anchor, picture), (kind, checked, caption) in zip(shapes, objects)
        if anchor is not None
    ]


def _cell_name(row: int, column: int) -> str:
    from openpyxl.utils import get_column_letter

    return f"{get_column_letter(column)}{row}"


class _XlsWarrant:
    """The F-PU-206 warrant sheet of a binary Excel original: its cells, checkboxes and pictures, one-based."""

    def __init__(self, path: str):
        import xlrd

        book = xlrd.open_workbook(path, formatting_info=True)
        found = [sheet for sheet in book.sheets() if self._find(sheet, resources.XLS_FIELDS["part_name"][0])]
        if len(found) != 1:
            raise unable("零件提交保证书工作簿中" + ("未找到" if not found else "有多张") + "保证书工作表，检查程序未能读取，需人工复核。")
        self.sheet, self.datemode = found[0], book.datemode
        self.shapes = _sheet_shapes(path, self.sheet.name)
        labels = [words for words, _where in resources.XLS_FIELDS.values()] + [resources.XLS_DATE, resources.XLS_SIGNATURE]
        labels += [*resources.XLS_AREAS.values(), *(words for words, _end in resources.XLS_CHOICES.values())]
        self.labels = {cell for words in labels for cell in self._find(self.sheet, words)}

    @staticmethod
    def _find(sheet: Any, words: tuple[str, ...]) -> list[tuple[int, int]]:
        wanted = [compact(word) for word in words]
        return [(row + 1, column + 1) for row in range(sheet.nrows) for column in range(sheet.ncols) if (text := compact(sheet.cell_value(row, column))) and all(word in text for word in wanted)]

    def find(self, words: tuple[str, ...]) -> list[tuple[int, int]]:
        return self._find(self.sheet, words)

    def text(self, row: int, column: int) -> str:
        import xlrd

        if not (1 <= row <= self.sheet.nrows and 1 <= column <= self.sheet.ncols):
            return ""
        cell = self.sheet.cell(row - 1, column - 1)
        if cell.ctype == xlrd.XL_CELL_DATE:
            return xlrd.xldate_as_datetime(cell.value, self.datemode).date().isoformat()
        if cell.ctype == xlrd.XL_CELL_NUMBER:
            return f"{cell.value:g}" if cell.value != int(cell.value) else str(int(cell.value))
        return str(cell.value).strip()

    def right(self, row: int, column: int) -> tuple[str, tuple[int, int]]:
        """What is written right of a label on its row, up to the next label; and where (the label itself when blank)."""
        cells = []
        for next_column in range(column + 1, self.sheet.ncols + 1):
            if (row, next_column) in self.labels:
                break
            if self.text(row, next_column):
                cells.append(next_column)
        return " ".join(self.text(row, cell) for cell in cells), (row, cells[0] if cells else column)

    def first_right(self, row: int, column: int) -> tuple[str, tuple[int, int]]:
        """The first text right of a cell on its row: the printed caption of a checkbox placed there."""
        for next_column in range(column + 1, self.sheet.ncols + 1):
            if self.text(row, next_column):
                return self.text(row, next_column), (row, next_column)
        return "", (row, column)

    def above(self, row: int, column: int) -> tuple[str, tuple[int, int]]:
        """The value written in the cell above a label (its merged area's first cell)."""
        for top, bottom, left, right in self.sheet.merged_cells:
            if top < row - 1 <= bottom - 1 and left < column <= right:
                return self.text(top + 1, left + 1), (top + 1, left + 1)
        return self.text(row - 1, column), (row - 1, column)


class UaesWarrantAgent(UaesExcelAgent):
    DOCUMENT_MODULE = resources
    EXTRA_RESOURCE = combine(resources.DOCUMENT)
    # Nothing on a warrant is read as a table, so the original is not converted; a layout the
    # warrant reader has not declared (any DOCX among them) is UNABLE in its transcription.
    SUPPORTED_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".docx", ".pdf"})
    TABLES = False

    def transcription(self) -> dict[str, Any]:
        """The warrant as the warrant schema, read once for all clauses."""
        if "_transcription" not in self.__dict__:
            try:
                self._transcription: Any = self._transcribe()
            except GuardStop as stop:
                self._transcription = stop
        if isinstance(self._transcription, GuardStop):
            raise self._transcription
        return self._transcription

    def _transcribe(self) -> dict[str, Any]:
        path = str(self.audit_file.file_url)
        self._digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if path.lower().endswith(".xls"):
            return self._read_xls(path)
        if not path.lower().endswith(".pdf"):
            raise unable("检查程序尚未声明此格式零件提交保证书的读取方式，需人工复核。")
        if not self.unread_pages():
            raise unable("零件提交保证书是文字版PDF，检查程序尚未声明此版式的字段读取方式，需人工复核。")
        import fitz

        with fitz.open(path) as document:
            if len(document) != 1:
                raise unable(f"零件提交保证书有{len(document)}页，检查程序尚未声明多页保证书的读取范围，需人工复核。")
            image = document[0].get_pixmap(dpi=200).tobytes("png")
        return self.transcribe(image, resources.PROMPT, resources.SCHEMA, "零件提交保证书")

    def _read_xls(self, path: str) -> dict[str, Any]:
        """The Excel warrant's cells and checkboxes in the warrant schema; where each fact was read is kept as its evidence."""
        try:
            sheet = _XlsWarrant(path)
        except GuardStop:
            raise
        except Exception:
            raise unable("零件提交保证书工作簿未能完整读取，需人工复核。") from None
        self._sheet = sheet
        self._where: dict[str, tuple[dict[str, Any], bool]] = {}
        facts: dict[str, Any] = {}

        def place(name: str, start: tuple[int, int], end: tuple[int, int] | None = None, *, visual: bool = False) -> None:
            locator = {"kind": "xlsx_cell", "sheet": sheet.sheet.name, "cell": _cell_name(*start)}
            if end is not None and end != start:
                locator = {"kind": "xlsx_range", "sheet": sheet.sheet.name, "start": _cell_name(*start), "end": _cell_name(*end)}
            self._where[name] = (locator, visual)

        def one(words: tuple[str, ...]) -> tuple[bool | None, tuple[int, int] | None]:
            cells = sheet.find(words)
            return (True, cells[0]) if len(cells) == 1 else (None if cells else False, None)

        labels: dict[str, tuple[int, int]] = {}
        for name, (words, where) in resources.XLS_FIELDS.items():
            present, cell = one(words)
            facts[name] = {"present": present, "value": None}
            if cell is not None:
                labels[name] = cell
                facts[name]["value"], at = (sheet.right if where == "right" else sheet.above)(*cell)
                place(name, at)
        for name, field in resources.XLS_DATES.items():
            # A field's date is the date label on the field's own row, right of the field's label.
            owner = labels.get(field)
            dates = [cell for cell in sheet.find(resources.XLS_DATE) if owner is not None and cell[0] == owner[0] and cell[1] > owner[1]]
            facts[name] = {"present": True if len(dates) == 1 else (None if dates or owner is None else False), "value": None}
            if len(dates) == 1:
                facts[name]["value"], at = sheet.right(*dates[0])
                place(name, at)
        for name, words in resources.XLS_AREAS.items():
            facts[name] = {"present": one(words)[0]}
        for name, (words, until) in resources.XLS_CHOICES.items():
            present, cell = one(words)
            facts[name] = {"present": present, "options": []}
            if cell is None:
                continue
            end = cell[0] + 1
            if until is not None:
                ends = [found for found in sheet.find(until) if found[0] > cell[0]]
                if len(ends) != 1:
                    facts[name]["present"] = None
                    continue
                end = ends[0][0]
            boxes = sorted((shape for shape in sheet.shapes if shape.kind == _CHECKBOX and cell[0] <= shape.anchor[0] < end), key=lambda shape: shape.anchor[:2])
            for box in boxes:
                label, at = (box.caption, (box.anchor[0], box.anchor[1])) if box.caption else sheet.first_right(box.anchor[0], box.anchor[1])
                facts[name]["options"].append({"label": label, "checked": box.checked, "cell": at})
            if boxes:
                place(name, cell, (end - 1, max(box.anchor[3] for box in boxes)))
        # "其它----请说明" is explained right of its option's label on the same row.
        other = next((option for option in facts[resources.REASON[0]]["options"] if any(word in compact(option["label"]) for word in _OTHER)), None)
        facts[resources.REASON_OTHER[0]] = {"present": None if other is None else True, "value": None}
        if other is not None:
            facts[resources.REASON_OTHER[0]]["value"], at = sheet.right(*other["cell"])
            place(resources.REASON_OTHER[0], at)
        present, cell = one(resources.XLS_SIGNATURE)
        facts[resources.SIGNATURE[0]] = {"present": present, "handwritten": None, "value": sheet.right(*cell)[0] if cell else None}
        self._signature_cell = cell
        if cell is not None:
            place(resources.SIGNATURE[0], cell)
        return facts

    def _picture(self, shape: _Shape) -> dict[str, Any]:
        """What one embedded picture shows, asked once of the image model."""
        cache = self.__dict__.setdefault("_pictures", {})
        if id(shape) not in cache:
            if shape.picture is None:
                cache[id(shape)] = {"handwritten_signature": None, "ink_seal": None}
            else:
                from PIL import Image

                output = BytesIO()
                with Image.open(BytesIO(shape.picture)) as image:
                    image.convert("RGB").save(output, format="PNG")
                cache[id(shape)] = self.transcribe(output.getvalue(), resources.PICTURE_PROMPT, resources.PICTURE_SCHEMA, "零件提交保证书上的图片")
        return cache[id(shape)]

    @staticmethod
    def _any(values: list[bool | None]) -> bool | None:
        return True if True in values else (False if all(value is False for value in values) else None)

    def signature(self) -> dict[str, Any]:
        """The supplier's signature field: whether it is printed, whether it is signed by hand, and its visible text."""
        field = dict(self.transcription().get(resources.SIGNATURE[0]) or {})
        if not hasattr(self, "_sheet") or self._signature_cell is None:
            return field
        row, column = self._signature_cell
        # A signature on an Excel warrant is a picture placed on the signature line, right of its label.
        candidates = [shape for shape in self._sheet.shapes if shape.kind == _PICTURE and shape.anchor[0] <= row + 1 and shape.anchor[2] >= row - 1 and shape.anchor[3] > column]
        signed = [shape for shape in candidates if self._picture(shape)["handwritten_signature"] is True]
        field["handwritten"] = True if signed else self._any([self._picture(shape)["handwritten_signature"] for shape in candidates])
        if signed:
            self._where[resources.SIGNATURE[0]] = (self._range(signed[0]), True)
        return field

    def seal(self) -> bool | None:
        """Whether a seal is seen on the warrant."""
        if not hasattr(self, "_sheet"):
            return self.transcription().get(resources.SEAL[0])
        pictures = [shape for shape in self._sheet.shapes if shape.kind == _PICTURE]
        seals = [shape for shape in pictures if self._picture(shape)["ink_seal"] is True]
        if seals:
            self._where[resources.SEAL[0]] = (self._range(seals[0]), True)
        return True if seals else self._any([self._picture(shape)["ink_seal"] for shape in pictures])

    def _range(self, shape: _Shape) -> dict[str, Any]:
        top, left, bottom, right = shape.anchor
        return {"kind": "xlsx_range", "sheet": self._sheet.sheet.name, "start": _cell_name(top, left), "end": _cell_name(bottom, right)}

    def note(self, fact: str, value: str) -> None:
        """A read value is evidence of where it was read: the cell or picture of an Excel warrant, else page 1 of the scan."""
        if hasattr(self, "_sheet"):
            locator, visual = self._where[fact]
            self.legacy().witness(locator, value, visual=visual)
            return
        locator = {"kind": "pdf_page", "page": 1, "source_sha256": self._digest, "engine": "ocr_llm", "fact": fact}
        self.legacy().witness(locator, value, visual=True)

    @rule("PSW基础身份两个工程日期及法规唯一选择")
    def rule_1(self, ctx: RuleContext):
        reading = _Reading(self)
        for name, label in resources.IDENTITY.items():
            reading.field(name, label)
        reading.one_of(*resources.SAFETY)
        return reading.decided("零件身份、两个工程更改及其日期均已填写，安全及政府条例恰好勾选一项。")

    @rule("供方和买方资料材料申报及IMDS")
    def rule_2(self, ctx: RuleContext):
        reading = _Reading(self)
        if reading.present(*resources.SUPPLIER_AREA):
            reading.field(*resources.SUPPLIER_NAME)
        if reading.present(*resources.CUSTOMER_AREA):
            for name, label in resources.CUSTOMER.items():
                reading.field(name, label)
        reading.one_of(*resources.SUBSTANCES)
        reading.field(*resources.IMDS)
        return reading.decided("供方公司名称、买方三项资料、材料信息唯一勾选与IMDS均已填写。")

    @rule("供方代表手写签字和真实印章")
    def rule_3(self, ctx: RuleContext):
        reading = _Reading(self)
        name, label = resources.SIGNATURE
        if (self.transcription().get(name) or {}).get("present") is False:
            return "NG", "零件提交保证书中缺少供方代表签字字段"
        if reading.present(name, label):
            signature = self.signature()
            handwritten = signature.get("handwritten")
            if handwritten is False:
                self.note(name, signature.get("value") or "未签名")
                reading.failed.append(f"{label}没有手写签名")
            elif handwritten is None:
                reading.unknown.append(f"{label}是否手写")
            else:
                self.note(name, signature.get("value") or "手写签名")
        if reading.failed or reading.unknown:
            return reading.decided("")
        seen = self.seal()
        if seen is True:
            self.note(resources.SEAL[0], "印章")
            return "PASS", "零件提交保证书有供方代表手写签字，原件上有印章。"
        if seen is False:
            # The image model finds no seal: the clause fails (product owner).
            return "NG", "零件提交保证书有供方代表手写签字，但原件上未找到印章，判不通过，请人工复核。"
        raise unable("零件提交保证书有供方代表手写签字；未能辨识原件上是否有印章，需人工复核。")

    @rule("提交原因选择与其它说明")
    def rule_4(self, ctx: RuleContext):
        reading = _Reading(self)
        name, label = resources.REASON
        if (self.transcription().get(name) or {}).get("present") is False:
            return "NG", "缺少提交原因"
        checked = reading.choice(name, label)
        if checked is not None:
            if not checked:
                return "NG", "请勾选提交原因"
            if any(word in str(option.get("label") or "").casefold() for option in checked for word in _OTHER):
                reading.field(*resources.REASON_OTHER)
        return reading.decided("提交原因已勾选；勾选“其它”时其说明已填写。")

    @rule("提交等级唯一实际选择")
    def rule_5(self, ctx: RuleContext):
        reading = _Reading(self)
        name, _label = resources.LEVEL
        if (self.transcription().get(name) or {}).get("present") is False:
            return "NG", "缺少要求提交的等级"
        reading.one_of(*resources.LEVEL)
        return reading.decided("要求提交的等级恰好勾选一项。")
