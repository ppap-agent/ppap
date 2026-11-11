"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from decimal import Decimal

from audit.checker_lib.source import SourceAudit, header, number
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def rows(self, aliases):
        facts = self.native()
        table = facts.table(("CCSC", "特性管理清单"))
        start, _columns = facts.columns(table, aliases)
        aliases = dict(aliases)
        for name in ("工序流程编号", "特性名称", "P阶段Cpk结果", "P阶段结果判定"):
            if name not in aliases and any(header(facts.readers[table["id"]].cell(start, column).get("value"), (name,)) for column in range(1, table["cols"] + 1)):
                aliases[name] = (name,)
        return facts.rows(aliases, title=(table["id"],))

    def rule_1(self):
        rows = self.rows({"cpk": ("P阶段Cpk结果",)})
        for row in rows:
            value = row["cpk"]
            if str(value).strip().upper() != "NA" and number(value) <= Decimal("1.33"):
                return "NG", "P阶段Cpk必须为NA或严格大于1.33的有限数值。"
        return "PASS", "全部读取的P阶段Cpk符合原规则。"

    def rule_2(self):
        for row in self.rows({"result": ("P阶段结果判定",)}):
            if row["result"] != "达标":
                return "NG", "P阶段结果判定必须全部严格为达标。"
        return "PASS", "全部P阶段结果判定为达标。"

    def rule_3(self):
        facts = self.native()
        for title in ("I-MR", "Xbar-R", "Xbar-S"):
            table = facts.table((title,))
            facts.field(("短期过程能力",), title=(table["id"],))
            if number(facts.field(("Cpk",), title=(table["id"],))) <= Decimal("1.33"):
                return "NG", title + "控制图的短期Cpk未严格大于1.33。"
        return "PASS", "三种指定控制图及短期Cpk已逐表读取。"
