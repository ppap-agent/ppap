"""UAES U12: special characteristic lists against UAES v1.0 revision 3 (SCL:1–SCL:3).

`UaesSpecialCharacteristicsRevision3` adds the clause methods below to the UAES special characteristic list reader.
Every clause first filters the list to this part number ("本件号") and decides on those rows only. Signatures and
stamps exist only as an image of the original: they are read as yes/no facts by the image model, and code decides.
"""

from __future__ import annotations

import re
import unicodedata
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes import special_characteristics as _reader
from audit.checker_lib.uaes.special_characteristics import _NAMES, UaesSpecialCharacteristicsAgent, characteristic_rows, compact, filled, unable
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet, Table
from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

_PART = re.compile(r"(?<![0-9A-Z])F[0-9A-Z]{9}(?![0-9A-Z])")
_PART_LABELS = ("零件号", "零件编号", "物料编号", "partnumber", "partno", "产品件号", "件号", "drawingno", "图号")
_ORDINARY = {"", "/"}
_SAFETY = {"cc", "sc/1", "1", "s", "★", "安全", "关键"}
_MAJOR = {"sc", "sc/2", "2", "d", "f", "重要", "主要", "跑道框"}
_KNOWN = _SAFETY | _MAJOR | {"yc", "ys", "3", "5"}
_TOKEN = re.compile(r"sc/[12]|安全|关键|重要|主要|跑道框|[a-z]+|\d+|[^\s,，、;；/]")
_SERIAL = re.compile(r"^\s*\d+(?:\s*-\s*\d+)?\s*[#＃]\s*(?:-\s*\d+)?")
_CODE = r"(?:[1235sdf]|cc|sc(?:/[12])?|yc|ys|★)"
_CLASS_SUFFIX = re.compile(rf"(?<=[一-鿿\s,，、]){_CODE}(?:[\s,，、]+{_CODE})*\s*(?=[（(]|$)", re.I)

# The header fields SCL:1 names, by the labels the lists write them under.
HEADER_FIELDS = {
    "零件号": ("零件号", "零件编号", "物料编号", "partno", "partnumber", "产品件号", "code"),
    "零件名称": ("零件名称", "产品名称", "物料名称", "partname", "name"),
    "版次或日期": ("版次", "版本", "rev", "日期", "date"),
    "编制人": ("编制", "prepared", "madeby", "编制人"),
}


def _norm(value: Any) -> str:
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold()


def parts_in(value: Any) -> set[str]:
    return set(_PART.findall(unicodedata.normalize("NFKC", "" if value is None else str(value)).upper().replace(" ", "")))


def mark_tokens(value: Any) -> list[str]:
    """The class codes a mark writes; codes side by side are separated by commas or spaces alike ("3 5" is 3 and 5)."""
    text = ",".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split())
    return _TOKEN.findall(_norm(text).replace("／", "/"))


def mark_class(value: Any) -> frozenset[str]:
    return frozenset("安全/关键级" if token in _SAFETY else "重要/主要级" if token in _MAJOR else token for token in mark_tokens(value))


def characteristic_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value)).split("\n")[0]
    return _norm(_CLASS_SUFFIX.sub("", _SERIAL.sub("", text).strip()))


def self_declared_class(value: Any) -> str:
    """The class a numbered name writes after itself: "3#孔径  3,5" is 3,5."""
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    match = re.search(r"^\s*\d+(?:-\d+)?#.*?[一-鿿]\s*((?:[1235])(?:\s*[,，、\s]\s*[1235])*)\s*(?:[（(].*)?$", text)
    return match.group(1) if match else ""


# Geometric tolerance symbols as the list writes them in words (the drawing transcription is asked for the words).
_GEOMETRY = {
    "∥": "平行度",
    "//": "平行度",
    "⊕": "位置度",
    "⌖": "位置度",
    "⊥": "垂直度",
    "◎": "同轴度",
    "⏥": "平面度",
    "▱": "平面度",
    "○": "圆度",
    "⌭": "圆柱度",
    "↗": "圆跳动",
    "⌰": "全跳动",
    "⌯": "对称度",
    "∠": "倾斜度",
    "⌒": "线轮廓度",
    "⌓": "面轮廓度",
}


def tolerance_key(value: Any) -> str:
    """Compare the whole specification; presentation changes never erase datums, their order or quantities."""
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    for symbol, word in _GEOMETRY.items():
        text = text.replace(symbol, word)
    if any(word in text for word in _GEOMETRY.values()):
        text = re.sub(r"(?<=[a-z])\s+(?=[a-z](?:\b|$))", "|", text, flags=re.I)
    text = _norm(text).replace("×", "x")
    text = re.sub(r"[φø⌀∅]", "φ", text)
    return re.sub(r"\+(\d+(?:\.\d+)?)/-\1(?![\d.])", r"±\1", text)


class Feature(NamedTuple):
    where: str
    name: str
    spec: str
    mark: str
    position: str = ""


class Sight(NamedTuple):
    page: int
    bbox: tuple[float, ...]  # PDF points
    tile: int
    reading: int
    feature: Feature
    frame: tuple[float, ...]  # the associated dimension, not a neighbouring stacked frame
    native: bool
    located: bool


_NULL_TXT = {"type": ["string", "null"]}
_DRAWING_PROMPT = (
    "转写零件图纸裁片中所有带特性类别小框/旗标的实际尺寸或形位公差，不含图例或标题栏示例。只转写原件，不做审核。"
    "bbox 仅框住该特性旁的类别小框/旗标，frame 只框住与该标记相连的尺寸或形位框，不含标记，切勿借上下相邻框的标识。两框都按本裁片从左上到右下的[x0,y0,x1,y1]千分坐标；两处相同尺寸须分别列出。"
    "dimension 保留数量、尺寸、公差、形位符号及完整有序基准，如 4X 位置度 Φ0.1 A-B G。"
    "name 单独转写该特性的文字名称或形位框首格符号（⌖写位置度），不得用基本尺寸、公差数字、序号、类别或位置代替名称。"
    "mark 原样转写类别码；view 原样转写该视图或区域；若裁片截断或不确定字段写null，明确原处没写名称才写空串。"
    "complete 仅当本裁片覆盖范围中的标记均已逐一读清时为 true。"
)
_DRAWING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "complete"],
    "properties": {
        "complete": {"type": "boolean"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["bbox", "frame", "dimension", "name", "mark", "view"],
                "properties": {
                    **{key: {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4} for key in ("bbox", "frame")},
                    "dimension": _NULL_TXT,
                    "name": _NULL_TXT,
                    "mark": _NULL_TXT,
                    "view": _NULL_TXT,
                },
            },
        },
    },
}


def same_box(a, b):
    """Two proposed locations for the actual mark frame, not two sightings of the same tolerance in a tile."""
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return False
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - w * h
    return w * h / union >= 0.5


def page_box(box, coords):
    if not isinstance(coords, list) or len(coords) != 4 or not all(isinstance(i, (int, float)) for i in coords):
        return ()
    if not (0 <= coords[0] < coords[2] <= 1000 and 0 <= coords[1] < coords[3] <= 1000):
        return ()
    return tuple(round((box.x0 + v * box.width / 1000) if i % 2 == 0 else (box.y0 + v * box.height / 1000), 2) for i, v in enumerate(coords))


def attached(mark, frame):
    """A flag beside the corresponding frame, not the same symbol beside the row above or below."""
    if not mark or not frame or same_box(mark, frame):
        return False
    for axis, cross in [(0, 1), (1, 0)]:
        h = min(mark[cross + 2] - mark[cross], frame[cross + 2] - frame[cross])
        overlap = min(mark[cross + 2], frame[cross + 2]) - max(mark[cross], frame[cross])
        gap = max(mark[axis], frame[axis]) - min(mark[axis + 2], frame[axis + 2])
        if overlap >= 0.5 * h and -0.35 * h <= gap <= h:
            return True
    return False


def native_at(box, words):
    return " ".join(w[4] for w in words if box[0] <= (w[0] + w[2]) / 2 <= box[2] and box[1] <= (w[1] + w[3]) / 2 <= box[3])


def pair_keys(feature):
    name, spec = characteristic_name(feature.name), tolerance_key(feature.spec)
    # A number or dimension repeated in the name field is not an independent name.
    if not re.search(r"[a-z一-鿿]", name) or tolerance_key(name) == spec:
        name = ""
    return (name, spec)


def transcription(feature):
    # On one original "SC/1" and "CC" are competing transcriptions, even though they are the same *level* across originals.
    return _norm(feature.name), tolerance_key(feature.spec), tuple(mark_tokens(feature.mark)), _norm(feature.position)


def pair(feature, peers):
    """Two independent keys first; a source region or process position can only resolve a remaining tie."""
    name, spec = pair_keys(feature)
    pos = _norm(feature.position)
    if not name or not spec:
        return []
    hits = [i for i, p in enumerate(peers) if pair_keys(p) == (name, spec)]
    if len(hits) > 1 and pos:
        exact = [i for i in hits if _norm(peers[i].position) == pos]
        if len(exact) == 1 and all(_norm(peers[i].position) for i in hits):
            return exact
    return hits


def valid_mark(mark):
    tokens = mark_tokens(mark)
    return bool(tokens) and all(token in _KNOWN for token in tokens)


def described(f):
    return f"{f.where} 名称“{f.name}”、规格“{f.spec}”、类别“{f.mark}”" + (f"、区域/工序“{f.position}”" if f.position else "")


def source_numbers(value):
    # A labelled reference may carry a longer material number; its suffix is significant.
    return set(re.findall(r"(?<![0-9A-Z])F[0-9A-Z]{9,}(?![0-9A-Z])", unicodedata.normalize("NFKC", str(value or "")).upper().replace(" ", "")))


def page_scanned(page):
    import fitz

    return not page.get_text().strip() or any(abs(fitz.Rect(i["bbox"]) & page.rect) >= 0.8 * abs(page.rect) for i in page.get_image_info())


_REVISION_LABEL = re.compile(r"改版理由|revisionreason|修改说明|修订说明|变更履历")


def list_rows(agent: Any, ctx: RuleContext) -> list[tuple[str, Any, dict]]:
    """The characteristic rows, without a change-history block that sits under the table (V040 writes its records
    above their own label row "序号No. 改版理由Revision reason 日期Date …")."""
    rows = characteristic_rows(agent, ctx)
    kept = []
    for title, row, columns in rows:
        sheet = row.ws
        label = None
        for line in range(row.row_index, min(sheet.max_row, row.row_index + 8) + 1):
            if any(_REVISION_LABEL.search(_norm(cell.value)) for cell in sheet[line] if cell.value is not None):
                label = line
                break
        if label is not None and all(any(cell.value not in (None, "") for cell in sheet[line]) for line in range(row.row_index, label)):
            continue
        kept.append((title, row, columns))
    return kept


def original_name(item: Any) -> str:
    return str((getattr(item, "file_metadata", None) or {}).get("original_filename") or Path(str(item.file_url)).name)


def carries_pictures(path: str) -> bool:
    """Whether a workbook original holds any picture or drawing; an .xls is not inspected and may hold one."""
    if not path.lower().endswith((".xlsx", ".xlsm")):
        return True
    with zipfile.ZipFile(path) as archive:
        return any(name.startswith(("xl/media/", "xl/drawings/", "xl/embeddings/")) for name in archive.namelist())


def _pgqp1_document(models: Any) -> tuple:
    """The PGQP1 important characteristic list as the UAES PGQP1 resource declares it."""
    rows, columns = models.WHOLE_SHEET_ROWS, models.WHOLE_SHEET_COLS
    return (
        (SheetModel(canonical_name="PGQP1", include_keywords=("pgqp1",), match_mode="exact"),),
        (
            SectionModel(
                header_search_max_row=rows,
                header_search_max_col=columns,
                canonical_name="重要特性区",
                sheet_name="PGQP1",
                header_anchors=("特性项目", "特殊特性", "测量设备"),
                header_hit=3,
                header_compact_match=True,
                row_anchors=("特性项目", "特殊特性"),
                row_anchor_match="any",
                row_stop_on_first_blank=False,
                row_max_blank_streak=rows,
                row_stop_markers=("5. Product Audit", "产品审核"),
            ),
        ),
        (
            ColumnModel(canonical_name="特性项目", section_name="重要特性区", aliases=("Items/项目",)),
            ColumnModel(canonical_name="特殊特性", section_name="重要特性区", aliases=("SC/特殊特性",)),
            ColumnModel(canonical_name="规格公差", section_name="重要特性区", aliases=("特性值", "公差", "规格/公差", "规格公差", "Specification", "Tolerance")),
            ColumnModel(canonical_name="位置", section_name="重要特性区", aliases=("图纸区域", "工序位置", "Drawing zone", "Process position")),
            ColumnModel(canonical_name="测量设备", section_name="重要特性区", aliases=("Measuring Equipment\n测量设备",)),
        ),
    )


class _Revision3(UaesSpecialCharacteristicsAgent):
    """Revision 3 clause methods over the UAES special characteristic list reader; `self._m` is its module."""

    _m: Any
    _pgqp1: Any

    # ---- this part number and the rows of it ------------------------------------------------------------------

    def own_part(self, ctx: RuleContext) -> tuple[str | None, str]:
        """(this part number, how it was found): the list's labelled header, its only per-row part, else the one
        part number the other submitted (not reference) originals of this round state."""
        header = set()
        for sheet in (Sheet(ctx, ws) for ws in ctx.workbook.worksheets):
            for value in self.labelled(sheet, self._m.resources.PART_NUMBER):
                header |= parts_in(value)
        if len(header) == 1:
            return next(iter(header)), "清单表头零件号"
        rows = list_rows(self, ctx)
        per_row = set().union(*(parts_in(row.value("零件")) for _title, row, columns in rows if "零件" in columns)) if rows else set()
        if len(per_row) == 1:
            return next(iter(per_row)), "清单各行零件号"
        stated = self.batch_parts()
        if len(stated) == 1:
            return next(iter(stated)), "同批次其他提交文件写明的零件号"
        # Several numbers in the batch: the one the list itself carries rows for, matched letter for letter.
        if per_row and len(per_row & stated) == 1:
            return next(iter(per_row & stated)), f"同批次其他提交文件写明的零件号（{'、'.join(sorted(stated))}）中清单有行的那一个"
        found = "、".join(sorted(header | per_row)) or "无"
        others = "、".join(sorted(stated)) or "无"
        return None, f"清单为多件号混装（读到{found}），同批次其他提交文件写明的零件号为{others}，不能确定本件号"

    def batch_parts(self) -> set[str]:
        if self.group_context is None:
            return set()
        found: set[str] = set()
        for item in self.group_context.by_audit_id.values():
            if item.audit_id == self.audit_file.audit_id or (item.file_metadata or {}).get("role") == "reference":
                continue
            try:
                found |= labelled_parts(str(item.file_url))
            except Exception:
                continue
        return found

    def scoped(self, ctx: RuleContext) -> tuple[str, list[tuple[str, Any, dict]], str]:
        part, how = self.own_part(ctx)
        if part is None:
            if reason := self.unreadable():
                raise unable(f"{reason}，尚不能确定本件号，需人工复核。")
            raise unable(f"{how}，尚不能按本件号过滤行范围，需人工复核。")
        rows = list_rows(self, ctx)
        kept = [(title, row, columns) for title, row, columns in rows if "零件" not in columns or part in "".join(parts_in(row.value("零件")))]
        if any("零件" not in columns for _t, _r, columns in rows):
            header = set()
            for sheet in (Sheet(ctx, ws) for ws in ctx.workbook.worksheets):
                for value in self.labelled(sheet, self._m.resources.PART_NUMBER):
                    header |= parts_in(value)
            if header and part not in header:
                kept = [entry for entry in kept if "零件" in entry[2]]
        return part, kept, f"按本件号{part}过滤（本件号取自{how}），参与判定{len(kept)}行"

    # ---- SCL:1 ------------------------------------------------------------------------------------------------

    @rule("物料字段、表头字段与签署闭环")
    def rule_1(self, ctx: RuleContext):
        # Header fields and signatures are checked once, whatever the rows of this part; the material fields per row
        # wait for the part filter.
        try:
            part, rows, scope = self.scoped(ctx)
            open_scope = ""
        except GuardStop as stop:
            if stop.decision != Decision.UNABLE:
                raise
            rows, scope, open_scope = [], "本件号未能确定", stop.message
        problems: list[str] = []
        # Material name and number: on every kept row ("产品名称/零件号"), or as labelled header fields.
        per_row = [(title, row) for title, row, columns in rows if "零件" in columns]
        if open_scope:
            pass
        elif per_row:
            for title, row in per_row:
                text = unicodedata.normalize("NFKC", str(row.value("零件") or ""))
                name = _PART.sub("", text.upper())
                if not parts_in(text) or not re.search(r"[一-鿿A-Za-z]{2,}", name):
                    self.cite(row.ws, row.row_index, row.columns["零件"].header_col)
                    problems.append(f"“{title}”第{row.row_index}行产品名称/零件号“{text.strip()}”缺物料{'编号' if not parts_in(text) else '名称'}")
                    break
        else:
            names = [value for sheet in (Sheet(ctx, ws) for ws in ctx.workbook.worksheets) for value in self.labelled(sheet, self._m.resources.PART_NAME) if compact(value)]
            numbers = [value for sheet in (Sheet(ctx, ws) for ws in ctx.workbook.worksheets) for value in self.labelled(sheet, self._m.resources.PART_NUMBER) if compact(value)]
            missing = [label for label, values in (("物料名称", names), ("物料编号", numbers)) if not values]
            if missing:
                problems.append(f"物料{'、'.join(missing)}字段不存在或内容为空")
        # Header fields: the rows above the characteristic rows, the table header included.
        sheets = Table(ctx).find_sheets("特殊特性清单", on_missing=Decision.IGNORE)
        if sheets:
            sheet = sheets[0]
            top = self.section(sheet, "特殊特性区").resolved.header_row
            _rows, columns = self.extent(sheet)
            texts = [_norm(cell.value()) for cell in sheet.scan_non_empty(row_min=1, row_max=top, col_min=1, col_max=columns)]
            absent = [field for field, labels in HEADER_FIELDS.items() if not any(any(_norm(label) in text for label in labels) for text in texts)]
            if absent:
                problems.append(f"表头（第1～{top}行）缺{'、'.join(absent)}字段")
        elif reason := self.unreadable():
            raise unable(f"{reason}，表头与签署尚未读取，需人工复核。")
        if problems:
            return "NG", f"{scope}。" + "；".join(problems) + "。" + ("（各行物料字段待本件号确定后核对）" if open_scope else "")
        if open_scope:
            raise unable(f"表头字段齐全；{open_scope}")
        # Signatures: an Excel original without any picture can carry neither a handwritten signature nor a stamp.
        path = str(self.audit_file.file_url)
        if not path.lower().endswith(".pdf"):
            try:
                pictures = carries_pictures(path)
            except Exception:
                pictures = True
            if not pictures:
                return "NG", f"{scope}。物料字段与表头字段齐全；原件没有任何图片，编制、审核、批准栏没有手写签字或电子签名图层，也没有印章，签署不闭环。"
        fields = {
            "prepared": "供应商编制栏有手写签字（或电子手写签名图层），不是打印体姓名",
            "checked": "供应商审核栏有手写签字（或电子手写签名图层），不是打印体姓名",
            "approved": "供应商批准栏有手写签字（或电子手写签名图层），不是打印体姓名",
            "stamp": "签署区至少有一处印章（受控章或公章，含电子签章图层）",
        }
        status, basis = self.visual(fields, "编制、审核、批准签字与印章")
        return status, f"{scope}；物料字段与表头字段齐全；签署：{basis}"

    # ---- SCL:2 ------------------------------------------------------------------------------------------------

    @rule("四类列与行内必填")
    def rule_2(self, ctx: RuleContext):
        sheets = Table(ctx).find_sheets("特殊特性清单", on_missing=Decision.IGNORE)
        if not sheets:
            if reason := self.unreadable():
                raise unable(f"{reason}，尚不能确认特殊特性清单的四类列，需人工复核。")
            return "NG", "原件中未见编号/序号、特性名称、特性及公差、特性类型四类列表头，缺列不通过。"
        for sheet in sheets:
            columns = self.section(sheet, "特殊特性区").columns(optional=("特性编号", *_NAMES, "特性值", "特性类别"))
            missing = [
                label
                for name, label in (("特性编号", "编号/序号"), ("特性名称", "特性名称"), ("特性值", "特性及公差"), ("特性类别", "特性类型/类别"))
                if name not in columns and not (name == "特性名称" and "过程特性名称" in columns)
            ]
            if missing:
                return "NG", f"特殊特性清单“{sheet.title}”缺少{'、'.join(missing)}列。"
        part, rows, scope = self.scoped(ctx)
        if not rows:
            return "NG", f"{scope}：清单中没有本件号的特性行，应填内容缺失。"
        problems, ordinary = [], []
        for title, row, columns in rows:
            name = next((str(row.value(column)).strip() for column in _NAMES if filled(row, column, columns)), "")
            value = str(row.value("特性值")).strip() if filled(row, "特性值", columns) else ""
            mark = str(row.value("特性类别") or "").strip() if "特性类别" in columns else ""
            where = f"“{title}”第{row.row_index}行" + (f"（{name or value}）" if name or value else "")
            blank = [label for present, label in ((filled(row, "特性编号", columns), "编号/序号"), (bool(name), "特性名称"), (bool(value), "特性及公差")) if not present]
            controlled = bool(self_declared_class(name)) or _norm(mark) not in _ORDINARY
            if _norm(mark) in _ORDINARY:
                if controlled:
                    blank.append("特性类型/类别（名称自标类别" + self_declared_class(name) + "）")
                else:
                    ordinary.append(f"第{row.row_index}行")
            if blank:
                column = {"编号/序号": "特性编号", "特性及公差": "特性值"}.get(blank[0], "特性类别" if blank[0].startswith("特性类型") else next((c for c in _NAMES if c in columns), None))
                if column in columns:
                    self.cite(row.ws, row.row_index, columns[column].header_col)
                problems.append(f"{where}{'、'.join(blank)}为空")
        if problems:
            return "NG", f"{scope}。" + "；".join(problems[:5]) + (f"等{len(problems)}行" if len(problems) > 5 else "") + "。"
        note = f"；{('、'.join(ordinary[:5]))}为仅作过程参考的普通行，特性类别留空或“/”，按已按规填写计（留痕）" if ordinary else ""
        return "PASS", f"{scope}；四类列齐全，各行编号、特性名称、特性及公差、特性类别均已填写{note}。"

    # ---- SCL:3 ------------------------------------------------------------------------------------------------

    def drawings(self):
        """A4 is no less eligible than A0. Positive form identification excludes unrelated untyped PDFs."""
        if not self.group_context:
            return []
        known = {}
        for kind, items in self.group_context.by_doc_type.items():
            if kind and _norm(kind) not in {"unknown", "未知"}:
                for item in items:
                    known[item.audit_id] = known.get(item.audit_id, False) or bool(re.search(r"图纸|图样|drawing", str(kind), re.I))
        return [
            (item, known.get(item.audit_id, False))
            for item in self.group_context.by_audit_id.values()
            if item.audit_id != self.audit_file.audit_id and (known.get(item.audit_id) or (item.audit_id not in known and Path(str(item.file_url)).suffix.lower() == ".pdf"))
        ]

    def drawing_identity(self, item, typed):
        """Read the drawing's own number, never a number from its BOM, filename, or arbitrary annotation."""
        import hashlib

        import fitz

        path = Path(str(item.file_url))
        metadata = getattr(item, "file_metadata", None) or {}
        digest = metadata.get("source_sha256") or metadata.get("sha256")
        if digest and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise GuardStop(Decision.ERR, "冻结图纸内容已变化，尚未取得业务结论。")
        if path.suffix.lower() != ".pdf":
            return "open", set(), "未读取该格式的图纸"
        with fitz.open(str(path)) as doc:
            page = doc[0]
            if page_scanned(page) or page.rotation:
                return "open", set(), "首页无可用原生图文或坐标未核实，未转写"
            text = page.get_text()
            lines = {_norm(line) for line in text.splitlines()}
            if lines & {"零件清单", "partslist", "billofmaterials"} and lines & {"qty", "数量", "所属图纸", "related-drawing"}:
                return "other", set(), "零件清单，不是零件图"
            # Read an explicitly labelled DWG No. (not a reference drawing in another form).
            name_label = re.compile(r"^\s*(?:图号\s*/\s*)?(?:DWG\.?\s*No\.?|图号)\s*[:：]?\s*(.+)$", re.I)
            native = []
            for block in page.get_text("dict", flags=0)["blocks"]:
                for line in block.get("lines", []):
                    value = "".join(span["text"] for span in line["spans"])
                    if hit := name_label.match(value):
                        if numbers := source_numbers(hit[1]):
                            native.append((numbers, line["bbox"], value))
            form = typed or bool(re.search(r"SECTION\s+[A-Z]\s*-\s*[A-Z]|剖视图|技术要求|GENERAL\s+TOLERANCES", text, re.I))
            version = str(metadata.get("file_version_id") or item.audit_id)
            if native and form:
                for _, bbox, value in native:
                    self.legacy().witness({"kind": "pdf_region", "page": 1, "bbox": list(bbox), "fact": "drawing_number"}, value, version)
                return "drawing", set().union(*(n for n, _, _ in native)), "首页图号栏"
            if not form:
                # Some originals use strokes rather than characters for the drawing/title. Ask for visible facts,
                # never whether the file should be audited. Without a positive form, keep the candidate unresolved.
                schema = {"type": "object", "additionalProperties": False, "required": ["views", "heading"], "properties": {"views": {"type": "boolean"}, "heading": _NULL_TXT}}
                result = self.transcribe(
                    page.get_pixmap(matrix=fitz.Matrix(1600 / max(page.rect.width, page.rect.height), 1600 / max(page.rect.width, page.rect.height))).tobytes("png"),
                    "只读取本页：views 是否可见带尺寸线的零件投影视图或剖面；heading 是文件本身的标题，不填相关资料名称，无标题写null。",
                    schema,
                    "可能的图纸首页",
                )
                if result.get("views") is not True:
                    if _norm(result.get("heading")) in {"零件清单", "partslist", "billofmaterials", "采购件质量计划", "控制计划", "controlplan"}:
                        return "other", set(), "首页为另一种表单"
                    return "open", set(), "首页未确认零件图身份"
        return "drawing", source_numbers(self.drawing_title(item).get("drawing_number")), "首页标题栏"

    def drawing_marks(self, item):
        """Return only physically located, consistent observations. An incomplete page is not negative evidence."""
        import contextvars
        from concurrent.futures import ThreadPoolExecutor

        import fitz

        version = str((getattr(item, "file_metadata", None) or {}).get("file_version_id") or item.audit_id)
        jobs, pending, sights, words = [], [], [], {}
        docname = original_name(item)
        with fitz.open(str(item.file_url)) as doc:
            for page in doc:
                p = page.number + 1
                if page_scanned(page) or page.rotation:
                    pending.append(f"{docname} p{p}未读取（无可用文字层/扫描页，或页面坐标未核实）")
                    continue
                words[p] = page.get_text("words")
                nc, nr = max(1, round(page.rect.width / 850)), max(1, round(page.rect.height / 800))
                w, h = page.rect.width / nc, page.rect.height / nr
                pad = 0.06 * max(w, h)
                for r in range(nr):
                    for c in range(nc):
                        box = fitz.Rect(c * w - pad, r * h - pad, (c + 1) * w + pad, (r + 1) * h + pad) & page.rect
                        image = page.get_pixmap(dpi=min(220, max(1, int(2400 * 72 / max(box.width, box.height)))), clip=box).tobytes("png")
                        for k in (1, 2):
                            jobs.append((p, r * nc + c + 1, k, box, image))

        def read(job):
            p, tile, k, box, image = job
            try:
                return self.transcribe(image, _DRAWING_PROMPT, _DRAWING_SCHEMA, f"{docname} p{p}块{tile}读取{k}"), ""
            except GuardStop as stop:
                return None, stop

        errors = 0
        if jobs:
            with ThreadPoolExecutor(max_workers=min(12, len(jobs))) as pool:
                futures = [pool.submit(contextvars.copy_context().run, read, job) for job in jobs]
                for (p, tile, k, box, _), future in zip(jobs, futures, strict=True):
                    result, why = future.result()
                    at = f"{docname} p{p}块{tile}读取{k}"
                    if result is None:
                        errors += why.decision == Decision.ERR
                        pending.append(f"{at}未取得转写：{why.message}")
                        continue
                    if result.get("complete") is not True:
                        pending.append(f"{at}覆盖未核全")
                    for e in result.get("items") or []:
                        b, frame = (page_box(box, e.get(key)) for key in ("bbox", "frame"))
                        if not b:
                            pending.append(f"{at}有未定位标记")
                            continue
                        where = f"{docname} p{p} 框{b}"
                        f = Feature(where, str(e.get("name") or "").strip(), str(e.get("dimension") or "").strip(), str(e.get("mark") or "").strip(), str(e.get("view") or "").strip())
                        # A null/incomplete label/unknown mark at a real location also invalidates other reads there.
                        located = all(e.get(key) is not None for key in ("dimension", "name", "mark")) and valid_mark(f.mark) and attached(b, frame)
                        native = False
                        if located:
                            native_mark, native_spec = native_at(b, words[p]), tolerance_key(native_at(frame, words[p]))
                            agrees = mark_tokens(native_mark) == mark_tokens(f.mark)
                            if valid_mark(native_mark) and not agrees:
                                located = False
                            native = bool(f.name and f.spec and agrees and native_spec in {tolerance_key(f.spec), tolerance_key(f.name + f.spec)} and characteristic_name(f.name) in native_spec)
                        sights.append(Sight(p, b, tile, k, f, frame, native, located))
        if jobs and errors == len(jobs):
            raise GuardStop(Decision.ERR, "图纸原件转写请求均失败，尚未取得业务结论。")
        groups = []
        for s in sights:
            overlaps = [g for g in groups if g[0].page == s.page and any(same_box(t.bbox, s.bbox) for t in g)]
            group = [s]
            for g in overlaps:
                groups.remove(g)
                group.extend(g)
            groups.append(group)
        confirmed = []
        for group in groups:
            first = group[0]
            passes = sorted({(s.tile, s.reading) for s in group})
            # Duplicate entries from one request are not votes. Different physical places never vote for each other.
            stable = (
                (any(s.native for s in group) or any({s.reading for s in group if s.tile == t.tile} == {1, 2} for t in group))
                and len(passes) == len(group)
                and all(
                    s.located and transcription(s.feature) == transcription(first.feature) and all(same_box(s.bbox, t.bbox) and s.frame and t.frame and same_box(s.frame, t.frame) for t in group)
                    for s in group
                )
            )
            for s in group:
                self.legacy().witness(
                    {
                        "kind": "pdf_region",
                        "page": s.page,
                        "bbox": list(s.bbox),
                        "coordinate_system": "pdf_points_unrotated",
                        "tile": s.tile,
                        "reading": s.reading,
                        "feature_bbox": list(s.frame),
                        "native_match": s.native,
                        "read_located": s.located,
                        "fact": "drawing_special_mark",
                    },
                    described(s.feature),
                    version,
                    visual=True,
                )
            if stable:
                confirmed.append(first.feature)
            else:
                pending.append(f"{first.feature.where} 同一标记的位置、名称、规格或类别未稳定读实（块/轮{passes}）")
        return confirmed, pending

    def pgqp_rows(self, ctx, sheet, plan):
        section = self.section(sheet, "重要特性区")
        cols = section.columns(optional=("特性项目", "特殊特性", "测量设备", "规格公差", "位置"))
        result, pending = [], []
        if not {"特性项目", "特殊特性"} <= cols.keys():
            return [], [f"PGQP1“{original_name(plan)}”重要特性表未定位"]
        for row in section.rows_from(cols):
            source = f"PGQP1“{original_name(plan)}”{sheet.title}!{row.row_index}"
            name, mark = str(row.value("特性项目") or "").strip(), str(row.value("特殊特性") or "").strip()
            if _norm(name) in {"items/项目", "项目", "items"}:
                continue
            if not name and not mark:
                continue
            if not valid_mark(mark):
                pending.append(f"{source}未读实该行的分类符号（原值“{mark}”），不作已配对项")
                continue
            spec = str(row.value("规格公差") or "").strip() if "规格公差" in cols else ""
            pos = str(row.value("位置") or "").strip() if "位置" in cols else ""
            if not spec and "规格公差" not in cols:
                # No located field does not establish that the original has no specification (e.g. embedded graphic).
                pending.append(f"{source}“{name}”类别“{mark}”已读，独立的规格公差值未定位，未按名称单键配对")
                continue
            places = [cols[c].header_col for c in ("特性项目", "规格公差", "特殊特性", "位置") if c in cols]
            coords = [row.ws.cell(row.row_index, col).coordinate for col in places]
            source = f"PGQP1“{original_name(plan)}”{sheet.title}!{'/'.join(coords)}"
            if len(result) < 2:
                for col in places:
                    self.cite(row.ws, row.row_index, col)
            result.append(Feature(source, name, spec, mark, pos))
        return result, pending

    @rule("特殊特性与图纸、PGQP1三方一致")
    def rule_3(self, ctx: RuleContext):
        part, rows, scope = self.scoped(ctx)
        failures: list[Any]
        pending, notes, failures, sources = [], [], [], []
        same_plans, same_drawings, mismatched, possible_draw = 0, 0, [], False
        plans = self.batch_files("E12")
        for plan in plans:
            if str(plan.file_url).lower().endswith(".pdf"):
                import fitz

                with fitz.open(str(plan.file_url)) as doc:
                    if any(page_scanned(page) for page in doc):
                        pending.append(f"PGQP1“{original_name(plan)}”含扫描页，未证明全表已阅")
            with self.reading(ctx, plan, self._pgqp1):
                sheets = Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE)
                if not sheets:
                    pending.append(f"已提交PGQP1“{original_name(plan)}”，未定位PGQP1页签")
                for sheet in sheets:
                    numbers = set().union(*(source_numbers(v) for v in self.labelled(sheet, ("Part No/零件号",))))
                    label = f"PGQP1“{original_name(plan)}”"
                    if len(numbers) != 1:
                        pending.append(f"{label}本件号未核实（读到{'、'.join(sorted(numbers)) or '空'}）")
                    elif part not in numbers:
                        mismatched.append(f"{label}表头件号{next(iter(numbers))}")
                    else:
                        same_plans += 1
                        try:
                            data, gaps = self.pgqp_rows(ctx, sheet, plan)
                        except GuardStop as stop:
                            if stop.decision == Decision.ERR:
                                raise
                            data, gaps = [], [f"{label}特性区未定位：{stop.message}"]
                        sources.append(("PGQP1", data))
                        pending.extend(gaps)
        for source, typed in self.drawings():
            label = f"“{original_name(source)}”"
            try:
                kind, numbers, basis = self.drawing_identity(source, typed)
            except GuardStop as stop:
                if stop.decision == Decision.ERR:
                    raise
                kind, numbers, basis = "open", set(), stop.message
            if kind == "other":
                continue
            possible_draw = True
            if kind == "open" or len(numbers) != 1:
                pending.append(f"{label}的图纸身份或本件图号未核实（{basis}），不作已缺件/已错配")
            elif part not in numbers:
                mismatched.append(f"图纸{label}{basis}图号{next(iter(numbers))}")
            else:
                same_drawings += 1
                data, gaps = self.drawing_marks(source)
                sources.append(("图纸", data))
                pending.extend(gaps)
        if not plans and not possible_draw:
            return "NA", f"{scope}。同批次未提交图纸与PGQP1，本条不适用，不记作通过，缺件只在包级计一次。"
        if mismatched:
            pending.append(f"比对源错配：{'；'.join(mismatched)}，与本件号{part}不同，不逐格比较异号源；错号来源对整条结论的影响待澄清")
        if not plans:
            notes.append("同批次未提交PGQP1，以已确认同号的图纸为唯一基准")
        if plans and not possible_draw:
            pending.append("提交PGQP1但未提交图纸；这一组合的整条结论待澄清")
        list_gap = self.unreadable()
        if not list_gap and not rows:
            sheets = Table(ctx).find_sheets("特殊特性清单", on_missing=Decision.IGNORE)
            if not sheets or not all(self.section(sheet, "特殊特性区").columns(optional=("特性名称", "过程特性名称", "特性值", "特性类别")) for sheet in sheets):
                list_gap = "未定位清单特性行范围"
        if list_gap:
            pending.append(list_gap)
        items, cells = [], []
        for title, row, columns in rows:
            names = [c for c in _NAMES if filled(row, c, columns)]
            name = str(row.value(names[0])).strip() if names else ""
            spec = str(row.value("特性值") or "").strip() if "特性值" in columns else ""
            mark = str(row.value("特性类别") or "").strip() if "特性类别" in columns else ""
            region = ""
            if columns:
                # A region named by the list itself; N/A is not a tie breaker.
                for cell in row.ws[row.section.header_row]:
                    if _norm(cell.value) in {"图纸区域", "drawingzone", "工序位置", "processposition"}:
                        value = str(row.ws.cell(row.row_index, cell.column).value or "").strip()
                        if _norm(value) not in {"", "/", "n/a", "na", "-"}:
                            region = value
                        break
            places = [(row.ws, row.row_index, columns[c].header_col) for c in (*names[:1], "特性值", "特性类别") if c in columns]
            coords = [ws.cell(r, c).coordinate for ws, r, c in places]
            cells.append(places)
            items.append(Feature(f"“{title}”!{'/'.join(coords)}", name, spec, mark, region))
        active = {i for i, f in enumerate(items) if _norm(f.mark) not in _ORDINARY}
        # If extraction was incomplete, no absence claim in the reverse direction. A positive independent
        # mismatch or a completely reviewed, unpairable source item is still reportable.
        covered = set()
        by_kind: dict[Any, Any] = {}
        for kind, data in sources:
            this_source = set()
            for j, feature in enumerate(data):
                matches = pair(feature, items)
                if not matches:
                    message = described(feature) + "：清单未找到名称＋完整规格公差对应行"
                    if not feature.name or not feature.spec:
                        message = described(feature) + "：已读标记缺独立名称或规格公差，不能配对，按缺项"
                    (pending if list_gap else failures).append(message)
                    continue
                i = matches[0]
                if len(matches) != 1 or pair(items[i], data) != [j]:
                    pending.append(described(feature) + "：两键仍多解，图纸区域/工序位置未能唯一定位，不以类别选择对应行")
                    continue
                this_source.add(i)
                f = items[i]
                for place in cells[i]:
                    self.cite(*place)
                if len(notes) < 3:
                    notes.append(f"{feature.where}（{feature.position or '未列区域'}）↔清单{f.where}：{f.name} / {f.spec}")
                if mark_class(feature.mark) != mark_class(f.mark):
                    failures.append(f"{described(feature)} 与清单{described(f)}按完整公差＋特性名配对，类别符号不同")
                elif _norm(feature.mark) != _norm(f.mark):
                    notes.append(f"{feature.where}与{f.where}符号写法“{feature.mark}”/“{f.mark}”同级")
            covered.update(this_source)
            by_kind.setdefault(kind, set()).update(this_source)
        for i in sorted(active - covered):
            message = f"清单{described(items[i])}：同号对端未找到两键配对"
            if not sources or pending:
                pending.append(message + "，对端尚未核全，未认定原件缺项")
            else:
                failures.append(message + "，按缺项不通过")
        if same_drawings and same_plans and any(len(values & active) < len(covered & active) for values in by_kind.values()):
            pending.append("同号图纸与PGQP1均已提交，清单有项目仅在一方对上；此组合的整条结论待澄清")
        summary = f"{scope}。按完整公差＋独立特性名双向配对，多解再核区域/工序位置；同号图纸{same_drawings}份、PGQP1 {same_plans}份。"
        if mismatched:
            summary += "比对源错配：" + "；".join(mismatched) + "，不逐格比较。"
        if notes:
            summary += "；".join(notes[:3]) + "。"
        if failures and same_plans and not possible_draw:
            pending[:0] = ["仅在PGQP1比对中见到：" + problem for problem in failures[:2]]
            failures.clear()
        if failures:
            return "NG", summary + "；".join(failures[:5]) + "。" + (f"另有{len(pending)}处未核定，不作为缺陷。" if pending else "")
        if pending:
            raise unable(summary + "；".join(pending[:5]) + "，需人工复核。")
        return "PASS", summary + f"清单{len(active)}项与已读对端双向覆盖，类别符号一致。"


def labelled_parts(path: str) -> set[str]:
    """Part numbers another original writes beside a part-number label: cells of a workbook, text of a PDF."""
    found: set[str] = set()
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        import fitz

        with fitz.open(path) as document:
            text = unicodedata.normalize("NFKC", "".join(page.get_text() for page in document[:2]))
        for match in re.finditer(r"(零件号|零件编号|物料编号|Part\s*No\.?|Part\s*Number|产品件号)", text, re.I):
            found |= parts_in(text[match.end() : match.end() + 40])
        return found
    if suffix not in {".xlsx", ".xlsm", ".xls"}:
        return found
    from audit.checker_lib.excel import load_workbook_auto

    book = load_workbook_auto(path)
    for sheet in book.worksheets:
        for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 40), max_col=min(sheet.max_column, 40)):
            for index, cell in enumerate(row):
                text = _norm(cell.value)
                if not text or not any(label in text for label in _PART_LABELS):
                    continue
                values = parts_in(cell.value)
                if not values:
                    for neighbour in row[index + 1 : index + 9]:
                        if neighbour.value not in (None, ""):
                            values = parts_in(neighbour.value)
                            break
                found |= values
    return found


# ---- the reader the leaf names ----

_PGQP1 = SimpleNamespace(DOCUMENT=_pgqp1_document(_reader.resources), PDF_SPECS=(_reader.resources.STITCHED, _reader.resources.PER_PAGE))


class UaesSpecialCharacteristicsRevision3(_Revision3):
    _m = _reader
    _pgqp1 = _PGQP1
    EXTRA_RESOURCE = _reader.combine(_reader.resources.DOCUMENT, _PGQP1.DOCUMENT)
