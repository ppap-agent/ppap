"""UAES U04 candidate: material and subcontractor list clauses of UAES v1.0 revision 3 (MSL:1-MSL:4).

A leaf carries only checker.py and resources.py and may not import the UAES implementation. resources.py
declares the UAES materials reader as the carrier; at run time `Audit` extends the declared reader class with
the revision 3 clause methods below (`MslClauses`), so the reader's table, signature and drawing readings are
reused. Verdicts are decided in code; the image model only reads signatures, seals and a PDF drawing's title block.
"""

from __future__ import annotations

import hashlib
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from audit.checker_lib.agents import AgentAudit
from audit.checker_lib.excel_agent import rule
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet
from resources import RESOURCE


def compact(value: Any) -> str:
    """Comparable text: width-folded, spaces removed, case-folded, trailing colon dropped (as the UAES reader)."""
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold().rstrip(":：")


def cell_text(cell: Any) -> str:
    """A cell's comparable text, without counting it as read."""
    return compact(cell.ws.cell(cell.row_index, cell.col_index).value)


def unable(message: str) -> GuardStop:
    return GuardStop(Decision.UNABLE, message)


def original_name(item: Any) -> str:
    return str((getattr(item, "file_metadata", None) or {}).get("original_filename") or Path(str(item.file_url)).name)


_DRAWING = ("图纸", "drawing")
_FLOW = ("过程流程图", "PFD")

# MSL:1 names these header fields and record fields; each is found by any of its spellings.
# A spelling is read from a header label; "字段缺失" is a field none of whose spellings is a label.
HEADER_REQUIRED = {
    "总成零件号": ("总成零件号", "零件号", "PartNo"),
    "零件名称": ("零件名称", "PartName"),
    "一级供应商名称及代码": ("一级供应商", "供应商名称", "供应商代码", "SupplierName", "SupplierCode"),
    "填表日期": ("填表日期", "日期", "Date"),
}
RECORD_REQUIRED = {
    "子零件/原材料/外协工序名称": ("零件/部件名称", "部件名称", "子零件名称", "材料名称", "工序名称", "ComponentName"),
    "子件号或材料牌号": ("分部件零件号", "子件号", "ComponentNumber", "材料牌号", "GradeCode"),
    "分供方全称": ("供应商名称", "分供方", "SupplierName"),
    "生产地址或产地": ("生产地点", "生产地址", "产地", "ProductionLocation"),
    "开发状态或质量体系认证状态": ("开发状态", "质量体系认证", "QSPassed"),
    "是否定点": ("是否定点", "定点"),
}

# MSL:2 layer two: the supplier's 编制/审核/批准 and UAES's 项目采购会签, when the template sets them.
SUPPLIER_EXTRA = ("编制", "审核", "批准")
PURCHASING = ("项目采购会签", "项目采购", "采购会签")

# MSL:4: words that mark an outsourced process, the clause's examples of one, and in-house marks.
OUTSOURCED = ("外协", "委外", "外委", "外购", "外包")
OUTSOURCED_KINDS = ("外协加工", "表面处理", "热处理", "焊接外委", "电镀")
IN_HOUSE = ("自制", "厂内", "内制")
RAW_MATERIAL = ("原材料", "原料")
# MSL:4 engineering semantics the clause allows: where a raw material enters the process (incoming
# inspection, melting), and the flow chart column that names the process nodes.
MATERIAL_ENTRY = ("进料检验", "来料检验", "原材料检验", "原料检验", "incominginspect", "熔化", "熔炼", "melt", *RAW_MATERIAL)
FLOW_STEP = ("过程描述", "operationdescription", "工序描述", "工序名称", "processdescription")


def _found(text: str, spellings: tuple[str, ...]) -> bool:
    return any(compact(spelling) in text for spelling in spellings)


def _signer_label(text: str, names: tuple[str, ...]) -> str | None:
    """A sign-off label cell: the name alone, optionally with 人/者 and English, never a sentence that mentions it."""
    bare = re.sub(r"[\W_a-z0-9]+", "", text)
    for name in names:
        if bare in {compact(name), compact(name) + "人", compact(name) + "者"}:
            return name
    return None


# Only layout whitespace is discarded in a written identity; no case, punctuation or suffix correction.
def layout_id(value: str) -> str:
    return "".join(value.split())


DRAWING_TITLE = {"图纸", "零件图", "工程图", "drawing", "partdrawing", "engineeringdrawing"}
PARTS_TITLE = {"零件清单", "partslist", "零件清单partslist"}
TITLE = {
    "number": {"图号", "dwgno.", "dwgno", "drawingno.", "drawingno", "drawingnumber"},
    "name": {"名称", "零件名称", "designation", "partname"},
    "project": {"项目", "项目编号", "uaes项目编号", "project", "projectno.", "projectnumber"},
    "status": {"图纸状态", "drawingstatus"},
}


def title_key(label: Any) -> str | None:
    key = compact(label).replace("/", "")
    return next((field for field, names in TITLE.items() if key in names or any(key in {cn + en, en + cn} for cn in names if re.search(r"[\u3400-\u9fff]", cn) for en in names if en.isascii())), None)


def pdf_field(lines, labels, *, numbers=False):
    """Only an inline labelled value or a single adjacent/below value of that label; never a full-text match."""
    found = {}
    for box, text in lines:
        parts = re.split("[:：]", text, maxsplit=1)
        if compact(parts[0]) not in labels and TITLE.get(title_key(parts[0])) != labels:
            continue
        if len(parts) == 2 and parts[1].strip():
            candidates = [(box, parts[1].strip())]
        else:
            candidates = [
                (b, v)
                for b, v in lines
                if (box.y1 <= b.y0 < box.y1 + 1.9 * box.height and abs((b.x0 + b.x1) / 2 - (box.x0 + box.x1) / 2) <= max(5, box.width / 2)) and compact(v) not in labels and not title_key(v)
            ]
        if numbers and len(parts) < 2:
            candidates = [(b, v) for b, v in candidates if re.fullmatch(r"[\w. /\-]+", v, re.ASCII) and re.search(r"\d", v)]
        if len(candidates) == 1:
            b, v = candidates[0]
            found[tuple(b)] = (b, v)
    return list(found.values())


def local_box(area, raw):
    import fitz

    if not isinstance(raw, list) or len(raw) != 4 or not all(type(v) in {int, float} for v in raw):
        return None
    x, y, r, b = raw
    if not (0 <= x < r <= 1 and 0 <= y < b <= 1) or (r - x) * (b - y) >= 0.9:
        return None
    return fitz.Rect(area.x0 + x * area.width, area.y0 + y * area.height, area.x0 + r * area.width, area.y0 + b * area.height)


_TITLE_FIELD = {
    "type": ["object", "null"],
    "properties": {"label": {"type": "string"}, "value": {"type": "string"}, "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}},
    "required": ["label", "value", "bbox"],
    "additionalProperties": False,
}
TITLE_SCHEMA = {
    "type": "object",
    "properties": {"kind": {"enum": ["drawing", "unknown"], "type": "string"}, **dict.fromkeys(TITLE, _TITLE_FIELD)},
    "additionalProperties": False,
    "required": ["kind", *TITLE],
}
TITLE_PROMPT = (
    "只转写图中独立工程零件图标题栏。能看到工程图标题栏结构、标注的图号字段才填 drawing，否则 unknown。"
    "分别转写本件图号、名称、项目编号、图纸在用状态，给出每个字段实际印刷的标签、原值与该栏在图内的归一坐标[x0,y0,x1,y1]。"
    "保留大小写、空格、后缀、完整双语名称。排除相关图号、首次用于、BOM、订货规范、物料行。未设或读不清一律 null，不推断项目或在用状态。"
)


class MslClauses:
    """Revision 3 clause methods over the declared UAES materials reader (`READER` is its module)."""

    READER: Any = None

    @property
    def lib(self) -> Any:
        return self.READER

    @property
    def res(self) -> Any:
        return self.READER.resources

    # ---- MSL:1 ----
    def header_area(self, sheet: Sheet, header_row: int) -> list:
        return [cell for cell in self.cells_of(sheet, 1, header_row - 1)]

    def cells_of(self, sheet: Sheet, first: int, last: int) -> list:
        _rows, columns = self.extent(sheet)
        if last < first:
            return []
        return sheet.scan_non_empty(row_min=first, row_max=last, col_min=1, col_max=columns)

    @rule("材料产品及分供清单非备注字段不得为空，字段缺失与内容为空分开计")
    def msl_1(self, ctx: RuleContext):
        missing: list[str] = []
        empty: list[Any] = []
        unknown: list[Any] = []
        checked = 0
        for sheet in self.lists(ctx):
            section = self.section(sheet, "材料清单区")
            header, serial = section.resolved.header_row, section.column("材料序号")
            # Header fields: labels above the table, each with its value right of it.
            labels = {cell: cell_text(cell) for cell in self.header_area(sheet, header)}
            for name, spellings in HEADER_REQUIRED.items():
                if not any(_found(text, spellings) for text in labels.values()):
                    missing.append(f"表头“{name}”")
            for name, aliases in self.res.HEADER_FIELDS.items():
                cells = self.beside(ctx, sheet, aliases)
                if len(cells) != 1:
                    unknown.append(f"未能定位表头“{name}”的填写位置")
                elif not cells[0].has_data():
                    empty.append(self.lib.Place.of(f"表头{name}", sheet, cells[0].row_index, cells[0].col_index))
            # Record columns, named by the header rows above the data.
            _rows, width = self.extent(sheet)
            leaves: dict[int, tuple[str, str | None]] = {}
            heads: dict[int, str] = {}
            for column in range(1, width + 1):
                texts = list(dict.fromkeys(str(sheet.cell(row, column).value() or "").strip() for row in range(header, serial.data_start_row)))
                texts = [text for text in texts if text]
                if not texts:
                    continue
                heads[column] = compact("".join(texts))
                if any("备注" in text for text in texts):
                    continue
                own = any(compact(field) in compact("".join(texts)) for field in self.res.OWN_FIELDS)
                group = None if own else next((name for name in self.res.CHOICE_GROUPS if name in compact(texts[0])), None)
                leaves[column] = (self.leaf_label(texts), group)
            for name, spellings in RECORD_REQUIRED.items():
                if not any(_found(text, spellings) for text in heads.values()):
                    missing.append(f"表体“{name}”列")
            pictures = self.pictures(sheet)
            for row in section.rows("材料序号"):
                index = row.row_index
                filled = {column for column in leaves if sheet.cell(index, column).has_data()}
                for column, (label, _group) in leaves.items():
                    if self.res.PICTURE in label and column not in filled and pictures and self.lib._covers(pictures, (index, index), (column, column)):
                        filled.add(column)
                if not filled - {serial.header_col}:
                    continue  # A numbered template row nobody filled in is not a record.
                checked += 1
                empty_groups = set()
                for column, (label, group) in leaves.items():
                    if column in filled:
                        continue
                    if group is not None:
                        if not any(other in filled for other, (_label, member) in leaves.items() if member == group):
                            empty_groups.add(group)
                    elif self.res.PICTURE in label and pictures is None:
                        unknown.append(self.lib.Place.of(f"第{index}行{label}的图片是否存在未能从原件读取", sheet, index, column))
                    else:
                        empty.append(self.lib.Place.of(f"第{index}行{label}", sheet, index, column))
                first = {group: min(column for column, (_label, member) in leaves.items() if member == group) for group in empty_groups}
                empty.extend(self.lib.Place.of(f"第{index}行{group}（未选择）", sheet, index, first[group]) for group in sorted(empty_groups))
        parts = []
        if missing:
            parts.append(f"字段缺失{len(missing)}项（模板中没有这一字段）：" + "、".join(missing))
        if empty:
            parts.append(f"内容为空{len(empty)}处（有该字段但未填写）：" + "；".join(map(self.named, empty[:12])) + ("等" if len(empty) > 12 else ""))
        if parts:
            return "NG", "材料产品及分供清单" + "。".join(parts) + "。"
        if unknown:
            raise unable("检查程序未能确认：" + "；".join(map(self.named, unknown[:8])) + "，需人工复核。")
        if not checked:
            raise unable("材料产品及分供清单没有已填写的记录行，不能按空清单通过。")
        return "PASS", f"表头字段齐全并已填写；已逐行核实{checked}条记录，除备注外字段齐全且均已填写。"

    # ---- MSL:2 ----
    def sign_line(self, ctx: RuleContext, sheet: Sheet, label: Any, pictures: Any) -> tuple[bool, tuple[int, int]]:
        """Whether anything is written or drawn right of a sign-off label, and the rows of its line."""
        _height, width = self.extent(sheet)
        merged = ctx.ops.get_detailed_merge_info(sheet.ws, label.row_index, label.col_index)
        rows = (merged.min_row, merged.max_row) if merged else (label.row_index, label.row_index)
        start = (merged.max_col if merged else label.col_index) + 1
        written = any(sheet.cell(row, column).has_data() for row in range(rows[0], rows[1] + 1) for column in range(start, width + 1))
        if written or pictures is None:
            return True, rows
        return self.lib._covers(pictures, rows, (start, width)), rows

    @rule("必签三方签字与按模板设置的附加签署栏")
    def msl_2(self, ctx: RuleContext):
        sheets = self.lists(ctx)
        blank: list[str] = []
        fields: dict[str, str] = {}
        extra_blank: list[str] = []
        extra_names: dict[str, str] = {}
        supplier_extra: list[str] = []
        for sheet in sheets:
            pictures = self.pictures(sheet)
            height, width = self.extent(sheet)
            cells = sheet.scan_non_empty(row_min=1, row_max=height, col_min=1, col_max=width)
            for index, role in enumerate(self.res.SIGNATURES):
                found = [cell for cell in cells if compact(cell.value()).startswith(compact(role))]
                if len(found) != 1:
                    raise unable(f"“{sheet.title}”中{'未能定位' if not found else '有多处'}“{role}”签字栏，检查程序未能读取，需人工复核。")
                drawn, _rows = self.sign_line(ctx, sheet, found[0], pictures)
                if not drawn:
                    blank.append(role.replace("签字", ""))
                else:
                    fields[f"must_{index}"] = f"“{role}”标签右侧的签字横线上有实际签名（手写或签名图片）；横线空白、只有打印体姓名为false。"
            # Layer two: only the columns the template sets.
            for cell in cells:
                text = cell_text(cell)
                name = _signer_label(text, SUPPLIER_EXTRA + PURCHASING)
                if name is None:
                    continue
                cell.value()
                drawn, _rows = self.sign_line(ctx, sheet, cell, pictures)
                key = f"extra_{len(extra_names)}"
                extra_names[key] = name
                if name in SUPPLIER_EXTRA:
                    supplier_extra.append(name)
                if not drawn:
                    extra_blank.append(name)
                else:
                    fields[key] = f"“{name}”签署栏有手写签字（打印体姓名不算）；栏位空白为false。"
        if supplier_extra and not blank and not extra_blank:
            fields["supplier_seal"] = "供应商签署区（编制、审核、批准栏或共用盖章处）至少有一处供应商印章。"
        problems = []
        if blank:
            problems.append("必签三方中" + "、".join(blank) + "未签字（签字栏右侧没有文字、图片或手写图形）")
        if extra_blank:
            problems.append("附加签署栏" + "、".join(extra_blank) + "空白，签署不闭环")
        if problems:
            return "NG", "材料产品及分供清单" + "；".join(problems) + "。"
        values, _unreadable = self._from_legacy(lambda agent: agent.visual_observation(fields))
        must = [self.res.SIGNATURES[int(name.split("_")[1])].replace("签字", "") for name, value in values.items() if name.startswith("must_") and value is False]
        extra = [extra_names[name] for name, value in values.items() if name in extra_names and value is False]
        if must:
            problems.append("必签三方中" + "、".join(must) + "未签字")
        if extra:
            problems.append("附加签署栏" + "、".join(extra) + "没有手写签字，签署不闭环")
        if values.get("supplier_seal") is False:
            problems.append("供应商侧签署区没有印章，签署不闭环")
        if problems:
            return "NG", "材料产品及分供清单" + "；".join(problems) + "。"
        if any(value is None for value in values.values()):
            raise unable("签字或印章尚未能从原件全部核实，需人工复核。")
        layer = "；模板设有附加签署栏" + "、".join(dict.fromkeys(supplier_extra)) + "，均已签署" if supplier_extra else "；模板未设附加签署栏，不计缺项"
        return "PASS", "供应商负责工程师、UAES NE工程师、UAES SQD/SQE工程师三方均已签字" + layer + "。"

    # ---- MSL:3 ----
    def identity_fields(self, ctx: RuleContext, item: Any) -> list[dict]:
        """Keep the original's own identity distinct from related drawings, BOM rows and order specifications."""
        version = str((item.file_metadata or {}).get("file_version_id") or item.audit_id)
        path = Path(str(item.file_url))
        expected = (item.file_metadata or {}).get("source_sha256") or (item.file_metadata or {}).get("sha256")
        if expected:
            with path.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                    raise OSError("冻结的关联资料已变化。")
        if path.suffix.lower() in {".xls", ".xlsx", ".xlsm"}:
            found = []
            with self.reading(ctx, item, self.lib.drawing_for_materials):
                for ws in ctx.workbook.worksheets:
                    sheet = Sheet(ctx, ws)
                    rows, width = self.extent(sheet)
                    cells = sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=width)
                    fields = {}
                    kind = "unknown"
                    for cell in cells:
                        value = str(cell.value() or "").strip()
                        if compact(value) in PARTS_TITLE:
                            kind = "parts_list"
                        elif compact(value) in DRAWING_TITLE and kind == "unknown":
                            kind = "drawing"
                        field = title_key(value)
                        if not field:
                            continue
                        merged = ctx.ops.get_detailed_merge_info(ws, cell.row_index, cell.col_index)
                        col = (merged.max_col if merged else cell.col_index) + 1
                        if col <= width:
                            raw = str(sheet.cell(cell.row_index, col).value() or "").strip()
                            if raw and not title_key(raw):
                                fields.setdefault(field, set()).add(raw)
                    if kind == "drawing" and "number" not in fields:
                        kind = "unknown"
                    found.append({"kind": kind, "where": f"{original_name(item)}!{ws.title}", **{k: next(iter(v)) for k, v in fields.items() if len(v) == 1}})
            return found or [{"kind": "unknown", "where": original_name(item)}]
        if path.suffix.lower() != ".pdf":
            return [{"kind": "unknown", "where": original_name(item)}]
        import fitz

        found = []
        with fitz.open(path) as doc:
            for page in doc:
                where = f"{original_name(item)} 第{page.number + 1}页"
                # Only upright visible strings, not oblique watermarks crossing the form's cells.
                lines = [
                    (fitz.Rect(s["bbox"]), s["text"].strip())
                    for b in page.get_text("dict", flags=0)["blocks"]
                    for line in b.get("lines", [])
                    if line["dir"][0] > 0.99
                    for s in line["spans"]
                    if s["text"].strip() and s.get("alpha", 255)
                ]

                def cite(box, label, value, visual=False):
                    self.legacy().witness({"kind": "pdf_region", "page": page.number + 1, "bbox": list(box), "fact": label}, value, version, visual=visual)

                # An expressly titled parts-list form, not a drawing. Report its two distinct header fields,
                # not the numbers in its component rows. This also works when the type classifier called it a drawing.
                top = [(r, v) for r, v in lines if r.y0 < page.rect.height * 0.3]
                list_title = next(((r, v) for r, v in top if compact(v) in PARTS_TITLE), None)
                if list_title and any(compact(v) in {"所属图纸", "related-drawing", "序号", "index/po"} for _, v in lines):
                    cite(list_title[0], "单据标题", list_title[1])
                    for labels, label in [({"number", "编号"}, "清单表头编号"), ({"related-drawing", "所属图纸"}, "所属图纸")]:
                        for box, value in pdf_field(top, labels, numbers=True):
                            cite(box, label, value)
                    found.append({"kind": "parts_list", "where": where})
                    continue
                # A page without a text layer is not a candidate for visual OCR (PSW alone has that exception).
                if not lines or page.rotation or any(fitz.Rect(img["bbox"]).get_area() >= 0.9 * page.rect.get_area() for img in page.get_image_info()):
                    found.append({"kind": "unknown", "where": where})
                    continue
                # Prefer an explicit drawing-number label. A naked number anywhere in the file is not an identity.
                values = {key: pdf_field(lines, labels, numbers=key == "number") for key, labels in TITLE.items()}
                # Independent identity: an explicit part-drawing heading or an actual engineering title block.
                # The same labels in a list of references do not turn that list into a part drawing.
                headings = {compact(v) for _, v in lines}
                drawing_layout = bool(headings & DRAWING_TITLE or headings & {"scale", "比例"} and headings & {"版本", "version", "sheet", "页次"})
                if drawing_layout and len(values["number"]) == 1 and len(values["name"]) == 1:
                    fields = {}
                    for field, items in values.items():
                        if len(items) == 1:
                            box, value = items[0]
                            fields[field] = value
                            cite(box, field, value)
                    found.append({"kind": "drawing", "where": where, **fields})
                    continue
                area = fitz.Rect(page.rect.width * 0.55, page.rect.height * 0.7, page.rect.width, page.rect.height)
                image = page.get_pixmap(matrix=fitz.Matrix(min(3, 2300 / max(area.width, area.height)), min(3, 2300 / max(area.width, area.height))), clip=area).tobytes("png")
                raw = self.transcribe(image, TITLE_PROMPT, TITLE_SCHEMA, where + "工程图标题栏")
                fields = {}
                # Require the visible field's own label, value and a local box; a related reference or BOM number
                # returned in the drawing-number slot must not become this part's number.
                if not isinstance(raw, dict):
                    raw = {}
                for field, record in raw.items():
                    if field not in TITLE or not isinstance(record, dict) or title_key(record.get("label")) != field:
                        continue
                    box = local_box(area, record.get("bbox"))
                    value = record.get("value")
                    if box is not None and isinstance(value, str) and value.strip():
                        fields[field] = value.strip()
                        cite(box, field, f"{record['label']}: {value}", visual=True)
                kind = "drawing" if raw.get("kind") == "drawing" and "number" in fields else "unknown"
                found.append({"kind": kind, "where": where, **fields})
        return found or [{"kind": "unknown", "where": original_name(item)}]

    @rule("同批图纸的零件名称与零件号一致")
    def msl_3(self, ctx: RuleContext):
        sheets = self.lists(ctx)
        if len(sheets) != 1:
            raise unable(f"原件有{len(sheets)}份材料及分供方清单，尚不能确定按哪一份核对图纸，需人工复核。")
        sheet = sheets[0]
        part = self.field_value(ctx, sheet, "零件号", self.res.PART_NUMBER)
        if self.lib._placeholder(compact(part)):
            raise unable(f"清单零件号为“{part}”，不能用于配对图纸。")
        refs = self.lib.related_by_content(self, _DRAWING)
        records = []
        for item in refs.items:
            try:
                records.extend(self.identity_fields(ctx, item))
            except GuardStop as stop:
                if stop.status != Decision.UNABLE:
                    raise
                records.append({"kind": "unknown", "where": original_name(item)})
        drawings = [r for r in records if r["kind"] == "drawing" and r.get("number")]
        unknown = [r["where"] for r in records if r["kind"] == "unknown" or r["kind"] == "drawing" and not r.get("number")]
        lists = "；已按表头排除零件清单：" + "、".join(r["where"] for r in records if r["kind"] == "parts_list") if any(r["kind"] == "parts_list" for r in records) else ""
        same = [r for r in drawings if layout_id(r["number"]) == layout_id(part)]
        if not same:
            if unknown:
                raise unable("尚不能确认图纸类型或本件图号：" + "、".join(unknown) + lists + "。")
            self.need("图纸", part=part)
            if not drawings:
                return "NG", "同批次没有图纸独立文件" + lists + "。"
            details = "；".join(f"{r['where']} 图号“{r['number']}”" for r in drawings)
            return "NG", f"比对源错配：清单零件号“{part}”；{details}。没有同号图纸，未比较零件名称{lists}。"
        # Project and designation cannot obscure an already established wrong-number source.
        project = self.field_value(ctx, sheet, "UAES项目编号", self.res.PROJECT)
        name = self.field_value(ctx, sheet, "零件名称", self.res.PART_NAME)
        if self.lib._placeholder(compact(project)) or not name:
            raise unable("已找到同号图纸，但清单的项目或零件名称无法用于核对。")
        eligible = [r for r in same if r.get("project") and layout_id(r["project"]) == layout_id(project)]
        if not eligible:
            if unknown or any(not r.get("project") for r in same):
                raise unable(f"找到零件号“{part}”的图纸，但尚不能从图纸字段确认项目“{project}”。")
            return "NG", f"同号图纸所列项目（{'、'.join(r['project'] for r in same)}）均不是清单项目“{project}”。"
        matches = [r for r in eligible if r.get("name", "").strip() == name.strip()]
        if matches:
            if len({r.get("name") for r in eligible}) > 1 and not any(compact(r.get("status")) in {"在用", "active", "current"} for r in matches):
                raise unable("同项目同件号有多份图纸，虽有名称相同者，但无法从原件确认该图在用。")
            matched = next((r for r in matches if compact(r.get("status")) in {"在用", "active", "current"}), matches[0])
            if matched.get("status") and compact(matched["status"]) not in {"在用", "active", "current"}:
                raise unable("同号图纸虽有相同名称，但其状态不能确认为在用。")
            return "PASS", f"{matched['where']} 的项目“{project}”、图号“{matched['number']}”、名称“{matched['name']}”与清单一致。"
        if unknown or any(not r.get("project") for r in same) or any(not r.get("name") for r in eligible):
            raise unable("存在身份、项目或名称未读全的候选图纸，不能判为名称不一致。")
        names = "；".join(f"{r['where']}：{r['name']}" for r in eligible)
        if any(re.search("[A-Za-z]", r["name"]) and re.search(r"[\u3400-\u9fff]", r["name"]) for r in eligible):
            raise unable(f"同号同项目图纸名称“{names}”与清单“{name}”非逐字相同；中英文对应关系待确认。")
        return "NG", f"零件名称不一致：同项目、同号图纸名称“{names}”，清单名称“{name}”。"

    # ---- MSL:4 ----
    def flow_nodes(self, ctx: RuleContext, item: Any) -> list[str]:
        """The flow chart's process nodes: the 过程描述 column of a spreadsheet, the text lines of a PDF.

        Control methods, characteristics and record names are not nodes; a spreadsheet without a
        过程描述 column cannot be read as nodes."""
        if str(item.file_url).lower().endswith(".pdf"):
            import fitz

            with fitz.open(str(item.file_url)) as document:
                if any(not page.get_text().strip() for page in document):
                    raise unable(f"过程流程图{original_name(item)}有页面没有文字层，检查程序未能读取，需人工复核。")
                return [line.strip() for line in "".join(page.get_text() for page in document).splitlines() if line.strip()]
        nodes: list[str] = []
        with self.reading(ctx, item, self.READER.drawing_for_materials):
            for worksheet in ctx.workbook.worksheets:
                sheet = Sheet(ctx, worksheet)
                rows, columns = self.extent(sheet)
                heads = [cell for cell in sheet.scan_non_empty(row_min=1, row_max=min(rows, 30), col_min=1, col_max=columns) if _found(cell_text(cell), FLOW_STEP)]
                for head in heads:
                    merged = ctx.ops.get_detailed_merge_info(sheet.ws, head.row_index, head.col_index)
                    last_row = merged.max_row if merged else head.row_index
                    span = range(head.col_index, (merged.max_col if merged else head.col_index) + 1)
                    for row in range(last_row + 1, rows + 1):
                        values = dict.fromkeys(" ".join(str(sheet.cell(row, column).value() or "").split()) for column in span)
                        text = " ".join(value for value in values if value)
                        if text and not _found(compact(text), FLOW_STEP):
                            nodes.append(text)
        if not nodes:
            raise unable(f"过程流程图{original_name(item)}未能定位“过程描述”列，检查程序未能读取工序节点，需人工复核。")
        return list(dict.fromkeys(nodes))

    def records(self, ctx: RuleContext, sheet: Sheet, part: str) -> list[dict[str, Any]]:
        """Each filled record: its row, names (part/material), and whether the list marks it outsourced.

        The record of the part itself (its part number is the list's own part number) is the part being made,
        not an outsourced item; its material is still a raw material."""
        section = self.section(sheet, "材料清单区")
        header, serial = section.resolved.header_row, section.column("材料序号")
        _rows, width = self.extent(sheet)
        heads = {column: compact("".join(str(sheet.cell(row, column).value() or "") for row in range(header, serial.data_start_row))) for column in range(1, width + 1)}
        name_columns = [column for column, head in heads.items() if _found(head, ("零件/部件名称", "部件名称", "子零件名称", "工序名称"))]
        material_columns = [column for column, head in heads.items() if _found(head, ("材料名称和材料牌号", "材料牌号", "材料名称"))]
        outsourced_columns = [column for column, head in heads.items() if _found(head, ("外协", "Subcontract", "国产", "进口"))]
        number_columns = [column for column, head in heads.items() if _found(head, ("零件号", "PartNo", "分部件零件号", "子件号"))]
        own = self.lib._identity(part) if part else ""
        found = []
        for row in section.rows("材料序号"):
            index = row.row_index
            values = {column: str(sheet.cell(index, column).value() or "").strip() for column in heads}
            if not any(value for column, value in values.items() if column != serial.header_col):
                continue
            text = compact("".join(values.values()))
            itself = bool(own) and any(self.lib._identity(values[column]) == own for column in number_columns if values[column])
            found.append(
                {
                    "row": index,
                    "names": [values[column] for column in name_columns if values[column]],
                    "materials": [values[column] for column in material_columns if values[column]],
                    "outsourced": not itself and (any(values[column] for column in outsourced_columns) or _found(text, OUTSOURCED)),
                    "itself": itself,
                    "text": text,
                }
            )
        return found

    @rule("分供清单与过程流程图双向溯源")
    def msl_4(self, ctx: RuleContext):
        flows = self.lib.related_by_content(self, _FLOW)
        if flows.missing:
            return "NA", "同批次未提交过程流程图，本条不适用（依据：条文“同批次未提交过程流程图时本条记‘不适用’”），不记作通过。"
        sheets = self.lists(ctx)
        records = []
        for sheet in sheets:
            try:
                part = self.field_value(ctx, sheet, "零件号", self.res.PART_NUMBER)
            except GuardStop:
                part = ""  # Without the list's own part number no record is taken as the part itself.
            records.extend(self.records(ctx, sheet, part))
        if not records:
            raise unable("分供清单没有已填写的记录行，检查程序未能核对双向溯源，需人工复核。")
        nodes: list[str] = []
        for item in flows.items:
            nodes.extend(self.flow_nodes(ctx, item))
        flow_text = compact("".join(nodes))
        material_nodes = [node for node in nodes if _found(compact(node), MATERIAL_ENTRY) and not _found(compact(node), IN_HOUSE)]
        unmatched_list: list[str] = []
        matched_list: list[str] = []
        # Direction 2: each outsourced item and each raw material of the list has a flow chart node.
        for record in records:
            for value in record["names"] if record["outsourced"] else []:
                key = compact(value)
                if key and key in flow_text:
                    matched_list.append(f"第{record['row']}行外协项“{value}”（流程图同名节点）")
                elif key:
                    unmatched_list.append(f"第{record['row']}行外协项“{value}”")
            for value in record["materials"]:
                key = compact(value)
                if key and key in flow_text:
                    matched_list.append(f"第{record['row']}行材料“{value}”（流程图同名节点）")
                elif key and material_nodes:
                    matched_list.append(f"第{record['row']}行材料“{value}”（流程图原材料进入节点“{material_nodes[0]}”）")
                elif key:
                    unmatched_list.append(f"第{record['row']}行材料“{value}”")
        # Direction 1: each outsourced process and raw material node of the flow chart has a list record.
        list_text = compact("".join(record["text"] for record in records))
        outsourced_nodes = [node for node in nodes if not _found(compact(node), IN_HOUSE) and (_found(compact(node), OUTSOURCED) or _found(compact(node), OUTSOURCED_KINDS))]
        named_material_nodes = [node for node in nodes if not _found(compact(node), IN_HOUSE) and _found(compact(node), RAW_MATERIAL)]
        if outsourced_nodes and not any(record["outsourced"] for record in records):
            return "NG", "过程流程图有外协工序节点（" + "、".join(f"“{node}”" for node in outsourced_nodes[:6]) + "），分供清单中没有任何外协记录，流程图外协工序未列入分供清单。"
        has_material = any(record["materials"] for record in records)
        if named_material_nodes and not has_material:
            return "NG", "过程流程图有原材料节点（" + "、".join(f"“{node}”" for node in named_material_nodes[:6]) + "），分供清单中没有任何材料记录，流程图原材料未列入分供清单。"
        unmatched_nodes = []
        for node in outsourced_nodes:
            bare = compact(node)
            for word in (*OUTSOURCED, "(", ")", "（", "）"):
                bare = bare.replace(compact(word), "")
            if not bare or bare not in list_text and not any(compact(name) in bare for record in records for name in [*record["names"], *record["materials"]] if compact(name)):
                unmatched_nodes.append(f"“{node}”")
        if material_nodes and not has_material:
            unmatched_nodes.extend(f"“{node}”" for node in material_nodes[:4])
        if unmatched_list or unmatched_nodes:
            parts = []
            if unmatched_list:
                parts.append("分供清单中" + "、".join(unmatched_list[:8]) + "在过程流程图中未找到同名或原材料进入（进料检验、熔化类）节点")
            if unmatched_nodes:
                parts.append("过程流程图中" + "、".join(unmatched_nodes[:8]) + "在分供清单中未找到对应记录")
            raise unable("；".join(parts) + "。条文允许按工程语义以简称或别名对应，文字不同不能据此判定缺失，需人工确认是否对应。")
        if not matched_list:
            raise unable("分供清单没有标为外协的项或原材料可供核对，检查程序未能确认双向溯源，需人工复核。")
        itself = [f"第{record['row']}行" for record in records if record["itself"]]
        note = ("；" + "、".join(itself) + "零件号即本清单零件号，是零件本身，不作外协项核对") if itself else ""
        if outsourced_nodes:
            reverse = "流程图外协工序节点（" + "、".join(f"“{node}”" for node in outsourced_nodes[:6]) + "）均在分供清单中有记录"
        else:
            reverse = "流程图“过程描述”列没有外协工序节点"
        if material_nodes:
            reverse += "，原材料进入节点（" + "、".join(f"“{node}”" for node in material_nodes[:4]) + "）在分供清单中有材料记录"
        return "PASS", "分供清单的外协项与原材料：" + "、".join(matched_list[:8]) + "，均在过程流程图中有对应节点；" + reverse + note + "。"


class Audit(AgentAudit):
    """Runs the declared UAES reader extended with this leaf's clause methods (see the module docstring)."""

    CLAUSES: dict[str, type] = {"UaesMaterialsAgent": MslClauses}

    def _implementation(self) -> Any:
        if self._agent is None and self._failure is None:
            reader = super()._implementation()
            if reader is None:
                return None
            base = type(reader)
            close = getattr(reader, "close", None)
            if callable(close):
                close()
            try:
                extended = type(base.__name__ + "R3", (self.CLAUSES[base.__name__], base), {"READER": sys.modules[base.__module__]})
                self._agent = extended(self.audit_file, self._implementation_rule(), group_context=self.group_context)
            except BaseException as exc:
                self._agent, self._failure = None, exc
                raise
        return super()._implementation()


def create_audit(audit_file, rule, group_context=None):
    return Audit(audit_file, rule, group_context, resource=RESOURCE)
