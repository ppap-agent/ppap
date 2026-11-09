"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, header, key
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
        facts = self.native()
        title = ("CCSC", "特性管理清单")
        table = facts.table(title)
        aliases = {"risk": ("现有管理方式风险评估",), "action": ("高风险管理项预防措施",)}
        start, _columns = facts.columns(table, aliases)
        anchor: tuple[str, ...] = ()
        if any(header(facts.readers[table["id"]].cell(start, column).get("value"), ("过程/流程步骤",)) for column in range(1, table["cols"] + 1)):
            aliases["step"] = ("过程/流程步骤",)
            anchor = ("step",)
        rows = facts.rows(aliases, title=title, anchor=anchor, defer_unknown=True)
        for row in rows:
            risk = row["risk"]
            if risk not in ("低风险", "中风险", "高风险"):
                raise UnknownFact("风险等级尚未确认为低风险、中风险或高风险，不能判定预防措施条件不适用。")
            if risk == "高风险" and blank(row["action"]):
                return ("NG", "高风险管理项缺少对应预防措施。")
        return ("PASS", "全部风险物理行已读取，高风险行均有预防措施。")
