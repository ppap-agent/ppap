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
    return _TOKEN.findall(_norm(value).replace("／", "/"))


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

    def drawings(self) -> list[Any]:
        if self.group_context is None:
            return []
        return [item for kind, items in self.group_context.by_doc_type.items() if re.search(r"图纸|图样|drawing", kind, re.I) for item in items if item.audit_id != self.audit_file.audit_id]

    def drawing_number(self, item: Any) -> set[str]:
        import fitz

        with fitz.open(str(item.file_url)) as document:
            text = "".join(page.get_text() for page in document)
        found = parts_in(text)
        if found:
            return found
        title = self.drawing_title(item)
        return parts_in(title.get("drawing_number") or "")

    @rule("特殊特性与图纸、PGQP1三方一致")
    def rule_3(self, ctx: RuleContext):
        part, rows, scope = self.scoped(ctx)
        plans, drawings = self.batch_files("E12"), self.drawings()
        if not plans and not drawings:
            return "NA", f"{scope}。同批次未提交图纸与PGQP1，本条不适用（依据：条款“图纸亦未提交时，本条记不适用”）；不记作通过，缺件只在包级计一次。"
        # Same-number check first: a source of another part number is a mismatched comparison source.
        mismatched, pending = [], []
        plan_rows: list[tuple[str, str, str, str]] = []
        unmarked_plan = False
        for plan in plans:
            with self.reading(ctx, plan, self._pgqp1):
                for sheet in Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE):
                    numbers = set().union(*(parts_in(value) for value in self.labelled(sheet, ("Part No/零件号",)))) if self.labelled(sheet, ("Part No/零件号",)) else set()
                    if part not in numbers:
                        mismatched.append(f"PGQP1“{original_name(plan)}”零件号{'、'.join(sorted(numbers)) or '未写明'}")
                        continue
                    for row in self.section(sheet, "重要特性区").rows("特性项目"):
                        name = str(row.value("特性项目") or "").strip()
                        written = str(row.value("特殊特性") or "").strip()
                        if not name:
                            continue
                        if written and all(token in _KNOWN for token in mark_tokens(written)):
                            plan_rows.append((f"PGQP1“{sheet.title}”第{row.row_index}行", name, "", written))
                        else:
                            unmarked_plan = True
        for drawing in drawings:
            try:
                numbers = self.drawing_number(drawing)
            except GuardStop as stop:
                pending.append(f"图纸“{original_name(drawing)}”图号未能读取（{stop.message}）")
                continue
            if part not in numbers:
                mismatched.append(f"图纸“{original_name(drawing)}”图号{'、'.join(sorted(numbers)) or '未能读出'}")
        if mismatched:
            raise unable(f"{scope}。比对源错配：{'；'.join(mismatched)}，与本件号{part}不同；按条款先做同号校验，不同号不逐项比对特性内容，请提交本件号的图纸/PGQP1后复核。")
        listed = []
        for title, row, columns in rows:
            mark = str(row.value("特性类别") or "").strip() if "特性类别" in columns else ""
            if _norm(mark) in _ORDINARY:
                continue
            name = next((str(row.value(column)).strip() for column in _NAMES if filled(row, column, columns)), "")
            value = str(row.value("特性值")).strip() if filled(row, "特性值", columns) else ""
            listed.append((f"“{title}”第{row.row_index}行", name, value, mark))
        failures, notes = [], []
        # PGQP1 → list, and list → PGQP1 (or the drawing), paired by name (the PGQP1 form writes no separate tolerance column).
        for where, name, _value, mark in plan_rows:
            paired = [entry for entry in listed if characteristic_name(entry[1]) == characteristic_name(name)]
            if not paired:
                failures.append(f"{where}特殊特性“{name}”（{mark}）在清单中没有对应行")
            elif not any(mark_class(entry[3]) == mark_class(mark) for entry in paired):
                failures.append(f"{where}“{name}”标识“{mark}”，清单{paired[0][0]}为“{paired[0][3]}”，类别符号不同")
            elif any(_norm(entry[3]) != _norm(mark) for entry in paired):
                notes.append(f"{name}写法“{mark}”/“{paired[0][3]}”按等价写法判一致")
        if failures:
            return "NG", f"{scope}。" + "；".join(failures[:5]) + (f"等{len(failures)}处" if len(failures) > 5 else "") + "。"
        if unmarked_plan:
            pending.append("PGQP1重要特性表的“SC/特殊特性”栏写的是规格而不是特殊特性符号，检查程序不能确定其中哪些是特殊特性")
        if drawings:
            pending.append("图纸中的特殊特性尚未由检查程序读取，“图纸↔清单”双向覆盖与名称、公差、符号一致性未审")
        if not plans:
            notes.append("同批次未提交PGQP1，以图纸为唯一基准")
        if pending:
            raise unable(f"{scope}。" + "；".join(pending) + ("；" + "；".join(notes) if notes else "") + "，需人工复核。")
        return "PASS", f"{scope}。清单{len(listed)}项特殊特性与PGQP1{len(plan_rows)}项特殊特性双向覆盖，名称与类别符号一致" + (f"（{'；'.join(notes[:3])}）" if notes else "") + "。"


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
