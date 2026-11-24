"""UAES U17: part submission warrant (PSW), UAES v1.0 revision 3 (PSW:1-PSW:7).

A binary Excel warrant (F-PU-206) is read from its cells, form checkboxes and pictures; only whether a picture is a
handwritten signature or a seal is asked of the image model. A one-page warrant PDF without a text layer (a scan)
is transcribed by the image model into a fixed schema, one region of the form per request (the request a clause
first needs). Every clause decides in code over those facts.
PSW:7 reads the header of the same-round control plan from its cells.
"""

from __future__ import annotations

import hashlib
import re
import struct
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.v1.reader import HOLLOW, PLACEHOLDERS, Place, Reader, compact, original_name, resource, unable
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet

# ---- PSW ------------------------------------------------------------------------------------------


_OTHER = ("other", "其它", "其他")
# PSW:1: the exemption names these spellings as no value in 其他工程更改; elsewhere they leave a field unfilled.
_NO_VALUE = {compact(word) for word in ("N/A", "/", "-")}
# PSW:1: an explanation that only says nothing or points nowhere.
_NO_POINTER = {compact(word) for word in ("无", "见附件", "详见附页", "见附页", "详见附件")}
_REJECTED = ("不合格", "不满足", "ng")
_PSW_LEVEL = re.compile(r"(?:等级|level)\s*([1-5一二三四五])", re.I)
_CN_DIGITS = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5"}

_TEXT = {"type": ["string", "null"]}
_MARK = {"type": ["boolean", "null"]}
# Coordinates in the image supplied to this one request, not the PDF or an earlier view.
_BOX = {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 1000}, "minItems": 4, "maxItems": 4}


def _object(**properties: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


_FIELD = _object(present=_MARK, value=_TEXT)
_CHOICE = _object(present=_MARK, options={"type": "array", "items": _object(label=_TEXT, checked=_MARK)})

IDENTITY = {
    "part_name": "零件名称（Part Name）",
    "customer_part_number": "客户零件号（Cust. Part Number）",
    "drawing_number": "图纸号（Shown on Drawing No.）",
    "organization_part_number": "供应商零件号（Org. Part Number）",
    "engineering_change_level": "工程更改级别（Engineering Change Level）",
    "engineering_change_level_date": "日期（编制，工程更改级别的日期 Dated）",
    "additional_engineering_changes": "其他工程更改（Additional Engineering Changes）",
    "additional_engineering_changes_date": "日期（修订，其他工程更改的日期 Dated）",
}
SAFETY = ("safety_government_regulation", "安全及政府条例（Safety and/or Government Regulation）")
WEIGHT = ("weight", "零件净重（Weight kg）")
ORDER = ("purchase_order", "采购订单号（Purchase Order No.）")
TOOLING = ("tooling_number", "模具/工装编号（Tool / Tooling No.）")
EXPLANATION = ("explanation", "说明/解释/备注（Explanation/Comments）")
SUPPLIER_AREA = ("organization_area", "供方制造厂资料（Organization Manufacturing Information）")
SUPPLIER_NAME = ("supplier_name", "公司名称（Supplier Name）")
CUSTOMER_AREA = ("customer_area", "买方资料（Customer Submittal Information）")
CUSTOMER = {
    "customer_name_division": "采购方及部门（Customer Name/Division）",
    "buyer_code": "采购方代码（Buyer/Buyer Code）",
    "application": "应用（Application）",
}
SUBSTANCES = ("substances_of_concern_reported", "是否报告了受客户关注的材料信息（Substances of Concern）")
IMDS = ("imds_submission", "以IMDS或者其它客户格式递交（Submitted by IMDS or other customer format）")
SIGNATURE = ("supplier_authorized_signature", "供方代表签字（Supplier Authorized Signature）")
CONTACT = {
    "print_name": "打印姓名（Print Name）",
    "title": "职务（Title）",
    "phone": "电话号码（Phone No.）",
    "email": "电子邮箱（E-mail）",
    "signed_date": "签署日期（签字栏旁的 Date）",
}
REASON = ("reason_for_submission", "提交原因（Reason for Submission）")
REASON_OTHER = ("reason_other_text", "“其它----请说明（Other - please specify）”旁边的说明")
LEVEL = ("requested_submission_level", "要求提交的等级（Requested Submission Level）")
RESULTS_AREA = ("results_area", "提交结果（Submission Results / Results）")
RESULTS = {
    "dimensional": "尺寸结果（Dimensional）",
    "material": "材料／性能结果（Material / Functional）",
    "appearance": "外观结果（Appearance）",
    "statistical": "统计过程数据（Statistical Process Package）",
}
MEETS = ("meets_requirements", "这些结果满足所有图样和规范要求（These results meet all drawing and specification requirements）")
SEAL = ("ink_seal", "页面上用印泥盖的实际印章")

# A result: whether it is printed, whether its box is checked, and any value written for it (OK/NG, 合格 …).
_RESULT = _object(present=_MARK, checked=_MARK, value=_TEXT)

SCHEMA = _object(
    **{name: _FIELD for name in IDENTITY},
    **{SAFETY[0]: _CHOICE, WEIGHT[0]: _FIELD, ORDER[0]: _FIELD, TOOLING[0]: _FIELD, EXPLANATION[0]: _FIELD},
    **{SUPPLIER_AREA[0]: _object(present=_MARK), SUPPLIER_NAME[0]: _FIELD, CUSTOMER_AREA[0]: _object(present=_MARK)},
    **{name: _FIELD for name in CUSTOMER},
    **{SUBSTANCES[0]: _CHOICE, IMDS[0]: _FIELD},
    **{SIGNATURE[0]: _object(present=_MARK, field_box={"anyOf": [_BOX, {"type": "null"}]})},
    **{name: _object(present=_MARK, value=_TEXT, role_as_written=_TEXT) if name == "title" else _FIELD for name in CONTACT},
    **{REASON[0]: _CHOICE, REASON_OTHER[0]: _FIELD, LEVEL[0]: _CHOICE},
    **{RESULTS_AREA[0]: _object(present=_MARK)},
    **{name: _RESULT for name in RESULTS},
    **{MEETS[0]: _CHOICE},
)
_LABELS = (
    *IDENTITY.items(),
    SAFETY,
    WEIGHT,
    ORDER,
    TOOLING,
    EXPLANATION,
    SUPPLIER_AREA,
    SUPPLIER_NAME,
    CUSTOMER_AREA,
    *CUSTOMER.items(),
    SUBSTANCES,
    IMDS,
    SIGNATURE,
    *CONTACT.items(),
    REASON,
    REASON_OTHER,
    LEVEL,
    RESULTS_AREA,
    *RESULTS.items(),
    MEETS,
)
PROMPT = (
    "这是一页零件提交保证书（PSW）的原件图像。请把图中可见内容按 JSON Schema 转写，只转写，不判断是否合格。\n"
    "present：表单上印有该栏位、区域或选项组的标签为 true，没有为 false，看不清为 null。\n"
    "value：照原样转写栏中填写的内容（打印或手写均可），栏位空白写空字符串，看不清写 null。\n"
    "options：按表单顺序列出该组的每一个选项；实际被勾选（√、×、涂黑等）为 true，明确未勾选为 false，看不清为 null。\n"
    "提交结果的四项（尺寸、材料/性能、外观、统计）：checked 为该项勾选框是否被勾选；value 转写该项旁写出的结论或数值，没有写空字符串。\n"
    "供方代表签字：field_box 只圈出包含供方签字标签及整条填写线的局部区域，不取顾客签字栏。按整张图左、上、右、下各 0–1000 标坐标；无法定位写 null。\n"
    "Title 的 value 照抄；role_as_written 只抄实际表示该人职务的原文字段内容，不从公司名、部门或邮箱推定职务；没有可辨识的职务写 null。\n"
    "字段与表单标签的对应：\n" + "\n".join(f"{name}: {label}" for name, label in _LABELS)
)
# A scanned warrant is transcribed in three requests over the same page image, one per region of the form. The whole
# schema in one request ran the image model past its fixed output limit (4096 tokens) before it answered.
PARTS = (
    (*IDENTITY, SAFETY[0], WEIGHT[0], ORDER[0], TOOLING[0], EXPLANATION[0]),
    (SUPPLIER_AREA[0], SUPPLIER_NAME[0], CUSTOMER_AREA[0], *CUSTOMER, SUBSTANCES[0], IMDS[0], SIGNATURE[0], *CONTACT),
    (REASON[0], REASON_OTHER[0], LEVEL[0], RESULTS_AREA[0], *RESULTS, MEETS[0]),
)
assert sorted(name for part in PARTS for name in part) == sorted(SCHEMA["properties"])


def part_request(names: tuple[str, ...]) -> tuple[str, dict[str, Any]]:
    """The prompt and schema for one region of the form: the shared instructions, only this region's labels."""
    head = PROMPT[: PROMPT.index("字段与表单标签的对应：")]
    labels = "\n".join(f"{name}: {label}" for name, label in _LABELS if name in names)
    return head + "本次只转写下列字段，字段与表单标签的对应：\n" + labels, _object(**{name: SCHEMA["properties"][name] for name in names})


# The locator proposes regions, not positive/negative business facts. The crop reader gets only actual local pixels.
_STAMP_SEARCH = _object(fully_examined=_MARK, candidates={"type": "array", "items": _BOX})
_STAMP_FIND = (
    "逐处查看本图，圈出每个可能的独立盖章印迹，不限颜色、形状、文字或盖章位置。"
    "无法区分是印迹、印刷标志、手写还是数字签名的也圈出待核。返回各图形本身的紧框 [左,上,右,下]，本图横纵轴各为0至1000。"
    "整片边缘均清楚且已完整检查、没有遗漏疑似印迹才填 fully_examined=true；未能检查全部填null。清楚且没有疑似印迹才返回空列表。不要作审核判断。"
)
_STAMP_LOCAL = _object(kind={"enum": ["imprint", "printed_logo", "handwriting", "digital_signature", "printed_text", "checkbox", "uncertain"]}, detail=_TEXT)
_STAMP_READ = (
    "只描述此局部图像本身。若能直接看见实际盖章印迹，kind=imprint，detail照录印文或描述具体可见的盖印痕迹。"
    "印刷商标、手写签字、数字签字框、打印文字和勾框分别归类，不是盖印。若图形不完整、图文关系不清、不能区别印章与标志，填 uncertain。"
    "不得根据文件类别或上下文推测，不作审核判断。"
)
_SIGN_LOCAL = _object(label=_TEXT, entire_field=_MARK, mark={"enum": ["handwriting", "digital_signature", "printed_text", "blank", "uncertain"]}, detail=_TEXT)
_SIGN_READ = (
    "只读本局部。label 照抄与笔迹或空白直接相连的签字栏标签；entire_field 是这条签字栏从标签到填写线终点是否在图内。"
    "mark 只描述该栏的填写内容：手写笔迹、数字签署、印刷体、空白或不确定，detail 写明看见的笔迹/字样或实际空白线。"
    "不得把邻栏、打印姓名或公司标志算到该栏。"
)


def _subbox(proposal: Any, bounds: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    """Reject invalid and near-whole-image proposals: those are not local witnesses. Never infer absence from rejection."""
    if not isinstance(proposal, list) or len(proposal) != 4 or any(type(v) is not int or not 0 <= v <= 1000 for v in proposal):
        return None
    left, top, right, bottom = proposal
    if left >= right or top >= bottom or (right - left) * (bottom - top) > 500_000:
        return None
    x, y, w, h = bounds[0], bounds[1], bounds[2] - bounds[0], bounds[3] - bounds[1]
    box = (x + left * w // 1000, y + top * h // 1000, x + (right * w + 999) // 1000, y + (bottom * h + 999) // 1000)
    return box if box[2] > box[0] and box[3] > box[1] else None


def _local_image(image, box) -> bytes:
    out = BytesIO()
    image.crop(box).save(out, format="PNG")
    return out.getvalue()


_EMBEDDED_FORM = {"enum": ["imprint", "handwriting", "imprint_and_handwriting", "printed_logo", "digital_signature", "printed_text", "blank", "uncertain"]}
PICTURE_SCHEMA = _object(kind=_EMBEDDED_FORM, detail=_TEXT)
PICTURE_PROMPT = (
    "就这一张嵌入原图区分实际盖章印迹、手写笔迹、两者同时可见、印刷商标、数字签字、打印文字、空白或不确定。"
    "detail写出具体可见痕迹或转写字样；不得把印刷标志、姓名或数字签署算作盖章，不推断是否在本文件上亲签，不作审核判断。"
)

# The F-PU-206 Excel warrant: each field is found by the words of its printed label (all of them, in one cell).
# "right": the value is written right of the label on its row, up to the next label; "above": in the cell above.
XLS_FIELDS = {
    "part_name": (("零件名称",), "right"),
    "customer_part_number": (("客户零件号",), "right"),
    "drawing_number": (("图纸号",), "right"),
    "organization_part_number": (("供应商零件号",), "right"),
    "engineering_change_level": (("工程更改级别",), "right"),
    "additional_engineering_changes": (("其他工程更改",), "right"),
    "weight": (("重量",), "right"),
    "purchase_order": (("采购单号",), "right"),
    "explanation": (("解释/说明",), "right"),
    "supplier_name": (("公司名称",), "above"),
    "customer_name_division": (("采购方及部门",), "above"),
    "buyer_code": (("采购方代码",), "above"),
    "application": (("Application", "应用"), "above"),
    "imds_submission": (("以IMDS",), "right"),
    "print_name": (("打印体姓名",), "right"),
    "title": (("职务",), "right"),
    "phone": (("电话",), "right"),
    "email": (("电子邮件",), "right"),
}
# Fields a template may not print; absent, they are not judged ("模板未设该栏的，不据此判缺项").
XLS_OPTIONAL = {"purchase_order", "tooling_number", "explanation"}
XLS_TOOLING = (("模具编号",), ("工装编号",), ("模具号",))
XLS_DATES = {"engineering_change_level_date": "engineering_change_level", "additional_engineering_changes_date": "additional_engineering_changes"}
XLS_DATE = ("日期",)
XLS_AREAS = {SUPPLIER_AREA[0]: ("供方制造厂资料",), CUSTOMER_AREA[0]: ("买方资料",)}
XLS_CHOICES = {
    SAFETY[0]: (("安全及政府条例",), None),
    SUBSTANCES[0]: (("受客户关注的材料",), None),
    REASON[0]: (("提交原因",), ("要求提交的等级",)),
    LEVEL[0]: (("要求提交的等级",), ("提交结果",)),
}
XLS_SIGNATURE = ("供方代表签字",)
# PSW:6 names the results area with these spellings; the declaration closes it.
RESULT_AREA_NAMES = ("提交结果", "results", "提交检查结果", "提交结果说明")
RESULT_END = ("声明", "declaration")
RESULT_WORDS = {
    "dimensional": ("尺寸", "dimension"),
    "material": ("材料", "性能", "material", "function"),
    "appearance": ("外观", "appearance"),
    "statistical": ("统计", "statistical"),
}

# ---- binary Excel drawing objects (BIFF8): form checkboxes and pictures ----------------------------


@dataclass(eq=False)
class _Shape:
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
    while start + 8 <= end:
        header, kind, size = struct.unpack_from("<HHI", blob, start)
        yield header >> 4, kind, start + 8, min(start + 8 + size, end), header & 0xF == 0xF
        start += 8 + size


def _blips(blob: bytes) -> list[bytes | None]:
    found: list[bytes | None] = []

    def walk(start: int, end: int) -> None:
        for _instance, kind, begin, finish, container in _art(blob, start, end):
            if container:
                walk(begin, finish)
            elif kind == 0xF007 and finish - begin >= 36:
                name = blob[begin + 33]
                embedded = next(iter(_art(blob, begin + 36 + name, finish)), None)
                if embedded is None or embedded[1] not in (0xF01D, 0xF01E):
                    found.append(None)
                else:
                    instance, _kind, data, stop, _ = embedded
                    found.append(bytes(blob[data + 16 * (1 + (instance & 1)) + 1 : stop]))

    walk(0, len(blob))
    return found


def sheet_shapes(path: str, sheet: str) -> list[_Shape]:
    """Every drawing object of one worksheet of a BIFF8 workbook, in drawing order."""
    import olefile

    with olefile.OleFileIO(path) as document:
        stream = document.openstream("Workbook" if document.exists("Workbook") else "Book").read()
    names, group, previous = {}, bytearray(), None
    drawing, objects, current = bytearray(), [], None
    for position, kind, data in _records(stream):
        if kind == 0x0085:
            length, wide = data[6], data[7]
            names[struct.unpack_from("<I", data)[0]] = data[8 : 8 + length * (2 if wide else 1)].decode("utf-16-le" if wide else "latin-1")
        elif kind == 0x00EB or kind == 0x003C and previous == 0x00EB:
            group.extend(data)
        elif kind == 0x0809:
            current = names.get(position)
        elif kind == 0x000A:
            current = None
        elif current == sheet and kind == 0x00EC:
            drawing.extend(data)
        elif current == sheet and kind == 0x005D:
            subrecords, offset = {}, 0
            while offset + 4 <= len(data):
                code, size = struct.unpack_from("<HH", data, offset)
                subrecords[code] = data[offset + 4 : offset + 4 + size]
                offset += 4 + size
                if code in (0x0000, 0x0013):
                    break
            state = struct.unpack_from("<H", subrecords[0x0012])[0] if len(subrecords.get(0x0012, b"")) >= 2 else None
            objects.append([struct.unpack_from("<H", data, 4)[0], {0: False, 1: True}.get(state) if state is not None else None, ""])
        elif current == sheet and kind == 0x003C and previous == 0x01B6 and objects and data:
            objects[-1][2] = data[1:].decode("utf-16-le" if data[0] else "latin-1", "replace").strip()
        previous = None if kind == 0x003C and previous == 0x01B6 else previous if kind == 0x003C else kind
    store = _blips(bytes(group))
    shapes: list[tuple[tuple[int, int, int, int] | None, int | None]] = []

    def walk(start: int, end: int) -> None:
        for _instance, kind, begin, finish, container in _art(bytes(drawing), start, end):
            if kind == 0xF004:
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
        for (anchor, picture), (kind, checked, caption) in zip(shapes, objects, strict=True)
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
        found = [sheet for sheet in book.sheets() if self._find(sheet, XLS_FIELDS["part_name"][0])]
        if len(found) != 1:
            raise unable("零件提交保证书工作簿中" + ("未找到" if not found else "有多张") + "保证书工作表，检查程序未能读取，需人工复核。")
        self.sheet, self.datemode = found[0], book.datemode
        self.extents = {sheet.name: (max(1, sheet.nrows), max(1, sheet.ncols)) for sheet in book.sheets()}
        self.shapes = sheet_shapes(path, self.sheet.name)
        labels = [words for words, _where in XLS_FIELDS.values()] + [XLS_DATE, XLS_SIGNATURE, *XLS_TOOLING]
        labels += [*XLS_AREAS.values(), *(words for words, _end in XLS_CHOICES.values())]
        labels += [("传真",), ("所有客户的工具",), ("顾客签字",)]
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
        cells = []
        for next_column in range(column + 1, self.sheet.ncols + 1):
            if (row, next_column) in self.labels:
                break
            if self.text(row, next_column):
                cells.append(next_column)
        return " ".join(self.text(row, cell) for cell in cells), (row, cells[0] if cells else column)

    def first_right(self, row: int, column: int) -> tuple[str, tuple[int, int]]:
        for next_column in range(column + 1, self.sheet.ncols + 1):
            if self.text(row, next_column):
                return self.text(row, next_column), (row, next_column)
        return "", (row, column)

    def above(self, row: int, column: int) -> tuple[str, tuple[int, int]]:
        for top, bottom, left, right in self.sheet.merged_cells:
            if top < row - 1 <= bottom - 1 and left < column <= right:
                return self.text(top + 1, left + 1), (top + 1, left + 1)
        return self.text(row - 1, column), (row - 1, column)

    def row_texts(self, first: int, last: int) -> list[tuple[int, int, str]]:
        return [(row, column, self.text(row, column)) for row in range(first, last) for column in range(1, self.sheet.ncols + 1) if self.text(row, column)]


class _Reading:
    """One clause's decision over the facts: definite violations, and what could not be read."""

    def __init__(self, agent: "WarrantR3"):
        self.agent, self.facts = agent, agent.transcription()
        self.failed: list[str] = []
        self.unknown: list[str] = []

    def present(self, name: str, label: str) -> bool:
        present = (self.facts.get(name) or {}).get("present")
        if present is False:
            self.failed.append(f"缺少{label}字段")
        elif present is None:
            self.unknown.append(label)
        return present is True

    def field(self, name: str, label: str, *, empty: set[str] | None = None) -> str | None:
        """A filled field's value; a blank one, or one written only as a word of `empty`, is a failure."""
        if not self.present(name, label):
            return None
        value = self.facts[name].get("value")
        if value is None:
            self.unknown.append(label)
            return None
        self.agent.note(name, value or "空白")
        if not value.strip() or value.strip() in HOLLOW:
            self.failed.append(f"{label}为空")
            return None
        if empty and compact(value) in empty:
            self.failed.append(f"{label}填“{value.strip()}”，未填写内容")
            return None
        return value

    def choice(self, name: str, label: str) -> list[dict[str, Any]] | None:
        if not self.present(name, label):
            return None
        options = self.facts[name].get("options") or []
        if not options or any(option.get("checked") is None for option in options):
            self.unknown.append(f"{label}的勾选")
            return None
        checked = [option for option in options if option["checked"] is True]
        self.agent.note(name, "、".join(str(option.get("label") or "") for option in checked) or "未勾选")
        return checked

    def one_of(self, name: str, label: str) -> list[dict[str, Any]] | None:
        checked = self.choice(name, label)
        if checked is not None and len(checked) != 1:
            self.failed.append(f"{label}{'未勾选' if not checked else '勾选了多项'}")
        return checked

    def decided(self, passed: str) -> tuple[str, str]:
        if self.failed:
            return "NG", "零件提交保证书" + "；".join(self.failed) + "。"
        if self.unknown:
            raise unable(f"未能从零件提交保证书原件辨识{'、'.join(self.unknown)}，需人工复核。")
        return "PASS", passed


# The same-round control plan header (PSW:7): labels and the row that closes the header.
CP_PART_NUMBER = ("零件号", "零件编号", "partnumber", "partno")
CP_PART_NAME = ("零件名称", "产品名称", "partname")
CP_NOT_PART = ("客户", "供应商", "customer", "supplier", "控制计划编号", "controlplannumber")
CP_HEADING = ("工序名称", "产品特性", "过程特性", "processname", "characteristics")
CP_LABEL_WORDS = ("更改等级", "联系人", "电话", "小组", "批准", "日期", "编号", "名称", "零件号", "版本", "编制", "date", "number", "name")
_UAES_PART = re.compile(r"(?<![0-9A-Za-z])F[0-9A-Z]{9}(?![0-9A-Za-z])")
_CP_KIND = "控制计划"
_APPROVAL_KINDS = ("E11", "顾客工程批准")
# A signing date, year first (2023-06-23, 2023年6月23日, 20230623) or day first (23.6.2023, 23/06/2023).
SIGNED_DATE = re.compile(r"\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}|\d{8}|(?<!\d)\d{1,2}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{4}")
_APPROVAL_NAMES = ("顾客工程批准", "工程批准", "偏差", "OTS", "认可")


def _identity(value: Any) -> str:
    """A part number as compared: width-folded, spaces removed, case-folded."""
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold()


class _ScanFacts:
    """The facts of a scanned warrant, transcribed one region of the form at a time when a clause first reads it.

    Each region is one request (see PARTS) and is asked once: a failed request is kept and raised again, so only the
    clauses that read that region stop, and the others are still decided from the regions that were read."""

    def __init__(self, agent: "WarrantR3", image: bytes, page_size: tuple[float, float]):
        self.agent, self.image, self.page_size = agent, image, page_size
        self.parts: dict[int, Any] = {}

    def _part(self, name: str) -> dict[str, Any]:
        index = next((index for index, names in enumerate(PARTS) if name in names), None)
        if index is None:
            return {}
        if index not in self.parts:
            prompt, schema = part_request(PARTS[index])
            try:
                self.parts[index] = self.agent.transcribe(self.image, prompt, schema, "零件提交保证书")
            except GuardStop as stop:
                self.parts[index] = stop
        if isinstance(self.parts[index], GuardStop):
            raise self.parts[index]
        return self.parts[index]

    def get(self, name: str, default: Any = None) -> Any:
        return self._part(name).get(name, default)

    def __getitem__(self, name: str) -> Any:
        return self._part(name)[name]


class WarrantR3(Reader):
    DOCUMENT = ((), (), ())
    EXTRA_RESOURCE = resource(DOCUMENT)
    SUPPORTED_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".docx", ".pdf"})
    TABLES = False

    # ---- facts -------------------------------------------------------------------------------

    def transcription(self) -> Any:
        if "_transcription" not in self.__dict__:
            try:
                self._transcription: Any = self._transcribe()
            except GuardStop as stop:
                self._transcription = stop
        if isinstance(self._transcription, GuardStop):
            raise self._transcription
        return self._transcription

    def _transcribe(self) -> Any:
        path = str(self.audit_file.file_url)
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
            page = document[0]
            page.set_rotation(0)  # All witnesses below are unrotated original PDF points.
            page_size = page.rect.width, page.rect.height
            image = page.get_pixmap(dpi=200).tobytes("png")
        self._digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        return _ScanFacts(self, image, page_size)

    def _read_xls(self, path: str) -> dict[str, Any]:
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

        signature_rows = [row for row, _column in sheet.find(XLS_SIGNATURE)]

        def nearest(words: tuple[str, ...]) -> tuple[bool | None, tuple[int, int] | None]:
            # The signer's contact labels are the first ones under the supplier's signature line (the customer's repeat them).
            cells = sorted(cell for cell in sheet.find(words) if len(signature_rows) == 1 and cell[0] >= signature_rows[0])
            return (True, cells[0]) if cells else (None if sheet.find(words) else False, None)

        labels: dict[str, tuple[int, int]] = {}
        for name, (words, where) in XLS_FIELDS.items():
            present, cell = nearest(words) if name in CONTACT else one(words)
            facts[name] = {"present": present, "value": None}
            if cell is not None:
                labels[name] = cell
                facts[name]["value"], at = (sheet.right if where == "right" else sheet.above)(*cell)
                place(name, at)
        tooling = next((cells for words in XLS_TOOLING if (cells := sheet.find(words))), [])
        facts[TOOLING[0]] = {"present": bool(tooling) if len(tooling) <= 1 else None, "value": None}
        if len(tooling) == 1:
            facts[TOOLING[0]]["value"], at = sheet.right(*tooling[0])
            place(TOOLING[0], at)
        for name, field in XLS_DATES.items():
            owner = labels.get(field)
            dates = [cell for cell in sheet.find(XLS_DATE) if owner is not None and cell[0] == owner[0] and cell[1] > owner[1]]
            facts[name] = {"present": True if len(dates) == 1 else (None if dates or owner is None else False), "value": None}
            if len(dates) == 1:
                facts[name]["value"], at = sheet.right(*dates[0])
                place(name, at)
        for name, words in XLS_AREAS.items():
            facts[name] = {"present": one(words)[0]}
        for name, (words, until) in XLS_CHOICES.items():
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
        other = next((option for option in facts[REASON[0]]["options"] if any(word in compact(option["label"]) for word in _OTHER)), None)
        facts[REASON_OTHER[0]] = {"present": None if other is None else True, "value": None}
        if other is not None:
            facts[REASON_OTHER[0]]["value"], at = sheet.right(*other["cell"])
            place(REASON_OTHER[0], at)
        present, cell = one(XLS_SIGNATURE)
        facts[SIGNATURE[0]] = {"present": present, "handwritten": None, "value": sheet.right(*cell)[0] if cell else None}
        self._signature_cell = cell
        facts["signed_date"] = {"present": False, "value": None}
        if cell is not None:
            place(SIGNATURE[0], cell)
            dates = [found for found in sheet.find(XLS_DATE) if found[0] == cell[0] and found[1] > cell[1]]
            facts["signed_date"] = {"present": True if len(dates) == 1 else None, "value": None}
            if len(dates) == 1:
                facts["signed_date"]["value"], at = sheet.right(*dates[0])
                place("signed_date", at)
        self._read_results(sheet, facts, place)
        return facts

    def _read_results(self, sheet: _XlsWarrant, facts: dict[str, Any], place: Any) -> None:
        """The results area: the label row down to the declaration; its result and yes/no boxes by caption."""
        starts = [cell for words in RESULT_AREA_NAMES for cell in sheet.find((words,)) if cell[1] <= 3]
        facts[RESULTS_AREA[0]] = {"present": bool(starts)}
        for name in RESULTS:
            facts[name] = {"present": False if not starts else None, "checked": None, "value": ""}
        facts[MEETS[0]] = {"present": False if not starts else None, "options": []}
        if not starts:
            return
        first = min(row for row, _column in starts)
        ends = [row for words in RESULT_END for row, _column in sheet.find((words,)) if row > first]
        last = min(ends) if ends else first + 4
        boxes = [shape for shape in sheet.shapes if shape.kind == _CHECKBOX and first <= shape.anchor[0] < last]
        for name, words in RESULT_WORDS.items():
            found = [box for box in boxes if any(word in compact(box.caption) for word in words)]
            if len(found) == 1:
                facts[name] = {"present": True, "checked": found[0].checked, "value": ""}
                place(name, found[0].anchor[:2])
            else:
                written = [(row, column, text) for row, column, text in sheet.row_texts(first, last) if any(word in compact(text) for word in words)]
                facts[name] = {"present": True if written else False, "checked": None if written else None, "value": ""}
        meets = [cell for cell in sheet.find(("满足所有",)) if first <= cell[0] < last]
        if len(meets) == 1:
            row = meets[0][0]
            options = [box for box in boxes if box.anchor[0] <= row <= box.anchor[2] and compact(box.caption) in {"yes", "no", "是", "否"}]
            facts[MEETS[0]] = {"present": True, "options": [{"label": box.caption, "checked": box.checked} for box in sorted(options, key=lambda box: box.anchor[1])]}
            place(MEETS[0], meets[0])
        else:
            facts[MEETS[0]] = {"present": None if meets else False, "options": []}
        # Words written in the area besides the printed labels: a result written as NG / 不合格.
        self._results_text = [text for _row, _column, text in sheet.row_texts(first, last)]

    def _picture(self, shape: _Shape) -> dict[str, Any]:
        cache = self.__dict__.setdefault("_pictures", {})
        if shape not in cache:
            unknown = {"handwritten_signature": None, "ink_seal": None, "detail": ""}
            if shape.picture is None or len(cache) >= 6:
                return unknown
            from PIL import Image

            output = BytesIO()
            try:
                with Image.open(BytesIO(shape.picture)) as image:
                    image.convert("RGB").save(output, format="PNG")
            except (ValueError, OSError):
                cache[shape] = unknown
                return unknown
            local = self.transcribe(output.getvalue(), PICTURE_PROMPT, PICTURE_SCHEMA, "零件提交保证书上的图片")
            kind, detail = local.get("kind"), local.get("detail")
            cache[shape] = unknown
            if kind in _EMBEDDED_FORM["enum"] and kind != "uncertain" and isinstance(detail, str) and detail.strip():
                cache[shape] = {"handwritten_signature": kind in {"handwriting", "imprint_and_handwriting"}, "ink_seal": kind in {"imprint", "imprint_and_handwriting"}, "detail": detail}
        return cache[shape]

    @staticmethod
    def _any(values: list[bool | None]) -> bool | None:
        return True if True in values else (False if all(value is False for value in values) else None)

    def _scan_note(self, scan: _ScanFacts, image, box, fact: str, detail: str) -> None:
        sx, sy = scan.page_size[0] / image.width, scan.page_size[1] / image.height
        bbox = [round(value * scale, 3) for value, scale in zip(box, (sx, sy, sx, sy), strict=True)]
        self.witness(
            {"kind": "pdf_region", "page": 1, "bbox": bbox, "coordinate_system": "pdf_points_unrotated", "source_sha256": self._digest, "engine": "ocr_llm", "fact": fact},
            detail,
            visual=True,
        )

    def _scan_signature(self, scan: _ScanFacts, field: dict[str, Any]) -> dict[str, Any]:
        from PIL import Image

        field["handwritten"] = None
        with Image.open(BytesIO(scan.image)) as image:
            box = _subbox(field.get("field_box"), (0, 0, *image.size))
            if box is None:
                return field
            local = self.transcribe(_local_image(image, box), _SIGN_READ, _SIGN_LOCAL, "供方签署栏局部")
            label = compact(local.get("label"))
            bound = re.search(r"(?:供方|供应商|供應商).*(?:签字|签名|簽名|簽字)|(?:supplier|organization).{0,32}signature", label)
            detail = local.get("detail")
            customer = re.search(r"customer|purchaser|buyer|客户|顧客|顾客|客戶", label)
            if not bound or customer or local.get("entire_field") is not True or not isinstance(detail, str) or not detail.strip():
                return field
            mark = local.get("mark")
            self._scan_note(scan, image, box, SIGNATURE[0], f"{local['label']}：{detail}")
            if mark in {"handwriting", "digital_signature", "printed_text", "blank"}:
                field["handwritten"] = mark == "handwriting"
        return field

    def signature(self) -> dict[str, Any]:
        facts = self.transcription()
        field = dict(facts.get(SIGNATURE[0]) or {})
        if isinstance(facts, _ScanFacts):
            return self._scan_signature(facts, field)
        if self._signature_cell is None:
            return field
        row, column = self._signature_cell
        boundary = min((c for r, c in self._sheet.labels if r == row and c > column), default=self._sheet.sheet.ncols + 1)
        values = []
        for shape in self._sheet.shapes:
            top, left, bottom, right = shape.anchor
            if shape.kind in {_CHECKBOX, 0x19} or not (top <= row <= bottom and right > column and left < boundary):
                continue
            mark = self._picture(shape)["handwritten_signature"] if shape.kind == _PICTURE else None
            if mark is True:
                # PSW:3#r3.2: the original proves an inserted picture of handwriting, not who signed this document.
                self.witness(self._range(shape), "供方签署栏附近有嵌入的手写笔迹图片；无法确认本件亲签", visual=True)
                field["inserted"] = True
            values.append(None if mark is True else mark)
        field["handwritten"] = self._any(values)
        return field

    def _scan_seal(self, scan: _ScanFacts) -> bool | None:
        from PIL import Image

        with Image.open(BytesIO(scan.image)) as image:
            w, h = image.size
            # Overlapping original-image views cover the entire submitted page, not just its signature block.
            complete = True
            # One PSW has a bounded worker budget; a limit skips the unresolved region, never certifies absence.
            crops: dict[Any, Any] = {}
            for panel in ((0, 0, w, (h * 55 + 99) // 100), (0, h * 45 // 100, w, h)):
                result = self.transcribe(_local_image(image, panel), _STAMP_FIND, _STAMP_SEARCH, "印章全页分区定位")
                boxes = result.get("candidates")
                if not isinstance(boxes, list):
                    complete = False
                    continue
                cleared = result.get("fully_examined") is True and len(boxes) <= 6
                for proposal in boxes[:6]:
                    box = _subbox(proposal, panel)
                    if box is None or len(set(image.crop(box).convert("L").getextrema())) == 1:
                        cleared = False
                        continue
                    if box not in crops:
                        if len(crops) >= 2:
                            cleared = False
                            continue
                        crops[box] = self.transcribe(_local_image(image, box), _STAMP_READ, _STAMP_LOCAL, "疑似印迹的原件局部")
                    local = crops[box]
                    kind, detail = local.get("kind"), local.get("detail")
                    if not isinstance(detail, str) or not detail.strip():
                        cleared = False
                        continue
                    self._scan_note(scan, image, box, SEAL[0], f"{kind}：{detail}")
                    if kind == "imprint":
                        return True
                    if kind not in {"printed_logo", "handwriting", "digital_signature", "printed_text", "checkbox"}:
                        cleared = False
                if cleared:
                    self._scan_note(scan, image, panel, SEAL[0], "本图区域完整查看，未见独立盖章印迹")
                complete = complete and cleared
            return False if complete else None

    def seal(self) -> bool | None:
        facts = self.transcription()
        if isinstance(facts, _ScanFacts):
            return self._scan_seal(facts)
        complete = True
        for sheet_name in (self._sheet.sheet.name, *(name for name in self._sheet.extents if name != self._sheet.sheet.name)):
            try:
                shapes = self._sheet.shapes if sheet_name == self._sheet.sheet.name else sheet_shapes(str(self.audit_file.file_url), sheet_name)
            except (OSError, ValueError, struct.error):
                complete = False
                continue
            for shape in shapes:
                if shape.kind in {_CHECKBOX, 0x19}:  # Form controls and BIFF cell comments are not applied marks.
                    continue
                mark = self._picture(shape)["ink_seal"] if shape.kind == _PICTURE else None
                if mark is True:
                    self.witness(self._range(shape, sheet_name), self._picture(shape)["detail"], visual=True)
                    return True
                if mark is None:
                    complete = False
            self.witness(
                {"kind": "xlsx_range", "sheet": sheet_name, "start": "A1", "end": _cell_name(*self._sheet.extents[sheet_name]), "scope": "worksheet_drawing_inventory"},
                f"核对该表绘图对象，共 {len(shapes)} 个",
            )
        return False if complete else None

    def _range(self, shape: _Shape, sheet_name: str | None = None) -> dict[str, Any]:
        top, left, bottom, right = shape.anchor
        return {"kind": "xlsx_range", "sheet": sheet_name or self._sheet.sheet.name, "start": _cell_name(top, left), "end": _cell_name(bottom, right)}

    def note(self, fact: str, value: str) -> None:
        if hasattr(self, "_sheet"):
            if fact in self._where:
                locator, visual = self._where[fact]
                self.witness(locator, value, visual=visual)
            return
        self.witness({"kind": "pdf_page", "page": 1, "source_sha256": getattr(self, "_digest", ""), "engine": "ocr_llm", "fact": fact}, value, visual=True)

    def first_submission(self) -> bool:
        """Whether the reason checked is 首次提交 (Initial submission)."""
        options = (self.transcription().get(REASON[0]) or {}).get("options") or []
        return any(option.get("checked") is True and any(word in compact(option.get("label")) for word in ("首次提交", "initialsubmission")) for option in options)

    # ---- PSW:1 ---------------------------------------------------------------------------------

    @rule("PSW基础字段两个日期法规唯一勾选净重与说明栏")
    def rule_1(self, ctx: RuleContext):
        reading = _Reading(self)
        exempt = self.first_submission()
        for name, label in IDENTITY.items():
            if name == "additional_engineering_changes" and exempt:
                # 全新开发首次提交且确无附加设变: N/A, /, - in 其他工程更改 is no missing item.
                reading.field(name, label)
                continue
            reading.field(name, label, empty=_NO_VALUE)
        reading.one_of(*SAFETY)
        weight = reading.field(*WEIGHT)
        if weight is not None:
            text = unicodedata.normalize("NFKC", weight)
            if not re.search(r"\d", text):
                reading.failed.append(f"零件净重“{weight}”没有数值")
            elif "kg" not in text.casefold():
                reading.failed.append(f"零件净重“{weight}”没有单位kg")
        for name, label in (ORDER, TOOLING):
            if (self.transcription().get(name) or {}).get("present") is True:
                reading.field(name, label)
        if (self.transcription().get(EXPLANATION[0]) or {}).get("present") is not False:
            explanation = reading.field(*EXPLANATION)
            if explanation is not None:
                written = compact(explanation)
                if written in PLACEHOLDERS:
                    reading.failed.append(f"说明/解释/备注栏只填占位词“{explanation.strip()}”，没有实质内容")
                elif written in _NO_POINTER:
                    reading.failed.append(f"说明/解释/备注栏只写“{explanation.strip()}”，没有具体指向")
        exemption = "；首次提交，其他工程更改按豁免处理" if exempt else ""
        return reading.decided(f"零件身份、工程更改级别及两个日期、零件净重、说明栏均已填写，安全及政府条例恰好勾选一项{exemption}。")

    # ---- PSW:2 ---------------------------------------------------------------------------------

    @rule("供方和买方资料材料申报及IMDS")
    def rule_2(self, ctx: RuleContext):
        reading = _Reading(self)
        if reading.present(*SUPPLIER_AREA):
            reading.field(*SUPPLIER_NAME)
        if reading.present(*CUSTOMER_AREA):
            for name, label in CUSTOMER.items():
                reading.field(name, label)
        reading.one_of(*SUBSTANCES)
        reading.field(*IMDS)
        return reading.decided("供方公司名称、买方三项资料、材料信息恰好勾选一项与IMDS均已填写。")

    # ---- PSW:3 ---------------------------------------------------------------------------------

    @rule("供方代表手写签字印章与联系信息")
    def rule_3(self, ctx: RuleContext):
        reading = _Reading(self)
        name, label = SIGNATURE
        if (reading.facts.get(name) or {}).get("present") is False:
            self.note(name, "未见供方代表签字栏")
            return "NG", "零件提交保证书中缺少供方代表签字字段。"
        contact, invalid = [], []
        for field, words in CONTACT.items():
            value = reading.field(field, words)
            if value is None:
                continue
            text = unicodedata.normalize("NFKC", value)
            if field == "title" and isinstance(reading.facts, _ScanFacts):
                role = reading.facts[field].get("role_as_written")
                if not isinstance(role, str) or not role.strip() or compact(role) not in compact(value):
                    reading.unknown.append(f"Title 原文“{value}”中可辨识的职务")
            if field == "phone" and len(re.sub(r"\D", "", text)) < 7:
                invalid.append(f"电话号码“{value}”格式不合法")
            elif field == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", text.strip()):
                invalid.append(f"电子邮箱“{value}”格式不合法")
            elif field == "signed_date" and not SIGNED_DATE.search(text):
                invalid.append(f"签署日期“{value}”不是日期")
            contact.append(f"{words.split('（')[0]}{value}")
        # Every gap of the signing area is reported together: contact details, signature and seal.
        missing = [failure for failure in reading.failed if failure.startswith("缺少") or failure.endswith("为空")]
        if missing or invalid:
            reading.failed[:] = [failure for failure in reading.failed if failure not in missing]
            reading.failed.insert(0, "联系信息不完整：" + "、".join([*missing, *invalid]))
        if reading.present(name, label):
            try:
                signature = self.signature()
            except GuardStop as stop:
                if stop.decision != Decision.UNABLE:
                    raise
                signature = {"handwritten": None}
            signed = signature.get("handwritten")
            if signed is False:
                if not isinstance(reading.facts, _ScanFacts):
                    self.note(name, signature.get("value") or "签署线未见手写笔迹")
                reading.failed.append(f"{label}没有手写签名")
            elif signed is None:
                reading.unknown.append("签署栏嵌入的手写图片是否算本件手签" if signature.get("inserted") else f"{label}是否手写")
        try:
            seen = self.seal()
        except GuardStop as stop:
            if stop.decision != Decision.UNABLE:
                raise
            seen = None
        if seen is False:
            reading.failed.append("原件上未找到印章")
        elif seen is None:
            reading.unknown.append("原件上是否有印章")
        if reading.failed and reading.unknown:
            return "NG", "零件提交保证书" + "；".join(reading.failed) + "。另未核实：" + "、".join(reading.unknown) + "。"
        return reading.decided(f"零件提交保证书有供方代表手写签字和印章，签署区{'、'.join(contact)}。")

    # ---- PSW:4 ---------------------------------------------------------------------------------

    @rule("提交原因选择与其它说明")
    def rule_4(self, ctx: RuleContext):
        reading = _Reading(self)
        name, label = REASON
        if (reading.facts.get(name) or {}).get("present") is False:
            self.note(name, "未见供方代表签字栏")
            return "NG", "零件提交保证书缺少“提交原因”字段。"
        checked = reading.choice(name, label)
        if checked is not None:
            if not checked:
                return "NG", "零件提交保证书“提交原因”没有勾选任何选项。"
            if any(word in compact(option.get("label")) for option in checked for word in _OTHER):
                reading.field(*REASON_OTHER)
        other = any(word in compact(option.get("label")) for option in checked or [] for word in _OTHER)
        return reading.decided("提交原因已勾选（" + "、".join(str(option.get("label") or "").strip() for option in checked or []) + "）" + ("，“其它”的说明已填写。" if other else "。"))

    # ---- PSW:5 ---------------------------------------------------------------------------------

    @rule("提交等级同义合并后唯一勾选")
    def rule_5(self, ctx: RuleContext):
        reading = _Reading(self)
        name, label = LEVEL
        if (reading.facts.get(name) or {}).get("present") is False:
            self.note(name, "未见供方代表签字栏")
            return "NG", "零件提交保证书缺少“要求提交的等级”字段。"
        checked = reading.choice(name, label)
        if checked is None:
            return reading.decided("")
        if not checked:
            return "NG", "零件提交保证书“要求提交的等级”没有勾选任何等级。"
        # Synonyms count once: 等级3, Level 3 and 等级三 are one level.
        levels = set()
        for option in checked:
            text = unicodedata.normalize("NFKC", str(option.get("label") or ""))
            match = _PSW_LEVEL.search(text)
            levels.add(_CN_DIGITS.get(match.group(1), match.group(1)) if match else compact(text))
        if len(levels) != 1:
            return "NG", "零件提交保证书“要求提交的等级”同义合并后仍勾选了多项：" + "、".join(str(option.get("label") or "")[:20] for option in checked) + "。"
        return "PASS", f"要求提交的等级恰好勾选一项：{str(checked[0].get('label') or '')[:40]}。"

    # ---- PSW:6 ---------------------------------------------------------------------------------

    def approvals(self) -> list[Any]:
        """Same-round customer engineering approvals or deviation permits."""
        return list({item.audit_id: item for item in [*self.batch_files(*_APPROVAL_KINDS), *self.named_files(_APPROVAL_NAMES)]}.values())

    @rule("提交结果四项结论与偏差依据")
    def rule_6(self, ctx: RuleContext):
        facts = self.transcription()
        area = facts.get(RESULTS_AREA[0]) or {}
        if area.get("present") is False:
            return "NG", "零件提交保证书没有“提交结果”区。"
        if area.get("present") is None:
            raise unable("未能从零件提交保证书原件辨识“提交结果”区，需人工复核。")
        missing, unknown, rejected = [], [], []
        for name, label in RESULTS.items():
            result = facts.get(name) or {}
            value = str(result.get("value") or "").strip()
            if result.get("present") is False:
                missing.append(f"{label}（未设该项）")
            elif value and any(word in compact(value) for word in _REJECTED):
                rejected.append(f"{label}“{value}”")
                self.note(name, value)
            elif result.get("checked") is True or (value and compact(value) not in PLACEHOLDERS):
                self.note(name, value or "已勾选")
            elif result.get("checked") is False:
                self.note(name, "未勾选")
                missing.append(f"{label}未勾选、无明确取值")
            else:
                unknown.append(label)
        rejected += [f"提交结果区写有“{text}”" for text in getattr(self, "_results_text", []) if compact(text) in {"ng", "不合格", "不满足"}]
        meets = facts.get(MEETS[0]) or {}
        verdict = None
        if meets.get("present") is False:
            missing.append("“这些结果满足所有图样和规范要求”一项（未设该项）")
        elif meets.get("present") is None or any(option.get("checked") is None for option in meets.get("options") or []):
            unknown.append("“这些结果满足所有图样和规范要求”的是/否勾选")
        else:
            checked = [compact(option.get("label")) for option in meets.get("options") or [] if option.get("checked") is True]
            self.note(MEETS[0], "、".join(checked) or "未勾选")
            if len(checked) != 1:
                missing.append("“这些结果满足所有图样和规范要求”" + ("未勾选“是”或“否”，结论缺失" if not checked else "同时勾选了“是”和“否”"))
            else:
                verdict = checked[0] in {"yes", "是"}
        if verdict is True and rejected:
            return "NG", "零件提交保证书结论与结果矛盾：勾选“是”（满足所有要求），但" + "、".join(rejected) + "。"
        if missing:
            return "NG", "零件提交保证书提交结果区不完整：" + "；".join(missing) + "。"
        if unknown:
            raise unable(f"未能从零件提交保证书原件辨识{'、'.join(unknown)}，需人工复核。")
        if verdict is False or rejected:
            approvals = self.approvals()
            if not approvals:
                self.need("顾客工程批准或偏差许可")
                return "NG", "零件提交保证书未获偏差即提交：" + ("勾选“否”（不满足所有要求）" if verdict is False else "、".join(rejected)) + "，同批次没有顾客工程批准或偏差许可。"
            raise unable(
                f"零件提交保证书{'勾选“否”' if verdict is False else '结果标有不合格'}"
                f"；同批次有{'、'.join(original_name(item) for item in approvals)}，检查程序未能核对其是否覆盖该不合格项，需人工复核。"
            )
        return "PASS", "提交结果区四项结果均已勾选或取值，结论勾选“是”，结果中没有不合格项。"

    # ---- PSW:7 ---------------------------------------------------------------------------------

    def plan_header(self, ctx: RuleContext, item: Any) -> tuple[list[tuple[str, Place]], list[tuple[str, Place]]]:
        """(part numbers, part names) written in a control plan's header, with where each was read."""
        numbers: list[tuple[str, Place]] = []
        names: list[tuple[str, Place]] = []
        with self.reading(ctx, item, ((), (), ())):
            for ws in ctx.workbook.worksheets:
                sheet = Sheet(ctx, ws)
                rows, width = self.extent(sheet)
                cells = self.cells(sheet)
                heading = min((cell.row_index for cell in cells if any(word in compact(self.raw(cell)) for word in CP_HEADING)), default=min(rows, 40))
                for cell in cells:
                    if cell.row_index >= heading:
                        continue
                    raw = self.raw(cell)
                    match = re.match(r"(.*?)[:：](.*)", raw, re.S)
                    label, after = (compact(match.group(1)), match.group(2).strip()) if match else (compact(raw), "")
                    if any(word in label for word in CP_NOT_PART):
                        continue
                    kind = numbers if any(word in label for word in CP_PART_NUMBER) else names if any(word in label for word in CP_PART_NAME) else None
                    if kind is None or len(label) > 40:
                        continue
                    value, at = after, (cell.row_index, cell.col_index)
                    if not value:
                        right = next((other for other in cells if other.row_index == cell.row_index and other.col_index > cell.col_index), None)
                        below = sheet.cell(cell.row_index + 1, cell.col_index)
                        if right is not None and right.col_index - cell.col_index <= 3 and not self.labelish(self.raw(right)):
                            value, at = self.raw(right), (right.row_index, right.col_index)
                        elif below.has_data() and not self.labelish(str(below.value())):
                            value, at = str(below.value()).strip(), (cell.row_index + 1, cell.col_index)
                    if value:
                        kind.append((value, Place.of(f"控制计划《{original_name(item)}》“{ws.title}”{_cell_name(*at)}", sheet, *at)))
        return numbers, names

    @staticmethod
    def labelish(value: str) -> bool:
        raw = str(value or "").strip()
        return raw.endswith((":", "：")) or any(word in compact(raw) for word in CP_LABEL_WORDS)

    @rule("PSW与同批控制计划表头零件号零件名称一致")
    def rule_7(self, ctx: RuleContext):
        plans = self.batch_files(_CP_KIND)
        if not plans:
            return "NA", "同批次未提交控制计划，本条不适用（缺件只在包级报告一次）。"
        reading = _Reading(self)
        number = reading.field("customer_part_number", IDENTITY["customer_part_number"])
        name = reading.field("part_name", IDENTITY["part_name"])
        if reading.unknown and not reading.failed:
            return reading.decided("")
        failures, unsure, matched = [f"零件提交保证书{item}" for item in reading.failed], [], []
        for plan in plans:
            if Path(str(plan.file_url)).suffix.lower() == ".pdf" and self.unread_pages(str(plan.file_url)):
                raise unable(f"控制计划《{original_name(plan)}》为扫描件，检查程序未能读取其表头，需人工复核。")
            numbers, names = self.plan_header(ctx, plan)
            written = {_identity(value) for value, _place in numbers if compact(value) not in PLACEHOLDERS}
            if len(written) != 1:
                raise unable(f"控制计划《{original_name(plan)}》表头未能读出唯一的零件号（读到：{'、'.join(value for value, _place in numbers) or '无'}），需人工复核。")
            theirs, where = numbers[0]
            self.named(where)
            if number is not None and _identity(number) != next(iter(written)):
                self.need("控制计划", part=number, found=theirs)
                failures.append(f"客户零件号“{number}”与{where}零件号“{theirs}”不一致（比对源错配）")
            plan_names = [(value, place) for value, place in names if compact(value) not in PLACEHOLDERS]
            if not plan_names:
                raise unable(f"控制计划《{original_name(plan)}》表头未能读出零件名称，需人工复核。")
            title, at = plan_names[0]
            self.named(at)
            if name is None:
                continue
            ours, other = compact(name), compact(title)
            cjk = lambda text: re.sub(r"[^一-鿿]", "", text)  # noqa: E731
            if ours == other or (cjk(ours) and cjk(ours) == cjk(other)):
                matched.append(f"零件名称“{name}”与{at}“{title}”一致")
            elif cjk(ours) and cjk(other) and (cjk(ours) in cjk(other) or cjk(other) in cjk(ours)):
                unsure.append(f"零件名称“{name}”与{at}“{title}”字面不同、一方包含另一方，是否属简称扩展")
            else:
                failures.append(f"零件名称“{name}”与{at}“{title}”不一致")
        if failures:
            return "NG", "零件提交保证书与同批次控制计划表头不一致：" + "；".join(failures) + "。"
        if unsure:
            raise unable("零件号一致；" + "；".join(unsure) + "，需人工判断。")
        return "PASS", f"零件提交保证书客户零件号“{number}”与同批次控制计划零件号一致；" + "；".join(matched) + "。"
