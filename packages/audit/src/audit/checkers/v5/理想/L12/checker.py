"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, key
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def rule_1(self):
        names = ("零件名称", "零件号", "供应商名称", "供应商编码")
        for name in names:
            if key(self.native().field((name,), title=("CCSC", "特性管理清单"))) in {key(label) for label in names}:
                raise UnknownFact("字段只读到相邻标签，尚不能证明有实际填写内容。")
        return ("PASS", "CCSC四项身份字段均有实际原件内容。")

    def rule_3(self):
        rows = self.native().rows({"risk": ("现有管理方式风险评估",), "action": ("高风险管理项预防措施",)}, title=("CCSC", "特性管理清单"))
        for row in rows:
            if row["risk"] == "高风险" and blank(row["action"]):
                return ("NG", "高风险管理项缺少对应预防措施。")
        return ("PASS", "全部风险物理行已读取，高风险行均有预防措施。")
