"""UAES U12 candidate: special characteristic lists against UAES v1.0 revision 3 (SCL:1–SCL:3).

The reader is the UAES special characteristic list implementation that resources.py declares; this leaf adds the
revision 3 clause methods on top of it at run time (a leaf may not import a company implementation, so the class and
its module's names are taken from the carrier the shared loader built). Every clause first filters the list to this part number ("本件号") and decides on those rows only.
Signatures and stamps exist only as an image of the original: they are read as yes/no facts by the image model, and
code decides.
"""

from __future__ import annotations

import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

from audit.checker_lib.agents import AgentAudit
from audit.checker_lib.excel_agent import rule
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet, Table
from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel
from resources import RESOURCE

# Names of the UAES list reader's module this leaf uses; bound from the carrier's module when the reader is built.
_READER_NAMES = ("compact", "unable", "combine", "_NAMES", "characteristic_rows", "filled")

_PART = re.compile(r"(?<![0-9A-Z])F[0-9A-Z]{9}(?![0-9A-Z])")
_PART_LABELS = ("零件号", "零件编号", "物料编号", "partnumber", "partno", "产品件号", "件号", "drawingno", "图号")
_ORDINARY = {"", "/"}
_SAFETY = {"cc", "sc/1", "1", "s", "★", "安全", "关键"}
_MAJOR = {"sc", "sc/2", "2", "d", "f", "重要", "主要", "跑道框"}
_KNOWN = _SAFETY | _MAJOR | {"yc", "ys", "3", "5"}
_TOKEN = re.compile(r"sc/[12]|安全|关键|重要|主要|跑道框|[a-z]+|\d+|[^\s,，、;；/]")
_SERIAL = re.compile(r"^\s*\d+(?:\s*-\s*\d+)?\s*[#＃]\s*(?:-\s*\d+)?")
_CLASS_SUFFIX = re.compile(r"(?:[\s,，、]*(?:[1235]|cc|sc(?:/[12])?|yc|ys|★))+\s*(?=[（(]|$)", re.I)
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
_GEOMETRY_WORDS = tuple(dict.fromkeys(_GEOMETRY.values()))


def tolerance_key(value: Any) -> str:
    """A tolerance value normalised for exact comparison (SCL:3 pairs by "公差值归一后精确相等"): width and case
    folded, spaces removed, diameter signs unified, a geometric symbol written as its name, a count prefix ("4X")
    and a geometric tolerance's datum letters dropped."""
    text = unicodedata.normalize("NFKC", "" if value is None else str(value)).split("\n")[0]
    for symbol, word in _GEOMETRY.items():
        text = text.replace(symbol, word)
    text = "".join(text.split()).casefold()
    text = re.sub(r"[φø⌀∅]", "φ", text)
    text = re.sub(r"^\d+[x×*]", "", text)
    text = re.sub(r"\+(\d+(?:\.\d+)?)/-\1(?!\d)", r"±\1", text)
    if text.startswith(_GEOMETRY_WORDS):
        text = re.sub(r"(?<=\d)[a-z](?:[-/]?[a-z])*$", "", text)
    return text


_DRAWING_PROMPT = (
    "这是一张零件图纸的一块局部图像（整张图纸切成多块，块与块略有重叠）。请按 JSON Schema 转写，只转写，不判断。\n"
    "items：本块中带有特性类别标记的每一个尺寸、公差或形位公差。类别标记指紧跟在尺寸或形位公差框后面的小框或旗形里的"
    "数字或字母（如 5、3/5、3、F、S、SC、CC、★）。没有类别标记的尺寸不要列出。\n"
    "dimension：照原样转写尺寸与公差，含 Φ、R、数量前缀（如 4X）、上下偏差（写成 +0.2/-0.1 的形式）或 ±；形位公差框写成"
    "“中文名称 公差值 基准”，如“位置度 Φ0.1 A-B”“平行度 0.1 A-B”，名称只用：平行度、位置度、垂直度、同轴度、平面度、圆度、"
    "圆柱度、圆跳动、全跳动、对称度、倾斜度、线轮廓度、面轮廓度；粗糙度写成“Rz max 3.2”这样。\n"
    "mark：类别标记框里的内容，照原样，如“5”“3/5”“F”。view：该尺寸所在视图的标签（如 A-A、B-B），看不出写空字符串。\n"
    "图例、技术要求、标题栏里解释标记含义的示例不是尺寸，不要列出。readable：本块中是否有因模糊、过小而看不清的带标记尺寸，"
    "全部看得清写 true，有看不清的写 false。"
)
_DRAWING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "readable"],
    "properties": {
        "readable": {"type": "boolean"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["dimension", "mark", "view"],
                "properties": {"dimension": {"type": "string"}, "mark": {"type": "string"}, "view": {"type": "string"}},
            },
        },
    },
}


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


def large_format(path: str) -> bool:
    """Whether a PDF has a page of A3 size or larger (its longer side at least 1150 pt), as part drawings are."""
    import fitz

    try:
        with fitz.open(path) as document:
            return any(max(page.rect.width, page.rect.height) >= 1150 for page in document)
    except Exception:
        return False


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
                row_anchors=("特性项目",),
                row_stop_on_first_blank=False,
                row_max_blank_streak=rows,
                row_stop_markers=("5. Product Audit", "产品审核"),
            ),
        ),
        (
            ColumnModel(canonical_name="特性项目", section_name="重要特性区", aliases=("Items/项目",)),
            ColumnModel(canonical_name="特殊特性", section_name="重要特性区", aliases=("SC/特殊特性",)),
            ColumnModel(canonical_name="测量设备", section_name="重要特性区", aliases=("Measuring Equipment\n测量设备",)),
        ),
    )


class _Revision3:
    """Revision 3 clause methods over the UAES special characteristic list reader; `self._m` is its module."""

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

    def drawings(self) -> list[tuple[Any, bool]]:
        """(item, typed) of the same-round originals that may be drawings: typed as a drawing, or large-format (A3 or
        larger) PDF originals of no recognised type; the UAES register has no drawing type, so a drawing arrives untyped,
        and an A4 parts list or cover is not taken as one."""
        if self.group_context is None:
            return []
        own = self.audit_file.audit_id
        typed: set[str] = set()
        found: dict[str, tuple[Any, bool]] = {}
        for kind, items in self.group_context.by_doc_type.items():
            unknown = not kind or _norm(kind) in {"unknown", "未知"}
            if not unknown:
                typed |= {item.audit_id for item in items}
            if not unknown and re.search(r"图纸|图样|drawing", str(kind), re.I):
                found |= {item.audit_id: (item, True) for item in items if item.audit_id != own}
        for item in self.group_context.by_audit_id.values():
            if item.audit_id != own and item.audit_id not in typed and str(item.file_url).lower().endswith(".pdf") and large_format(str(item.file_url)):
                found.setdefault(item.audit_id, (item, False))
        return list(found.values())

    def drawing_number(self, item: Any) -> set[str]:
        import fitz

        with fitz.open(str(item.file_url)) as document:
            text = "".join(page.get_text() for page in document)
        found = parts_in(text)
        if found:
            return found
        title = self.drawing_title(item)
        return parts_in(title.get("drawing_number") or "")

    def drawing_marks(self, item: Any) -> tuple[list[tuple[str, str, str, bool]], bool]:
        """((dimension, mark, view, confirmed) of every special-characteristic mark on a PDF drawing, whether every tile
        was read and legible). The drawing's dimensions and marks are vector strokes outside the text layer, so each
        page is cut into overlapping tiles and each tile transcribed twice by the image model; a mark is confirmed when
        both transcriptions (or two overlapping tiles) read the same tolerance value and class. Code pairs and decides."""
        import contextvars
        from concurrent.futures import ThreadPoolExecutor

        import fitz

        version = str((getattr(item, "file_metadata", None) or {}).get("file_version_id") or item.audit_id)
        cache = self.__dict__.setdefault("_drawing_marks", {})
        if version in cache:
            return cache[version]
        tiles: list[bytes] = []
        with fitz.open(str(item.file_url)) as document:
            for page in document:
                columns = max(1, round(page.rect.width / 850))
                rows = max(1, round(page.rect.height / 800))
                width, height = page.rect.width / columns, page.rect.height / rows
                pad = 0.06 * max(width, height)
                for row in range(rows):
                    for column in range(columns):
                        area = fitz.Rect(column * width - pad, row * height - pad, (column + 1) * width + pad, (row + 1) * height + pad) & page.rect
                        dpi = min(220, int(2400 * 72 / max(area.width, area.height)))
                        tiles.append(page.get_pixmap(dpi=dpi, clip=area).tobytes("png"))
        subject = f"图纸{original_name(item)}的特殊特性标记"

        def read(tile: bytes) -> dict[str, Any] | None:
            try:
                return self.transcribe(tile, _DRAWING_PROMPT, _DRAWING_SCHEMA, subject)
            except GuardStop as stop:
                if stop.decision == Decision.ERR:
                    return None  # One failed reading of a tile; the other reading of it may stand.
                raise

        jobs = [tile for tile in tiles for _pass in range(2)]
        with ThreadPoolExecutor(max_workers=min(12, len(jobs))) as pool:
            results = list(pool.map(lambda tile: contextvars.copy_context().run(read, tile), jobs))
        if all(result is None for result in results):
            raise GuardStop(Decision.ERR, "原件模型请求失败或结果尚未确认，尚未取得业务结论。")
        seen: dict[tuple[str, frozenset[str]], tuple[str, str, str]] = {}
        counts: dict[tuple[str, frozenset[str]], int] = {}
        legible = True
        for result in results:
            if result is None:
                legible = False
                continue
            legible = legible and result.get("readable") is True
            keys = set()
            for entry in result.get("items") or []:
                dimension, mark = str(entry.get("dimension") or "").strip(), str(entry.get("mark") or "").strip()
                tokens = mark_tokens(mark)
                # Only the class codes the clause names count as a mark; anything else is a misread label.
                if not tolerance_key(dimension) or not tokens or not all(token in _KNOWN for token in tokens):
                    continue
                key = (tolerance_key(dimension), mark_class(mark))
                seen.setdefault(key, (dimension, mark, str(entry.get("view") or "").strip()))
                keys.add(key)
            for key in keys:
                counts[key] = counts.get(key, 0) + 1
        cache[version] = ([(*value, counts[key] >= 2) for key, value in seen.items()], legible)
        return cache[version]

    @rule("特殊特性与图纸、PGQP1三方一致")
    def rule_3(self, ctx: RuleContext):
        part, rows, scope = self.scoped(ctx)
        plans, drawings = self.batch_files("E12"), self.drawings()
        if not plans and not drawings:
            return "NA", f"{scope}。同批次未提交图纸与PGQP1，本条不适用（依据：条款“图纸亦未提交时，本条记不适用”）；不记作通过，缺件只在包级计一次。"
        # Same-number check first: a source of another part number is a mismatched comparison source. It is left out of
        # the comparison (its content is not checked cell by cell); the sources of this number are still compared.
        mismatched, pending = [], []
        plan_rows: list[tuple[str, str, str, str]] = []
        unmarked_plan = False
        same_plans = 0
        for plan in plans:
            with self.reading(ctx, plan, self._pgqp1):
                for sheet in Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE):
                    numbers = set().union(*(parts_in(value) for value in self.labelled(sheet, ("Part No/零件号",)))) if self.labelled(sheet, ("Part No/零件号",)) else set()
                    if part not in numbers:
                        mismatched.append(f"PGQP1“{original_name(plan)}”零件号{'、'.join(sorted(numbers)) or '未写明'}")
                        continue
                    same_plans += 1
                    for row in self.section(sheet, "重要特性区").rows("特性项目"):
                        name = str(row.value("特性项目") or "").strip()
                        written = str(row.value("特殊特性") or "").strip()
                        if not name:
                            continue
                        if written and all(token in _KNOWN for token in mark_tokens(written)):
                            plan_rows.append((f"PGQP1“{sheet.title}”第{row.row_index}行", name, "", written))
                        else:
                            unmarked_plan = True
        same_drawings = []
        unread = False  # A submitted drawing whose number or marks the program could not read.
        for drawing, typed in drawings:
            try:
                numbers = self.drawing_number(drawing)
            except GuardStop as stop:
                if typed:
                    pending.append(f"图纸“{original_name(drawing)}”图号未能读取（{stop.message}）")
                    unread = True
                continue  # An untyped original without a readable drawing number is not taken as a drawing.
            if not numbers and not typed:
                continue
            if part not in numbers:
                mismatched.append(f"图纸“{original_name(drawing)}”图号{'、'.join(sorted(numbers)) or '未能读出'}")
            else:
                same_drawings.append(drawing)
        if mismatched and not same_plans and not same_drawings:
            raise unable(
                f"{scope}。比对源错配：{'；'.join(mismatched)}，与本件号{part}不同；按条款先做同号校验，不同号不逐项比对特性内容，同批次没有本件号的图纸或PGQP1可比，请提交本件号的图纸/PGQP1后复核。"
            )
        notes = []
        if mismatched:
            notes.append(f"比对源错配：{'；'.join(mismatched)}，与本件号{part}不同，不逐项比对其内容，只用本件号的{'图纸' if same_drawings else 'PGQP1'}比对")
        if not plans:
            notes.append("同批次未提交PGQP1，以图纸为唯一基准")
        scoped_rows = []
        for title, row, columns in rows:
            mark = str(row.value("特性类别") or "").strip() if "特性类别" in columns else ""
            name = next((str(row.value(column)).strip() for column in _NAMES if filled(row, column, columns)), "")
            value = str(row.value("特性值")).strip() if filled(row, "特性值", columns) else ""
            scoped_rows.append((f"“{title}”第{row.row_index}行", name, value, mark))
        listed = [entry for entry in scoped_rows if _norm(entry[3]) not in _ORDINARY]
        failures = []
        # PGQP1 → list, paired by name (the PGQP1 form writes no separate tolerance column).
        for where, name, _value, mark in plan_rows:
            paired = [entry for entry in listed if characteristic_name(entry[1]) == characteristic_name(name)]
            if not paired:
                failures.append(f"{where}特殊特性“{name}”（{mark}）在清单中没有对应行")
            elif not any(mark_class(entry[3]) == mark_class(mark) for entry in paired):
                failures.append(f"{where}“{name}”标识“{mark}”，清单{paired[0][0]}为“{paired[0][3]}”，类别符号不同")
            elif any(_norm(entry[3]) != _norm(mark) for entry in paired):
                notes.append(f"{name}写法“{mark}”/“{paired[0][3]}”按等价写法判一致")
        # Drawing → list, paired by the tolerance value (the drawing writes no characteristic names).
        marks: list[tuple[str, str, str, bool]] = []
        legible = True
        for drawing in same_drawings:
            if not str(drawing.file_url).lower().endswith(".pdf"):
                pending.append(f"图纸“{original_name(drawing)}”不是PDF，其中的特殊特性尚未由检查程序读取")
                unread = True
                continue
            found, readable = self.drawing_marks(drawing)
            marks.extend(found)
            legible = legible and readable
        unsure = []
        for dimension, mark, view, confirmed in marks:
            where = f"图纸{('“' + view + '”视图') if view else ''}“{dimension}”（标{mark}）"
            paired = [entry for entry in scoped_rows if tolerance_key(entry[2]) == tolerance_key(dimension)]
            problem = ""
            if not paired:
                problem = f"{where}在清单中没有同公差值的行"
            elif not any(mark_class(entry[3]) == mark_class(mark) for entry in paired):
                problem = f"{where}，清单{paired[0][0]}同公差值的行类别为“{paired[0][3] or '空'}”，类别符号不同"
            elif any(_norm(entry[3]) != _norm(mark) for entry in paired):
                notes.append(f"“{dimension}”写法“{mark}”/“{paired[0][3]}”分隔符不同，判一致（留痕）")
            if problem and confirmed:
                failures.append(problem)
            elif problem:
                unsure.append(problem)
        if unsure:
            pending.append(f"图纸另有{len(unsure)}处（如{unsure[0]}）两次读图结果不一致，未据此判定")
        # List → drawing or PGQP1.
        orphans = []
        drawn = {tolerance_key(dimension) for dimension, _mark, _view, _confirmed in marks}
        planned = {characteristic_name(name) for _where, name, _value, _mark in plan_rows}
        for where, name, value, mark in listed:
            if tolerance_key(value) in drawn or characteristic_name(name) in planned:
                continue
            orphans.append(f"{where}“{name} {value}”（{mark}）")
        if orphans:
            if mismatched or unmarked_plan or not legible or unread:
                why = "错号的比对源不参与比对" if mismatched else "PGQP1未逐行标特殊特性符号" if unmarked_plan else "图纸有看不清的部分" if not legible else "有图纸未能读取"
                pending.append(f"清单{'、'.join(orphans[:4])}{'等' + str(len(orphans)) + '项' if len(orphans) > 4 else ''}在本件号图纸/PGQP1中未找到，因{why}，“清单→图纸/PGQP1”方向尚不能判定")
            else:
                failures.append(f"清单{'、'.join(orphans[:4])}{'等' + str(len(orphans)) + '项' if len(orphans) > 4 else ''}在图纸与PGQP1中都找不到对应特殊特性")
        if failures:
            return "NG", f"{scope}。" + "；".join(failures[:5]) + (f"等{len(failures)}处" if len(failures) > 5 else "") + "。" + ("（" + "；".join(notes[:3]) + "）" if notes else "")
        if unmarked_plan:
            pending.append("PGQP1重要特性表的“SC/特殊特性”栏写的是规格而不是特殊特性符号，检查程序不能确定其中哪些是特殊特性")
        if same_drawings and not legible:
            pending.append("图纸有看不清的带标记尺寸，“图纸→清单”覆盖尚不能确认完整")
        if pending:
            raise unable(f"{scope}。" + "；".join(pending) + ("；" + "；".join(notes) if notes else "") + "，需人工复核。")
        basis = []
        if plan_rows:
            basis.append(f"PGQP1{len(plan_rows)}项特殊特性（按名称配对）")
        if same_drawings:
            basis.append(f"图纸{len(marks)}项带类别标记的尺寸（按公差值配对，图纸经图像转写、每块读两次）")
        return "PASS", f"{scope}。清单{len(listed)}项特殊特性与{'、'.join(basis) or '比对源'}双向覆盖，类别符号一致" + (f"（{'；'.join(notes[:3])}）" if notes else "") + "。"


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


_BUILT: dict[type, type] = {}


def _extend(base: type) -> type:
    if base not in _BUILT:
        module = sys.modules[base.__module__]
        globals().update({name: getattr(module, name) for name in _READER_NAMES})
        from types import SimpleNamespace

        plan = SimpleNamespace(DOCUMENT=_pgqp1_document(module.resources), PDF_SPECS=(module.resources.STITCHED, module.resources.PER_PAGE))
        resource = module.combine(module.resources.DOCUMENT, plan.DOCUMENT)
        _BUILT[base] = type("UaesSpecialCharacteristicsRevision3", (_Revision3, base), {"_m": module, "_pgqp1": plan, "EXTRA_RESOURCE": resource, "__module__": __name__})
    return _BUILT[base]


class Revision3Audit(AgentAudit):
    def _implementation(self):
        if self._failure is not None:
            raise self._failure
        agent = super()._implementation()
        if agent is None or isinstance(agent, _Revision3):
            return agent
        try:
            self._agent = _extend(type(agent))(self.audit_file, self._implementation_rule(), group_context=self.group_context)
        except BaseException as exc:
            self._failure = exc
            raise
        return self._agent


def create_audit(audit_file, rule, group_context=None):
    return Revision3Audit(audit_file, rule, group_context, resource=RESOURCE)


# ruff: noqa: E402, F821  # a candidate leaf binds the declared reader's names at run time; it may not import it
