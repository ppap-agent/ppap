"""N04 product identifiers have no invented minimum digit count."""

import re

from audit.checkers.v3.excel_agent import rule
from audit.kinds import PpapDocumentType
from audit.precheck_facts import checker_fact

from .tables import NioTableAgent


class NioSpecialCharacteristicsAgent(NioTableAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST
    SHEET_KEYWORD = "特殊特性清单"

    @rule("标题前产品编号", clause_id="1")
    def rule_1(self, ctx):
        for sheet in self._sheets(ctx):
            for cells in sheet.iter_rows(max_row=min(sheet.max_row, 8)):
                for cell in cells:
                    text = str(cell.value or "")
                    if "特殊特性清单" in text:
                        ctx.note_cell(sheet.title, cell.row, cell.column)
                        prefix = text.split("特殊特性清单", 1)[0].strip()
                        if prefix and re.search(r"[A-Za-z0-9]", prefix):
                            return self._done(ctx)
                        return "NG", "特殊特性清单标题前缺少产品编号。"
        return "NG", "未找到特殊特性清单标题。"

    @rule("全部特性字段非空", clause_id="2")
    def rule_2(self, ctx):
        names = ("零件编号", "零件名称", "特性分类", "特性描述", "特性值", "特性类别", "特殊特性种类", "控制方法（制造过程）", "防错方案描述", "频次", "样本数")
        rows = self._data(ctx, {key: [key] for key in names})
        applicable = False
        for row in rows:
            if row["零件编号"] and row["零件名称"]:
                applicable = True
                missing = [name for name in names if not row[name]]
                if missing:
                    return "NG", "特性行缺少：" + "、".join(missing)
        return self._done(ctx) if applicable else ("UNABLE", "未识别到同时有零件编号及零件名称的有效数据行。")

    def precheck(self):
        names = ("零件编号", "零件名称", "特性分类", "特性描述", "特性值", "特性类别", "特殊特性种类", "控制方法（制造过程）", "防错方案描述", "频次", "样本数")
        return [
            checker_fact(self, "N04:S1", self.rule_1, code="W03"),
            checker_fact(self, "N04:S2", lambda ctx: self._columns(ctx, {key: [key] for key in names}), code="B05"),
            checker_fact(self, "N04:S3", self._identity_cells),
            checker_fact(self, "N04:S4", self.rule_2),
        ]

    def _identity_cells(self, ctx):
        rows = self._data(ctx, {key: [key] for key in ("零件编号", "零件名称", "特性描述")})
        active = [row for row in rows if row["特性描述"] or row["零件编号"] or row["零件名称"]]
        if any(not row["零件编号"] or not row["零件名称"] for row in active):
            return "NG", "特性行的零件编号或零件名称为空。"
        return self._done(ctx) if active else ("UNABLE", "没有可核实的特性行。")
