"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from decimal import Decimal

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, header, key, number
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def rule_1(self):
        names = ("零件名称", "零件号", "供应商名称", "供应商编码", "产能评估总体结论")
        for name in names:
            if key(self.native().field((name,))) in {key(label) for label in names}:
                raise UnknownFact("字段只读到相邻标签，尚不能证明有实际填写内容。")
        return ("PASS", "产能评估五个身份与总体结论字段均有原件内容。")

    def rule_2(self):
        facts = self.native()
        table = facts.table(("产能", "产能评估"))
        start, columns = facts.columns(table, {"line": ("线体/工序类别",)})
        aliases = {}
        for column in range(1, table["cols"] + 1):
            value = facts.cell(table, start, column)
            if not blank(value):
                aliases[str(value)] = (str(value),)
        if len(aliases) < 2:
            raise UnknownFact("产能数据表完整列范围尚未确认。")
        rows = facts.rows(aliases, title=(table["id"],), anchor=("线体/工序类别",))
        for row in rows:
            if any((blank(row[name]) for name in aliases)):
                return ("NG", "有线体/工序类别的业务行存在空白列内容。")
        return ("PASS", "每个有线体/工序类别的业务行全部已识别列均有内容。")

    def rule_3(self):
        labels = ("SOP+1达成风险", "SOP+2达成风险", "SOP+3达成风险")
        facts = self.native()
        table = facts.table()
        aliases = {name: (name,) for name in labels}
        start, _columns = facts.columns(table, aliases)
        if any(header(facts.readers[table["id"]].cell(start, column).get("value"), ("线体/工序类别",)) for column in range(1, table["cols"] + 1)):
            aliases["line"] = ("线体/工序类别",)
        rows = facts.rows(aliases, title=(table["id"],))
        risks = []
        for row in rows:
            for name in labels:
                value = row[name]
                risks.append(str(value).strip() if not blank(value) else "")
        if any(value not in {"无风险", "低风险", "中风险", "高风险"} for value in risks):
            raise UnknownFact("风险类别尚未唯一读为无风险、低风险、中风险或高风险；未知文字或空值不能当作低风险。")
        if any(value in {"中风险", "高风险"} for value in risks):
            conclusion = self.native().field(("产能评估总体结论",))
            action = self.native().field(("后续措施",), required=False)
            if not any((term in conclusion for term in ("条件通过", "未通过"))) or blank(action):
                return ("NG", "中高风险必须对应条件通过/未通过总体结论及非空后续措施。")
        return ("PASS", "风险各物理行与触发后的总体结论、措施已核对。")

    def rule_4(self):
        for row in self.native().rows({"yield": ("良率",), "qualified": ("合格品数",), "output": ("产出数量",)}):
            actual, qualified, output = (number(row[name]) for name in ("yield", "qualified", "output"))
            if output == 0:
                raise UnknownFact("产出数量为0，不能计算良率。")
            if abs(actual - qualified / output) > Decimal("1e-10"):
                return ("NG", "良率与合格品数除以产出数量不一致。")
        return ("PASS", "全部良率均与原件分子分母的计算结果一致。")

    def rule_5(self):
        aliases = {"jph": ("单线体/工序总产出JPH",), "output": ("产出数量",), "duration": ("净利用时间", "净利用时间（h）"), "lines": ("参与测试线体数量",)}
        for row in self.native().rows(aliases):
            actual, output, duration, lines = (number(row[name]) for name in aliases)
            if duration == 0 or lines == 0:
                raise UnknownFact("净利用时间或测试线体数量为0，不能计算JPH。")
            if abs(actual - output / duration / lines) > Decimal("1e-10"):
                return ("NG", "JPH与产出数量除以净利用时间及线体数量不一致。")
        return ("PASS", "全部JPH均与原件数值计算一致。")

    def rule_6(self):
        aliases = {
            "monthly": ("线体/工序预估月度产能",),
            "jph": ("单线体/工序总产出JPH",),
            "lines": ("现有线体数",),
            "shifts": ("班次/天",),
            "duration": ("每班净利用时间", "每班净利用时间（h）"),
            "days": ("每月标准工作天数",),
            "yield": ("累积良率",),
        }
        for row in self.native().rows(aliases):
            expected = Decimal(1)
            for name in ("jph", "lines", "shifts", "duration", "days", "yield"):
                expected *= number(row[name])
            if abs(number(row["monthly"]) - expected) > Decimal("1e-10"):
                return ("NG", "月度产能与原规则全部因子的乘积不一致。")
        return ("PASS", "全部月度产能均与原规则源数值乘积一致。")
