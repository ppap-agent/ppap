"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

import re

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, header, key
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def rule_1(self):
        names = ("零件供应商名称", "提交日期")
        for name in names:
            if key(self.native().field((name,))) in {key(label) for label in names}:
                raise UnknownFact("字段只读到相邻标签，尚不能证明有实际填写内容。")
        return ("PASS", "供应商名称与提交日期均有实际原件内容。")

    def rule_2(self):
        names = ("一级分总成零件名称", "分总成组件或分序模具名称", "模具制造供应商", "模具存放地点", "零件质量评估", "模具分供方及实物VS 数据&标准验收", "模具量产性能评估", "模具维保评估")
        for row in self.native().rows({"process": ("工艺类型",), **{name: (name,) for name in names}}, anchor=("process",)):
            if any((blank(row[name]) for name in names)):
                return ("NG", "有工艺类型的模具行存在未填写的必填字段。")
        return ("PASS", "每个有工艺类型的模具行必填内容均已填写。")

    def column_rows(self, name):
        facts = self.native()
        table = facts.table(("模具验收",))
        aliases = {name: (name,)}
        start, _columns = facts.columns(table, aliases)
        for marker in ("工艺类型", "一级分总成零件名称"):
            if any((header(facts.readers[table["id"]].cell(start, column).get("value"), (marker,)) for column in range(1, table["cols"] + 1))):
                aliases[marker] = (marker,)
        return facts.rows(aliases, title=(table["id"],))

    def rule_3(self):
        for row in self.column_rows("模具设计尺寸"):
            if re.fullmatch("\\d+(?:\\.\\d+)?×\\d+(?:\\.\\d+)?×\\d+(?:\\.\\d+)?", str(row["模具设计尺寸"] or "").strip()) is None:
                return ("NG", "模具设计尺寸必须为数字×数字×数字。")
        return ("PASS", "全部模具设计尺寸均符合长×宽×高格式。")

    def rule_4(self):
        return self.visual_result(
            {"physical_photo": "实物照片列下有实际嵌入图片，文字占位和其他列图片不算；只核实图片存在，不认证照片内容是否正确。无法分辨图片与空白单元格时返回未知。"}, "实物照片列中的对应图片"
        )

    def rule_5(self):
        names = ("编制", "审核")
        for name in names:
            if key(self.native().field((name,))) in {key(label) for label in names}:
                raise UnknownFact("字段只读到相邻标签，尚不能证明有实际填写内容。")
        return ("PASS", "编制与审核字段均有实际内容。")

    def rule_6(self):
        for row in self.column_rows("实物确认"):
            if row["实物确认"] not in {"有", "无"}:
                return ("NG", "实物确认只允许有或无。")
        return ("PASS", "实物确认列的全部业务值均为有或无。")
