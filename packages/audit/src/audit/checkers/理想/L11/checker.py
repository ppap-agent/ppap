"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, key
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def rule_1(self):
        names = ("零件号", "零件名称", "项目车型", "供应商名称", "代码", "编制日期", "版本", "团队")
        for name in names:
            if key(self.native().field((name,), title=("过程流程图",))) in {key(label) for label in names}:
                raise UnknownFact("字段只读到相邻标签，尚不能证明有实际填写内容。")
        return ("PASS", "过程流程图八项身份字段均有实际内容。")

    def rule_4(self):
        for row in self.native().rows({"operation": ("工序名称",), "description": ("操作描述",)}, title=("过程流程图",), anchor=("operation",)):
            if blank(row["description"]):
                return ("NG", "已命名工序对应的操作描述为空。")
        return ("PASS", "全部已命名工序都有对应操作描述。")

    def flow_rows(self):
        labels = ("制造", "运输", "存储", "检验", "返工", "报废")
        return self.native().rows({"operation": ("工序名称",), **{name: (name,) for name in labels}}, title=("过程流程图",), anchor=("operation",))

    def rule_5(self):
        rows = self.flow_rows()
        if self.native().has_unread_content():
            return self.visual_result({"operation_shape": "每个有工序名称的业务行，制造、运输、存储、检验、返工、报废六列中至少一列有实际符号或内容。"}, "每个工序的活动类型实际标记")
        for row in rows:
            if all((blank(row[name]) for name in ("制造", "运输", "存储", "检验", "返工", "报废"))):
                return ("NG", "工序名称存在，但六类活动列全部为空。")
        return ("PASS", "每个已命名工序至少有一个活动列内容。")

    def rule_6(self):
        self.flow_rows()
        return self.visual_result(
            {
                "operation_shapes": "每个有工序名称的业务行，在制造、运输、存储、检验、返工、报废六列中至少有一个实际标记。",
                "connections": "上述各业务行有内容的实际活动区域之间存在对应连线，不能用邻列边框或表格网格线代替流程连线。",
            },
            "工序活动区域及实际连接线",
        )
