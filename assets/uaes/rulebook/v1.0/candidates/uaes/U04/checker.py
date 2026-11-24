"""UAES U04 candidate: material and subcontractor list clauses of UAES v1.0 revision 3 (MSL:1-MSL:4).

A leaf carries only checker.py and resources.py and may not import the UAES implementation. resources.py
declares the UAES materials reader as the carrier; at run time `Audit` extends the declared reader class with
the revision 3 clause methods below (`MslClauses`), so the reader's table, signature and drawing readings are
reused. Verdicts are decided in code; the image model only reads signatures, seals and a PDF drawing's title block.
"""

from __future__ import annotations

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


def _found(text: str, spellings: tuple[str, ...]) -> bool:
    return any(compact(spelling) in text for spelling in spellings)


def _signer_label(text: str, names: tuple[str, ...]) -> str | None:
    """A sign-off label cell: the name alone, optionally with 人/者 and English, never a sentence that mentions it."""
    bare = re.sub(r"[\W_a-z0-9]+", "", text)
    for name in names:
        if bare in {compact(name), compact(name) + "人", compact(name) + "者"}:
            return name
    return None


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
    def drawing_identity(self, ctx: RuleContext, item: Any) -> tuple[list[str] | None, dict[str, Any] | None]:
        """A drawing's cells (spreadsheet) or its transcribed title block (PDF)."""
        cells = self.drawing_cells(ctx, item)
        if cells is not None:
            return cells, None
        return None, self.drawing_title(item)

    @rule("同批图纸的零件名称与零件号一致")
    def msl_3(self, ctx: RuleContext):
        sheets = self.lists(ctx)
        if len(sheets) != 1:
            raise unable(f"原件有{len(sheets)}份材料及分供方清单，尚不能确定按哪一份核对图纸，需人工复核。")
        sheet = sheets[0]
        part = self.field_value(ctx, sheet, "零件号", self.res.PART_NUMBER)
        name = self.field_value(ctx, sheet, "零件名称", self.res.PART_NAME)
        project = self.field_value(ctx, sheet, "UAES项目编号", self.res.PROJECT)
        drawings = self.lib.related_by_content(self, _DRAWING)
        if drawings.missing:
            self.need("图纸")
            return "NG", "同批次没有图纸独立文件，缺少图纸。"
        if self.lib._placeholder(self.lib._identity(part)):
            raise unable(f"清单零件号为“{part}”，不能用于配对图纸，需人工复核。")
        same, other, unknown = [], [], []
        for item in drawings.items:
            found = self.drawing_shows(ctx, item, part)
            if found is True:
                same.append(item)
            elif found is False:
                other.append(item)
            else:
                unknown.append(original_name(item))
        if not same:
            if unknown:
                raise unable(f"同批次图纸（{'、'.join(unknown)}）的零件号未能读取，尚不能确认是否为零件号“{part}”的图纸，需人工复核。")
            numbers = []
            for item in other:
                if str(item.file_url).lower().endswith(".pdf"):
                    numbers.append(f"{original_name(item)}的图号“{self.drawing_title(item).get('drawing_number')}”")
                else:
                    numbers.append(original_name(item))
            self.need("图纸", part=part)
            return "NG", f"比对源错配：同批次图纸与分供清单的零件号本身不一致（清单零件号“{part}”；图纸：{'；'.join(numbers)}），没有与当前物料编号一致的图纸。"
        # The clause asks for the drawing of the current project as well.
        if self.lib._placeholder(self.lib._identity(project)):
            raise unable(f"清单UAES项目编号为“{project}”，尚不能确认图纸是否为当前项目的图纸，需人工复核。")
        same_project = [item for item in same if self.drawing_mentions(ctx, item, project)]
        if not same_project:
            raise unable(f"零件号“{part}”的图纸（{'、'.join(self.lib.Related(same).names)}）未能读取到项目“{project}”，尚不能确认是否为当前项目的图纸，需人工复核。")
        # Two drawings in use for the same project and part: the list's name matching either is consistent.
        unread = []
        for item in same_project:
            cells, title = self.drawing_identity(ctx, item)
            if cells is not None:
                if any(self.lib._identity(name) in self.lib._names(cell) for cell in cells):
                    return "PASS", f"同批次图纸{original_name(item)}与清单同项目“{project}”，零件号“{part}”、零件名称“{name}”一致。"
                continue
            designation = str((title or {}).get("designation") or "")
            if not designation:
                unread.append(original_name(item))
                continue
            words = {self.lib._identity(word) for word in re.split(r"[\s/／,，]+", designation)} | {self.lib._identity(designation)}
            if self.lib._identity(name) in words:
                return "PASS", f"同批次图纸{original_name(item)}与清单同项目“{project}”，零件号“{part}”一致，标题栏名称“{designation}”与清单零件名称“{name}”一致。"
        if unread:
            raise unable(f"图纸{'、'.join(unread)}的零件名称未能读取，尚不能确认与清单零件名称“{name}”一致，需人工复核。")
        return "NG", f"零件名称不一致：同批次零件号“{part}”的图纸（{'、'.join(self.lib.Related(same_project).names)}）中没有清单零件名称“{name}”。"

    # ---- MSL:4 ----
    def related_texts(self, ctx: RuleContext, item: Any) -> list[str]:
        cells = self.drawing_cells(ctx, item)
        if cells is not None:
            return cells
        import fitz

        with fitz.open(str(item.file_url)) as document:
            if any(not page.get_text().strip() for page in document):
                raise unable(f"过程流程图{original_name(item)}有页面没有文字层，检查程序未能读取，需人工复核。")
            return "".join(page.get_text() for page in document).splitlines()

    def records(self, ctx: RuleContext, sheet: Sheet) -> list[dict[str, Any]]:
        """Each filled record: its row, names (part/material), and whether the list marks it outsourced."""
        section = self.section(sheet, "材料清单区")
        header, serial = section.resolved.header_row, section.column("材料序号")
        _rows, width = self.extent(sheet)
        heads = {column: compact("".join(str(sheet.cell(row, column).value() or "") for row in range(header, serial.data_start_row))) for column in range(1, width + 1)}
        name_columns = [column for column, head in heads.items() if _found(head, ("零件/部件名称", "部件名称", "子零件名称", "工序名称"))]
        material_columns = [column for column, head in heads.items() if _found(head, ("材料名称和材料牌号", "材料牌号", "材料名称"))]
        outsourced_columns = [column for column, head in heads.items() if _found(head, ("外协", "Subcontract", "国产", "进口"))]
        found = []
        for row in section.rows("材料序号"):
            index = row.row_index
            values = {column: str(sheet.cell(index, column).value() or "").strip() for column in heads}
            if not any(value for column, value in values.items() if column != serial.header_col):
                continue
            text = compact("".join(values.values()))
            found.append(
                {
                    "row": index,
                    "names": [values[column] for column in name_columns if values[column]],
                    "materials": [values[column] for column in material_columns if values[column]],
                    "outsourced": any(values[column] for column in outsourced_columns) or _found(text, OUTSOURCED),
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
        records = [record for sheet in sheets for record in self.records(ctx, sheet)]
        if not records:
            raise unable("分供清单没有已填写的记录行，检查程序未能核对双向溯源，需人工复核。")
        texts: list[str] = []
        for item in flows.items:
            texts.extend(self.related_texts(ctx, item))
        flow_text = compact("".join(texts))
        unmatched_list: list[str] = []
        matched_list: list[str] = []
        # Direction 2: each outsourced item and each raw material of the list has a flow chart node.
        for record in records:
            wanted = [*(record["names"] if record["outsourced"] else []), *record["materials"]]
            for value in wanted:
                key = compact(value)
                if key and key in flow_text:
                    matched_list.append(f"第{record['row']}行“{value}”")
                elif key:
                    unmatched_list.append(f"第{record['row']}行“{value}”")
        # Direction 1: each outsourced process and raw material node the flow chart writes has a list record.
        list_text = compact("".join(record["text"] for record in records))
        nodes = []
        for text in texts:
            own = compact(text)
            if not own or _found(own, IN_HOUSE):
                continue
            if _found(own, OUTSOURCED) or _found(own, OUTSOURCED_KINDS) or _found(own, RAW_MATERIAL):
                nodes.append(str(text).strip())
        outsourced_nodes = [node for node in nodes if _found(compact(node), OUTSOURCED + OUTSOURCED_KINDS)]
        if outsourced_nodes and not any(record["outsourced"] for record in records):
            return "NG", "过程流程图有外协工序节点（" + "、".join(f"“{node}”" for node in outsourced_nodes[:6]) + "），分供清单中没有任何外协记录，流程图外协工序未列入分供清单。"
        unmatched_nodes = []
        for node in nodes:
            bare = compact(node)
            for word in (*OUTSOURCED, *RAW_MATERIAL, "(", ")", "（", "）"):
                bare = bare.replace(compact(word), "")
            if not bare or bare not in list_text and not any(compact(name) in bare for record in records for name in [*record["names"], *record["materials"]] if compact(name)):
                unmatched_nodes.append(f"“{node}”")
        if unmatched_list or unmatched_nodes:
            parts = []
            if unmatched_list:
                parts.append("分供清单中" + "、".join(unmatched_list[:8]) + "在过程流程图中未找到同名节点")
            if unmatched_nodes:
                parts.append("过程流程图中" + "、".join(unmatched_nodes[:8]) + "在分供清单中未找到同名记录")
            raise unable("；".join(parts) + "。条文允许按工程语义以简称或别名对应，文字不同不能据此判定缺失，需人工确认是否对应。")
        if not matched_list:
            raise unable("分供清单没有标为外协的项或原材料可供核对，检查程序未能确认双向溯源，需人工复核。")
        reverse = "均在分供清单中有记录。" if nodes else "未见，无需反向对应。"
        return "PASS", "分供清单的外协项与原材料（" + "、".join(matched_list[:8]) + "）均在过程流程图中有同名节点；流程图中写明外协或原材料的节点" + reverse


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
