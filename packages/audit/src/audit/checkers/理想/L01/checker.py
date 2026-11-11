"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, key
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def rule_1(self):
        self.native().require_title("全功能检验报告")
        return ("PASS", "已从原件标题位置核实全功能检验报告。")

    def rule_2(self):
        return self.visual_result({"importance_one": "重要度区分字段对应区域恰好有一个实际勾选，预印选项和空框不代表已选择。"}, "重要度区分的唯一实际选择")

    def rule_4(self):
        rows = self.native().rows({"method": ("供方检查方法/检查工具",)}, title=("全功能检验报告",))
        for row in rows:
            value = row["method"]
            if blank(value):
                raise UnknownFact("检查方法为空，尚不能形成方法选择结论。")
            if "/" in str(value) or "或" in str(value):
                return ("NG", "供方检查方法/检查工具包含斜线或“或”。")
        return ("PASS", "已完整读取方法列，各项没有斜线或“或”。")

    def rule_8(self):
        names = ("车型", "零件名称", "零件号", "供应商", "责任人", "日期")
        for name in names:
            if key(self.native().field((name,))) in {key(label) for label in names}:
                raise UnknownFact("字段只读到相邻标签，尚不能证明有实际填写内容。")
        return ("PASS", "车型、零件及责任日期字段均有原件内容。")
