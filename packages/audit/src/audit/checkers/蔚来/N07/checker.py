"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit, UnknownFact, key
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def field(self, aliases):
        value = self.native().field(aliases)
        labels = ("文件编号", "报告编号", "编号", "实验判定", "试验判定", "判定", "试验结论", "实验结论", "结论", "试验类型", "样品数量", "样本数量")
        if key(value) in {key(label) for label in labels}:
            raise UnknownFact("报告字段只读到相邻打印标签，尚未取得实际填写内容。")
        return value

    def rule_1(self):
        self.field(("文件编号", "报告编号", "编号"))
        return "PASS", "已读取报告自己的非空编号。"

    def rule_2(self):
        value = self.field(("实验判定", "试验判定", "判定", "试验结论", "实验结论", "结论"))
        return ("PASS", "结论严格匹配原规则文字。") if value in {"符合", "通过", "合格", "P", "pass"} else ("NG", "结论不是原规则明确允许的文字；不翻译、不接受同义词。")

    def rule_3(self):
        value = self.field(("试验类型",))
        return ("PASS", "试验类型为DV或PV。") if value.casefold() in {"dv", "pv"} else ("NG", "试验类型必须为DV或PV。")

    def rule_4(self):
        return self.visual_result({"seal": "本报告存在实际印章；盖章文字标签、公司标志、图标或红色方块不算。"}, "报告实际印章", existential_fields=("seal",))

    def rule_5(self):
        return self.visual_result(
            {
                "before": "试验前区域有实际样品或现场照片；示意图、纯色和文字占位不算。",
                "during": "实验中或试验中区域有实际试验过程照片；示意图、纯色和文字占位不算。",
                "after": "试验后区域有实际样品或试验结果照片；示意图、纯色和文字占位不算。",
            },
            "试验前、中、后的对应实际照片",
        )

    def rule_6(self):
        self.field(("样品数量", "样本数量"))
        return "PASS", "已读取原报告的样品/样本数量内容。"
