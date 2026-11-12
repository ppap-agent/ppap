"""UAES material and subcontractor list: complete fields, three sign-offs and the same part's drawing."""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from audit.checker_lib.nio.excel_agent import rule
from audit.engine.rule_engine import Decision, RuleContext, Sheet, Table

from .excel import UaesExcelAgent, compact, unable
from .resources import combine, drawing_for_materials
from .resources import materials as resources

_DRAWING = ("图纸", "drawing")


def _identity(value: Any) -> str:
    """A part number or project as written, without spaces or case; a spreadsheet number keeps no trailing .0."""
    return re.sub(r"^(\d+)\.0+$", r"\1", compact(value))


def _placeholder(value: str) -> bool:
    from audit.precheck.pfmea_local import pair_identity

    return not pair_identity(value) or bool(re.search(r"x{3,}", value, re.I))


@dataclass
class Related:
    """Same-round originals that may be the document a clause names: typed as it, or of no recognised type."""

    items: list = field(default_factory=list)

    @property
    def missing(self) -> bool:
        return not self.items

    @property
    def names(self) -> list[str]:
        return [str((item.file_metadata or {}).get("original_filename") or Path(str(item.file_url)).name) for item in self.items]


def related_by_content(agent: UaesExcelAgent, terms: tuple[str, ...]) -> Related:
    """Other originals of this round typed as `terms`, plus those whose type was not recognised (their content decides)."""
    from .native import _kind_matches

    group = agent.group_context
    if group is None:
        return Related()
    for kind in group.unavailable_files.values():
        if not kind or _kind_matches(kind, terms):
            raise OSError("本轮有已提供但无法读取的关联资料，不能完成本项核对。")
    own = agent.audit_file.audit_id
    typed = {item.audit_id for items in group.by_doc_type.values() for item in items}
    found = {item.audit_id: item for kind, items in group.by_doc_type.items() if _kind_matches(kind, terms) for item in items if item.audit_id != own}
    found |= {item.audit_id: item for item in group.by_audit_id.values() if item.audit_id != own and item.audit_id not in typed}
    return Related(list(found.values()))


def drawing_anchors(path: str, title: str) -> list[tuple[int, int, int, int]] | None:
    """(first row, first column, last row, last column), 1-based, of every DrawingML object on an .xlsx sheet; None for other formats."""
    if not path.lower().endswith((".xlsx", ".xlsm")):
        return None
    ns = {
        "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "p": "http://schemas.openxmlformats.org/package/2006/relationships",
        "x": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    }

    def target(base: str, rels: str, rid: str) -> str:
        for relation in ElementTree.fromstring(archive.read(rels)).findall("p:Relationship", ns):
            if relation.get("Id") == rid:
                location = relation.get("Target", "")
                return location.lstrip("/") if location.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(base), location))
        raise KeyError(rid)

    with zipfile.ZipFile(path) as archive:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        rid = next((sheet.get(f"{{{ns['r']}}}id") for sheet in workbook.iterfind("m:sheets/m:sheet", ns) if sheet.get("name") == title), None)
        if rid is None:
            return None
        part = target("xl/workbook.xml", "xl/_rels/workbook.xml.rels", rid)
        drawing = ElementTree.fromstring(archive.read(part)).find("m:drawing", ns)
        if drawing is None:
            return []
        rels = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
        drawing_part = target(part, rels, drawing.get(f"{{{ns['r']}}}id", ""))
        anchors = []
        for anchor in ElementTree.fromstring(archive.read(drawing_part)):
            start, end = anchor.find("x:from", ns), anchor.find("x:to", ns)
            if start is None:
                # An absolute anchor has no cell position: nothing can be excluded.
                return None
            first = (int(start.findtext("x:row", "0", ns)) + 1, int(start.findtext("x:col", "0", ns)) + 1)
            last = (int(end.findtext("x:row", "0", ns)) + 1, int(end.findtext("x:col", "0", ns)) + 1) if end is not None else first
            anchors.append((first[0], first[1], last[0], last[1]))
        return anchors


def _covers(anchors: list[tuple[int, int, int, int]], rows: tuple[int, int], columns: tuple[int, int]) -> bool:
    return any(top <= rows[1] and bottom >= rows[0] and left <= columns[1] and right >= columns[0] for top, left, bottom, right in anchors)


class UaesMaterialsAgent(UaesExcelAgent):
    DOCUMENT_MODULE = resources
    EXTRA_RESOURCE = combine(resources.DOCUMENT)

    def precheck(self):
        """Preparation facts still come from the snapshot reader."""
        from .native import UaesSourceAgent
        from .precheck_l1 import precheck_materials

        return precheck_materials(UaesSourceAgent(self.audit_file, self.rule, group_context=self.group_context))

    def lists(self, ctx: RuleContext) -> list[Sheet]:
        sheets = Table(ctx).find_sheets("材料清单", on_missing=Decision.IGNORE)
        if not sheets:
            if self.untabled() or self.unread_pages():
                raise unable("PDF原件未能读出表格，检查程序未能读取材料及分供方清单，需人工复核。")
            raise unable("检查程序未能找到“材料及分供方清单”工作表，需人工复核。")
        return sheets

    def labels(self, sheet: Sheet, aliases: tuple[str, ...]) -> list:
        wanted = {compact(alias) for alias in aliases}
        rows, columns = self.extent(sheet)
        return [cell for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns) if compact(cell.value()) in wanted]

    def beside(self, ctx: RuleContext, sheet: Sheet, aliases: tuple[str, ...]) -> list:
        """The cell right after each label (past the label's merged area): where the template puts its value."""
        found = []
        for label in self.labels(sheet, aliases):
            merged = ctx.ops.get_detailed_merge_info(sheet.ws, label.row_index, label.col_index)
            found.append(sheet.cell(label.row_index, (merged.max_col if merged else label.col_index) + 1))
        return found

    def field_value(self, ctx: RuleContext, sheet: Sheet, name: str, aliases: tuple[str, ...]) -> str:
        cells = self.beside(ctx, sheet, aliases)
        if len(cells) != 1:
            raise unable(f"“{sheet.title}”中{'未能定位' if not cells else '有多处'}“{name}”字段，检查程序未能读取，需人工复核。")
        return str(cells[0].value() or "").strip()

    def pictures(self, sheet: Sheet) -> list[tuple[int, int, int, int]] | None:
        """Objects drawn on the original sheet; None when the original's drawings cannot be read (xls, PDF)."""
        if sheet.ws.parent is not self._workbook_cache:
            return None
        return drawing_anchors(str(self.audit_file.file_url), sheet.title)

    @staticmethod
    def leaf_label(labels: list[str]) -> str:
        return "/".join(re.split(r"\s{2,}|\n|　", label.strip())[0] for label in labels)

    @rule("材料清单除备注外逐字段非空")
    def rule_1(self, ctx: RuleContext):
        problems, unknown, checked = [], [], 0
        for sheet in self.lists(ctx):
            for name, aliases in resources.HEADER_FIELDS.items():
                cells = self.beside(ctx, sheet, aliases)
                if len(cells) != 1:
                    unknown.append(f"未能定位“{name}”字段")
                elif not cells[0].has_data():
                    problems.append(f"{name}（{cells[0].ws.cell(cells[0].row_index, cells[0].col_index).coordinate}）为空")
            section = self.section(sheet, "材料清单区")
            header, serial = section.resolved.header_row, section.column("材料序号")
            _rows, width = self.extent(sheet)
            # Each column is named by the header rows above the data; one choice spreads over a group of columns.
            leaves: dict[int, tuple[str, str | None]] = {}
            for column in range(1, width + 1):
                labels = list(dict.fromkeys(str(sheet.cell(row, column).value() or "").strip() for row in range(header, serial.data_start_row)))
                labels = [label for label in labels if label]
                if not labels or any("备注" in label for label in labels):
                    continue
                group = None if any(compact(own) in compact("".join(labels)) for own in resources.OWN_FIELDS) else next((name for name in resources.CHOICE_GROUPS if name in compact(labels[0])), None)
                leaves[column] = (self.leaf_label(labels), group)
            pictures = self.pictures(sheet)
            for row in section.rows("材料序号"):
                index = row.row_index
                filled = {column for column in leaves if sheet.cell(index, column).has_data()}
                for column, (label, _group) in leaves.items():
                    if resources.PICTURE in label and column not in filled and pictures and _covers(pictures, (index, index), (column, column)):
                        filled.add(column)
                if not filled - {serial.header_col}:
                    continue  # A numbered template row nobody filled in is not a listed item.
                checked += 1
                empty_groups = set()
                for column, (label, group) in leaves.items():
                    if column in filled:
                        continue
                    if group is not None:
                        if not any(other in filled for other, (_label, member) in leaves.items() if member == group):
                            empty_groups.add(group)
                    elif resources.PICTURE in label and pictures is None:
                        unknown.append(f"第{index}行{label}的图片是否存在未能从原件读取")
                    else:
                        problems.append(f"第{index}行{label}为空")
                problems.extend(f"第{index}行{group}未选择" for group in sorted(empty_groups))
        if problems:
            return "NG", "材料及分供方清单除备注外有空字段：" + "；".join(problems[:12]) + ("等" if len(problems) > 12 else "") + "。"
        if unknown:
            raise unable("检查程序未能确认：" + "；".join(unknown[:8]) + "，需人工复核。")
        if not checked:
            raise unable("材料及分供方清单没有已填写的物料行，不能按空清单通过。")
        return "PASS", f"已逐行核实{checked}个物料行及表头字段，除备注外均已填写。"

    @rule("供应商及UAES两方工程师实际签字")
    def rule_2(self, ctx: RuleContext):
        blank, open_ = [], []
        for sheet in self.lists(ctx):
            pictures = self.pictures(sheet)
            height, width = self.extent(sheet)
            for role in resources.SIGNATURES:
                found = [cell for cell in sheet.scan_non_empty(row_min=1, row_max=height, col_min=1, col_max=width) if compact(cell.value()).startswith(compact(role))]
                if len(found) != 1:
                    raise unable(f"“{sheet.title}”中{'未能定位' if not found else '有多处'}“{role}”签字栏，检查程序未能读取，需人工复核。")
                label = found[0]
                merged = ctx.ops.get_detailed_merge_info(sheet.ws, label.row_index, label.col_index)
                rows = (merged.min_row, merged.max_row) if merged else (label.row_index, label.row_index)
                start = (merged.max_col if merged else label.col_index) + 1
                written = any(sheet.cell(row, column).has_data() for row in range(rows[0], rows[1] + 1) for column in range(start, width + 1))
                # The signature line right of the label: no writing and no picture or drawing there is no signature.
                if not written and pictures is not None and not _covers(pictures, rows, (start, width)):
                    blank.append(role)
                else:
                    open_.append(role)
        if blank:
            return "NG", "材料及分供方清单" + "、".join(blank) + "处没有签字（签字栏右侧没有文字、图片或手写图形）。"
        # What is on a signature line (a picture, handwriting) is read from the image of the original.
        labels = {
            "supplier_signature": "供应商负责工程师签字/Supplier Authorized Signature",
            "ne_signature": "UAES NE工程师签字/UAES NE Signature",
            "sqe_signature": "UAES SQD/SQE工程师签字/UAES SQD/SQE Signature",
        }
        return self.visual({name: f"“{label}”标签右侧的签字横线上有实际签名（手写或签名图片）；横线空白为false。" for name, label in labels.items()}, "三方实际签字")

    @rule("同项目同物料独立图纸的零件名称和编号")
    def rule_3(self, ctx: RuleContext):
        sheets = self.lists(ctx)
        if len(sheets) != 1:
            raise unable(f"原件有{len(sheets)}份材料及分供方清单，尚不能确定按哪一份核对图纸，需人工复核。")
        sheet = sheets[0]
        part = self.field_value(ctx, sheet, "零件号", resources.PART_NUMBER)
        name = self.field_value(ctx, sheet, "零件名称", resources.PART_NAME)
        project = self.field_value(ctx, sheet, "UAES项目编号", resources.PROJECT)
        drawings = related_by_content(self, _DRAWING)
        if drawings.missing:
            return "NG", "同批次没有图纸独立文件，请复审或提交图纸文件一起审核。"
        if _placeholder(_identity(part)):
            raise unable(f"清单零件号为“{part}”，不能用于配对同物料编号的图纸，需人工复核。")
        same, unknown = [], []
        for item in drawings.items:
            found = self.drawing_shows(ctx, item, part)
            if found is True:
                same.append(item)
            elif found is None:
                unknown.append(Related([item]).names[0])
        if not same:
            if unknown:
                raise unable(f"同批次图纸（{'、'.join(unknown)}）的零件号未能读取，尚不能确认是否为物料编号“{part}”的图纸，需人工复核。")
            return "NG", f"同批次图纸（{'、'.join(drawings.names)}）均不是物料编号“{part}”的图纸，请复审或提交同物料编号的图纸一起审核。"
        if len(same) > 1:
            raise unable(f"同批次有{len(same)}份物料编号“{part}”的图纸（{'、'.join(Related(same).names)}），尚不能确定按哪一份核对，需人工复核。")
        drawing = same[0]
        # The clause pairs by project as well; then the drawing's part name must be the list's.
        if _placeholder(_identity(project)):
            raise unable(f"清单UAES项目编号为“{project}”，尚不能确认图纸是否同项目，需人工复核。")
        if not self.drawing_mentions(ctx, drawing, project):
            raise unable(f"图纸{Related([drawing]).names[0]}未能读取到项目“{project}”，尚不能确认同项目，需人工复核。")
        if not self.drawing_mentions(ctx, drawing, name):
            raise unable(f"图纸{Related([drawing]).names[0]}未能读取到零件名称“{name}”，尚不能确认与清单一致，需人工复核。")
        return "PASS", f"同批次图纸{Related([drawing]).names[0]}与清单同项目“{project}”、零件号“{part}”、零件名称“{name}”一致。"

    def drawing_cells(self, ctx: RuleContext, item: Any) -> list[str] | None:
        """Every cell value of a spreadsheet drawing (read as evidence of its version); None for a PDF."""
        if str(item.file_url).lower().endswith(".pdf"):
            return None
        values = []
        with self.reading(ctx, item, drawing_for_materials):
            for worksheet in ctx.workbook.worksheets:
                sheet = Sheet(ctx, worksheet)
                rows, columns = self.extent(sheet)
                values.extend(_identity(cell.value()) for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns))
        return values

    def drawing_mentions(self, ctx: RuleContext, item: Any, value: str) -> bool:
        wanted = _identity(value)
        cells = self.drawing_cells(ctx, item)
        if cells is not None:
            return any(wanted in cell for cell in cells)
        import fitz

        with fitz.open(str(item.file_url)) as document:
            return wanted in _identity("".join(page.get_text() for page in document))

    def drawing_shows(self, ctx: RuleContext, item: Any, part: str) -> bool | None:
        """Whether a same-round drawing is of this part number; None when its identity cannot be read."""
        if self.drawing_mentions(ctx, item, part):
            return True
        cells = self.drawing_cells(ctx, item)
        if cells is not None:
            # Every cell was read; a spreadsheet without drawn objects that never names the part is another part's.
            from audit.checker_lib.excel import load_workbook

            path = str(item.file_url)
            book = load_workbook(path)
            try:
                drawn = any(drawing_anchors(path, title) != [] for title in book.sheetnames)
            finally:
                book.close()
            return None if drawn else False
        # A PDF drawing's title block is often vector graphics without a text layer; a text layer that does not
        # name the part leaves its number unread. Asking an image reader to confirm an expected number is not a reading.
        return None
