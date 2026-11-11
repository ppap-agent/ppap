"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, key
from audit.engine.checkers.date_utils import normalize_excel_date
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def rule_1(self):
        names = ("公司名称", "车型项目", "零件名称", "零件号", "制造地址", "PFMEA开始日期", "PFMEA修订日期", "跨职能团队")
        for name in names:
            if key(self.native().field((name,), title=("PFMEA",))) in {key(label) for label in names}:
                raise UnknownFact("字段只读到相邻标签，尚不能证明有实际填写内容。")
        return ("PASS", "PFMEA八个头部身份字段均有实际内容。")

    def rule_2(self):
        facts = self.native()
        initial = normalize_excel_date(facts.field(("PFMEA开始日期",), title=("PFMEA",)))
        revised = normalize_excel_date(facts.field(("PFMEA修订日期",), title=("PFMEA",)))
        rows = facts.rows({"date": ("修订日期",)}, title=("变更履历",))
        dates = [normalize_excel_date(row["date"]) for row in rows]
        if initial is None or revised is None or any((value is None for value in dates)):
            raise UnknownFact("PFMEA日期或变更履历日期无法可靠解析。")
        return ("PASS", "PFMEA日期与履历首末有效日期一致。") if initial == dates[0] and revised == dates[-1] else ("NG", "PFMEA开始/修订日期与履历首末日期不一致。")

    def rule_4(self):
        for row in self.native().rows({"function": ("过程项功能",)}, title=("PFMEA",)):
            if blank(row["function"]):
                raise UnknownFact("过程项功能尚未可靠读取。")
        return self.text_result({"dimensions": "PFMEA每个过程项功能都分别包含内部、客户工厂、最终用户三个维度的实际内容；只列维度名、示例或无法判断归属时不能判为满足。"}, "过程项功能的三个独立维度")
