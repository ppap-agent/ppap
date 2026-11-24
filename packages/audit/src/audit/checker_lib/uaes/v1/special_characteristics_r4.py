"""UAES U12 decision methods for signed v1.0 revision 4."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.materials import related_by_content
from audit.checker_lib.uaes.special_characteristics import _NAMES, characteristic_rows, combine, compact, filled, resources, unable
from audit.checker_lib.uaes.v1.special_characteristics import (
    _ORDINARY,
    _PGQP1,
    _REVISION_LABEL,
    HEADER_FIELDS,
    UaesSpecialCharacteristicsRevision3,
    _norm,
    carries_pictures,
    self_declared_class,
)
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet, Table
from audit.engine.rule_engine.excel_ops import DefaultExcelOps

_PART = re.compile(r"(?<![A-Za-z0-9_.-])[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?(?![A-Za-z0-9_.-])")


_NUMBER_LABELS = {"零件号", "零件编号", "物料编号", "产品件号", "partno", "partnumber", "code"}


_COMBINED_LABELS = {"产品名称", "零件名称", "name", "partname"}


def number_label(value, *, combined=False):
    tokens = {_norm(x).rstrip(":.") for x in re.split(r"[/／\n]+", str(value or "").strip())} - {""}
    return bool(tokens & _NUMBER_LABELS) and tokens <= (_NUMBER_LABELS | _COMBINED_LABELS if combined else _NUMBER_LABELS)


def parts_in(value: Any) -> set[str]:
    """A written composite Name/Code can contain a part and a name; retain whole, case-sensitive ID atoms."""
    raw = str(value or "").strip()
    if not raw:
        return set()
    if re.fullmatch(r"F\s*[0-9][0-9A-Za-z]*(?:[ \t]+[0-9A-Za-z]+)+", raw):
        return {"".join(raw.split())}
    return {token for token in _PART.findall(raw) if any(c.isdigit() for c in token)}


def labelled_number(value) -> set[str]:
    raw = str(value or "").strip()
    if raw.upper() in {"", "-", "/", "N/A", "NONE", "TBD"}:
        return set()
    # In a dedicated part-number field the whole atom is the identity, including case, suffix and punctuation.
    if re.fullmatch(r"[A-Za-z0-9]+(?:[-./_][A-Za-z0-9]+)*", raw):
        return {raw}
    if re.fullmatch(r"F\s*[0-9][0-9A-Za-z]*(?:[ \t]+[0-9A-Za-z]+)+", raw):
        return {"".join(raw.split())}
    return set()


def row_numbers(row, columns):
    field = columns["零件"]
    label = row.ws.cell(row.section.header_row, field.header_col).value
    return (labelled_number if number_label(label) else parts_in)(row.value("零件"))


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
        # The resolver can see a cover field above the table. Only a label in the actual column header
        # makes this a per-row part number (a serial column beneath a Part No. cover field is not one).
        if "零件" in columns and not number_label(sheet.cell(row.section.header_row, columns["零件"].header_col).value, combined=True):
            columns = {k: v for k, v in columns.items() if k != "零件"}
        kept.append((title, row, columns))
    return kept


def labelled_parts(path: str):
    """Yield a written value together with its original location; never span PDF pages/blocks to find a value."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        import fitz

        with fitz.open(path) as document:
            for page in document:
                for *bounds, block, _number, kind in page.get_text("blocks"):
                    if kind != 0:
                        continue
                    lines = block.splitlines()
                    for n, line in enumerate(lines):
                        label, _, inline = line.replace("：", ":").partition(":")
                        if number_label(label) and _norm(label) != "code":
                            value = inline.strip() or (lines[n + 1].strip() if n + 1 < len(lines) else "")
                            for number in labelled_number(value):
                                yield number, {"kind": "pdf_region", "page": page.number + 1, "bbox": bounds}
        return
    if suffix not in {".xlsx", ".xlsm", ".xls"}:
        return
    from audit.checker_lib.excel import load_workbook_auto

    book, ops = load_workbook_auto(path), DefaultExcelOps()
    for sheet in book.worksheets:
        positions = ops.iter_filled_cell_positions(sheet, row_min=1, row_max=sheet.max_row, col_min=1, col_max=sheet.max_column)
        for r, c in positions:
            cell = sheet.cell(r, c)
            text = str(cell.value or "").strip()
            # Bilingual labels often print a colon after each language.
            label, _, inline = re.sub(r"[:：]\s*\n", "\n", text).replace("：", ":").partition(":")
            if not number_label(label) or _norm(label) == "code":
                continue
            source = cell
            if not inline.strip():
                merge = ops.get_detailed_merge_info(sheet, r, c)
                edge = merge.max_col if merge else c
                if edge >= sheet.max_column:
                    continue
                source = sheet.cell(r, edge + 1)
            for number in labelled_number(inline.strip() or source.value):
                yield number, {"kind": "xlsx_cell", "sheet": sheet.title, "cell": source.coordinate}


def _r4_resource():
    sheets, sections, columns = resources.DOCUMENT
    aliases = {
        "零件": ("零件号", "Part No.", "Part No", "Code"),
        "特性名称": ("特性名称",),
        "特性值": ("规格/公差",),
        "特性类别": ("分类",),
    }
    columns = tuple(col.model_copy(update={"aliases": col.aliases + aliases[col.canonical_name]}) if col.canonical_name in aliases else col for col in columns)
    return combine((sheets, sections, columns), _PGQP1.DOCUMENT)


class UaesSpecialCharacteristicsRevision4(UaesSpecialCharacteristicsRevision3):
    """Reader for signed UAES v1.0 revision 4."""

    EXTRA_RESOURCE = _r4_resource()

    def header_parts(self, ctx):
        found = set()
        for sheet in Table(ctx).find_sheets("特殊特性清单", on_missing=Decision.IGNORE):
            end = self.section(sheet, "特殊特性区").resolved.header_row
            if end < 2:
                continue
            for cell in sheet.scan_non_empty(row_min=1, row_max=end - 1, col_min=1, col_max=sheet.ws.max_column):
                raw = str(cell.value() or "").strip()
                label, _, inline = re.sub(r"[:：]\s*\n", "\n", raw).replace("：", ":").partition(":")
                if not number_label(label):
                    continue
                adjacent = None if inline.strip() else cell.find_right(max_offset=8)
                found |= labelled_number(inline.strip() or (adjacent.value() if adjacent else ""))
        return found

    def own_part(self, ctx: RuleContext) -> tuple[str | None, str]:
        """(this part number, how it was found): the list's labelled header, its only per-row part, else the one
        part number the other submitted (not reference) originals of this round state."""
        header = self.header_parts(ctx)
        if len(header) == 1:
            return next(iter(header)), "清单表头零件号"
        if len(header) > 1:
            return None, f"清单表头写有多个零件号{'、'.join(sorted(header))}，不能确定本件号"
        rows = list_rows(self, ctx)
        per_row = set().union(*(row_numbers(row, columns) for _title, row, columns in rows if "零件" in columns)) if rows else set()
        if len(per_row) == 1 and all(row_numbers(row, columns) for _title, row, columns in rows if "零件" in columns):
            return next(iter(per_row)), "清单各行零件号"
        stated = self.batch_parts()
        if len(stated) == 1:
            return next(iter(stated)), "同批次其他提交文件写明的零件号"
        # A row's existence cannot break a tie between contradictory submitted originals.
        found = "、".join(sorted(header | per_row)) or "无"
        others = "、".join(sorted(stated)) or "无"
        return None, f"清单为多件号混装（读到{found}），同批次其他提交文件写明的零件号为{others}，不能确定本件号"

    def batch_parts(self) -> set[str]:
        if self.group_context is None:
            return set()
        if self.group_context.unavailable_files:
            raise unable("同批存在未能读取的提交资料，不能据其余文件确定混装清单的本件号。")
        found: set[str] = set()
        for item in self.group_context.by_audit_id.values():
            if item.audit_id == self.audit_file.audit_id or (item.file_metadata or {}).get("role") == "reference":
                continue
            try:
                for number, place in labelled_parts(str(item.file_url)):
                    version = str((item.file_metadata or {}).get("file_version_id") or item.audit_id)
                    self.legacy().witness(place, number, version)
                    found.add(number)
            except Exception as exc:
                raise unable("同批提交文件的件号证据读取失败，不能据其余文件选择本件号。") from exc
        return found

    def scoped(self, ctx: RuleContext) -> tuple[str, list[tuple[str, Any, dict]], str]:
        part, how = self.own_part(ctx)
        if part is None:
            if reason := self.unreadable():
                raise unable(f"{reason}，尚不能确定本件号，需人工复核。")
            raise unable(f"{how}，尚不能按本件号过滤行范围，需人工复核。")
        rows = list_rows(self, ctx)
        if any(len(row_numbers(row, cols)) != 1 for _title, row, cols in rows if "零件" in cols):
            raise unable(f"按本件号{part}过滤时，有行的件号为空或不能唯一读取，范围尚不完整，交人工复核。")
        kept = [(title, row, columns) for title, row, columns in rows if "零件" not in columns or row_numbers(row, columns) == {part}]
        return part, kept, f"按本件号{part}过滤（本件号取自{how}），参与判定{len(kept)}行"

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
                name = text
                for identifier in parts_in(text):
                    name = name.replace(identifier, "")
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
        # Signatures: an Excel original without any picture can carry neither a handwritten signature nor a stamp.
        path = str(self.audit_file.file_url)
        if not path.lower().endswith(".pdf"):
            try:
                pictures = carries_pictures(path)
            except Exception:
                pictures = True
            if not pictures:
                material_basis = "各行物料字段待本件号确定后核对" if open_scope else "物料字段与表头字段齐全"
                return "NG", f"{scope}。{material_basis}；原件没有任何图片，编制、审核、批准栏没有手写签字或电子签名图层，也没有印章，签署不闭环。"
        if open_scope:
            raise unable(f"表头字段齐全；{open_scope}")
        fields = {
            "prepared": "供应商编制栏有手写签字（或电子手写签名图层），不是打印体姓名",
            "checked": "供应商审核栏有手写签字（或电子手写签名图层），不是打印体姓名",
            "approved": "供应商批准栏有手写签字（或电子手写签名图层），不是打印体姓名",
            "stamp": "签署区至少有一处印章（受控章或公章，含电子签章图层）",
        }
        status, basis = self.visual(fields, "编制、审核、批准签字与印章")
        return status, f"{scope}；物料字段与表头字段齐全；签署：{basis}"

    @rule("四类列与行内必填")
    def rule_2(self, ctx: RuleContext):
        sheets = Table(ctx).find_sheets("特殊特性清单", on_missing=Decision.IGNORE)
        if not sheets:
            if reason := self.unreadable():
                raise unable(f"{reason}，尚不能确认特殊特性清单的四类列，需人工复核。")
            return "NG", "原件中未见编号/序号、特性名称、特性及公差、特性类型四类列表头，缺列不通过。"
        part, rows, scope = self.scoped(ctx)
        for sheet in sheets:
            if rows and sheet.title not in {title for title, _row, _columns in rows}:
                continue
            columns = self.section(sheet, "特殊特性区").columns(optional=("特性编号", *_NAMES, "特性值", "特性类别"))
            missing = [
                label
                for name, label in (("特性编号", "编号/序号"), ("特性名称", "特性名称"), ("特性值", "特性及公差"), ("特性类别", "特性类型/类别"))
                if name not in columns and not (name == "特性名称" and "过程特性名称" in columns)
            ]
            if missing:
                return "NG", f"按本件号{part}过滤。特殊特性清单“{sheet.title}”缺少{'、'.join(missing)}列；按列结构判定，参与逐行必填判定0行（不表示表体无记录）。"
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

    @rule("特殊特性与图纸、PGQP1三方一致")
    def rule_3(self, ctx: RuleContext):
        # A complete batch containing neither required source has no comparison to perform,
        # independent of which rows of a mixed list would have been in scope.
        group = self.group_context
        if not self.batch_files("E12") and not self.drawings():
            if not group or group.unavailable_files or any(not kind or _norm(kind) in {"unknown", "未知"} for kind in group.by_doc_type):
                raise unable("本轮资料范围或文件类型未核全，不能确认未提交图纸与PGQP1。")
            if related_by_content(self, ("图纸", "drawing", "PGQP1", "采购件质量计划")).items:
                raise unable("有未归类或待核实的比对来源，不能确认未提交图纸与PGQP1。")
            ctx.record_execution()
            return "NA", "同批次未提交图纸与PGQP1，无跨表比对对象，本条不适用；未按全表统计，缺件只在包级计一次。"
        return super().rule_3(ctx)
