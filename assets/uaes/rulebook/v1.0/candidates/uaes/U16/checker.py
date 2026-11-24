"""UAES U16 candidate: process flow charts against UAES v1.0 revision 3 (PFD:1–PFD:4).

The reader is the UAES flow chart implementation that resources.py declares (cells, legend, placed drawings, stamp);
this leaf adds the revision 3 clause methods on top of it at run time. A leaf may not import a company implementation,
so the class and its module's names are taken from the carrier the shared loader built. Facts that exist only
as an image of the original (drawn marks, signatures, stamps) are read as yes/no facts by the image model; code decides.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from typing import Any

from audit.checker_lib.agents import AgentAudit
from audit.checker_lib.excel_agent import rule
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet
from resources import RESOURCE

_CHARACTERISTIC_MARK = re.compile(r"(?<![A-Za-z])(?:CC|SC|YC|YS)(?:/\d)?(?![A-Za-z])|[★◆]")
_SPECIAL_COLUMN = ("特殊特性", "特性分类", "特性类别", "special characteristic", "class")
_LEGEND_WORDS = re.compile(r"关键|重要|特殊|表示|代表|含义|定义|符号|key|critical|special", re.I)

# PFD:4 node classes; synonyms are matched as written, never inferred.
NODES = {
    "来料控制": r"进货检验|来料|进料|原材料入库|原料入库|原材料检验|入厂检验|incoming|receiv|IQC|免检|直送上线|供方自检",
    "发运控制": r"出货检验|出厂检验|发货检验|GP\s*-?\s*12|发运前|发货前|出货前|OQC|pre-?ship|免检",
    "异常处理": r"不合格|返工|返修|隔离|报废|偏差|nonconform|rework|repair|scrap|quarantin|isolat|deviation|MRB|不良品",
    "成品仓储与交付": r"成品入库|入库|仓储|仓库|库存|贮存|储存|包装|发运|发货|交付|出货|stock|storage|warehouse|pack|deliver|ship",
}
# Words a written "this process has no such node" statement names its node by.
NODE_WORDS = {"来料控制": r"来料|进货|原材料", "发运控制": r"发运|出货|发货", "异常处理": r"不合格|异常|返工|报废", "成品仓储与交付": r"仓储|入库|交付|成品"}
_DECLARATION = re.compile(r"不具备|无需|不设|不适用|无成品仓储|直发|单件试制|无独立仓储")


def _text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split())


def compact(value: Any) -> str:
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold().rstrip(":：")


def unable(message: str) -> GuardStop:
    return GuardStop(Decision.UNABLE, message)


class _Revision3:
    """Revision 3 clause methods over the UAES flow chart reader; `self._m` is its module."""

    # ---- cell facts -------------------------------------------------------------------------------------------

    def cells(self, ctx: RuleContext) -> list[tuple[Sheet, int, int, str]]:
        out = []
        for worksheet in ctx.workbook.worksheets:
            sheet = Sheet(ctx, worksheet)
            rows, columns = self.extent(sheet)
            for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns):
                out.append((sheet, cell.row_index, cell.col_index, _text(cell.value())))
        return out

    def page_text(self) -> list[tuple[int, str]]:
        import fitz

        with fitz.open(str(self.audit_file.file_url)) as document:
            return [(page.number + 1, _text(page.get_text())) for page in document]

    # ---- PFD:1 ------------------------------------------------------------------------------------------------

    @rule("关键工序标记、特性符号与图例")
    def rule_1(self, ctx: RuleContext):
        chart = self.chart(ctx)
        marked = None if chart is None else chart.key_operation()
        if marked is False:
            return "NG", f"过程流程图“{chart.sheet.title}”全部工序没有关键工序标记（K、★、◆、加粗、底色或虚框均未使用），第1项不满足。"
        cells = [] if chart is None else self.cells(ctx)
        symbols = sorted({match.group(0) for _sheet, _row, _column, text in cells for match in _CHARACTERISTIC_MARK.finditer(text)})
        if chart is not None:
            # The special characteristic column: wholly blank while the batch's drawing or control plan has special characteristics.
            header = chart.header
            _rows, columns = self.extent(chart.sheet)
            special = [column for column in range(1, columns + 1) if any(word in compact(chart.text(header, column)) for word in map(compact, _SPECIAL_COLUMN))]
            if special and not any(chart.text(row, column) for column in special for row in chart.operation_rows()):
                counterparts = [kind for kind in ("控制计划", "图纸", "产品图纸") if self.batch_files(kind)]
                if counterparts:
                    raise unable(f"过程流程图特殊特性列整列空白；同批次有{'、'.join(counterparts)}，检查程序尚未读取其中是否确有特殊特性（若有即按漏标判不通过），需人工复核。")
        legend_cells = [
            (sheet, row, column, text)
            for sheet, row, column, text in cells
            if any(symbol in text for symbol in symbols) and _LEGEND_WORDS.search(text.replace(next((s for s in symbols if s in text), ""), ""))
        ]
        key_legend = [text for _sheet, _row, _column, text in cells if re.search(r"(?<![A-Za-z])K\d*(?![A-Za-z])|[★◆]", text) and _LEGEND_WORDS.search(text)]
        if marked and symbols and legend_cells and key_legend:
            sheet, row, column, text = legend_cells[0]
            self.cite(sheet, row, column)
            return "PASS", f"工序有关键工序标记；特性列有符号{'、'.join(symbols)}；图例/注释“{text[:60]}”说明了标识含义。"
        # What the cells do not show is read from an image of the original, one yes/no fact each; code decides.
        fields = {name: text for name, text in self.KEY_MARKS.items() if not (marked and name == "key_operation")}
        if symbols and legend_cells and key_legend:
            fields.pop("characteristic_marks", None)
            fields.pop("legend", None)
        return self.visual(fields, "关键工序、特性标记和标记定义", fails_unidentified=False)

    # ---- PFD:2 ------------------------------------------------------------------------------------------------

    @rule("图形符号规范性")
    def rule_2(self, ctx: RuleContext):
        chart = self.chart(ctx)
        if chart is None:
            # Revision 3: what cannot be made out of the graphics is "未审", never a failure.
            status, basis = self.visual(self.SYMBOL_FIELDS, "全图标准符号、完整图例、一致性及检验符号", fails_unidentified=False)
            return status, basis + "（取证方式：原件图像目视识读；判定依据请人工复核）"
        try:
            status, basis = super().rule_2(ctx)
        except GuardStop as stop:
            if stop.decision == Decision.UNABLE:
                raise unable(stop.message + "（取证方式：单元格与图形解析，图形仍不可辨，记未审）") from None
            raise
        if status != "PASS":
            return status, basis
        inspected = [operation for operation in chart.operations if re.search(self._m.flow_resources.INSPECTION_ACTIVITY, operation.description, re.I)]
        if not inspected:
            raise unable("全图检索不到任何检验类活动；是否应设检验工序属工程有效性判断，移交客户裁定，不判不通过。")
        return status, basis + "（取证方式：单元格符号与图例解析）"

    # ---- PFD:3 ------------------------------------------------------------------------------------------------

    @rule("受控章")
    def rule_3(self, ctx: RuleContext):
        return super().rule_3(ctx)

    # ---- PFD:4 ------------------------------------------------------------------------------------------------

    @rule("四类核心控制节点")
    def rule_4(self, ctx: RuleContext):
        chart = self.chart(ctx)
        pdf = str(self.audit_file.file_url).lower().endswith(".pdf")
        places: list[tuple[str, str]] = []  # (where, text)
        if chart is not None:
            for operation in chart.operations:
                places.append((f"工序{' '.join(operation.step.split()) or '?'}（第{operation.rows[0]}行）", _text(operation.description)))
            resources = self._m.flow_resources
            for sheet, row, column, text in self.cells(ctx):
                if row in chart.operation_rows() and sheet.ws is chart.sheet.ws:
                    continue
                # A legend entry names a symbol, not a node of this process.
                if any(symbol in text for symbol in resources.SYMBOLS) and len(text) < 20:
                    continue
                places.append((f"“{sheet.title}”第{row}行", text))
        if pdf:
            unread = self.unread_pages()
            if unread and chart is None:
                raise unable(f"原件第{'、'.join(map(str, unread))}页没有文字层，控制节点未能读取，需人工复核。")
            places.extend((f"第{page}页", text) for page, text in self.page_text())
        if not places:
            raise unable("过程流程图未能读出任何工序或文字，需人工复核。")
        found, missing, declared = {}, [], {}
        for node, pattern in NODES.items():
            # Finished-goods storage and delivery come last in a flow; the latest place names it best.
            operations = len(chart.operations) if chart is not None else 0
            ordered = places[:operations][::-1] + places[operations:] if node == "成品仓储与交付" else places
            hit = next(((where, text) for where, text in ordered if re.search(pattern, text, re.I)), None)
            if hit:
                found[node] = hit
                continue
            statement = next(((where, text) for where, text in places if _DECLARATION.search(text) and re.search(NODE_WORDS[node], text)), None)
            if statement:
                declared[node] = statement
            else:
                missing.append(node)
        if missing:
            if pdf and self.unread_pages():
                raise unable(f"可读文字中缺少{'、'.join(missing)}节点，原件第{'、'.join(map(str, self.unread_pages()))}页没有文字层，需人工复核。")
            shown = "；".join(f"{node}：{where}“{text[:30]}”" for node, (where, text) in found.items())
            return "NG", f"过程流程图缺少{'、'.join(missing)}节点（全表合并检索，亦无书面说明）。已有：{shown}。"
        shown = "；".join(f"{node}：{where}“{text[:30]}”" for node, (where, text) in found.items())
        statements = "；".join(f"{node}按书面说明“{text[:60]}”（{where}）" for node, (where, text) in declared.items())
        return "PASS", f"四类核心控制节点齐备（全表合并检索）：{shown}{'；' + statements if statements else ''}。"


_BUILT: dict[type, type] = {}


def _extend(base: type) -> type:
    if base not in _BUILT:
        _BUILT[base] = type("UaesProcessFlowRevision3", (_Revision3, base), {"_m": sys.modules[base.__module__], "__module__": __name__})
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
