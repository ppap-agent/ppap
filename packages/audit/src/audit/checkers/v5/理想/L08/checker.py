"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit, blank, header
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def rows(self, extra):
        facts = self.native()
        table = facts.table(("控制计划",))
        start, _columns = facts.columns(table, extra)
        aliases = dict(extra)
        for name in ("工序名称", "特殊特性分类", "控制方法", "频率", "频次"):
            if name not in aliases and any(header(facts.readers[table["id"]].cell(start, column).get("value"), (name,)) for column in range(1, table["cols"] + 1)):
                aliases[name] = (name,)
        return facts.rows(aliases, title=(table["id"],))

    def rule_1(self):
        return self.visual_result({"production_selected": "控制计划的SOP或生产选项被实际勾选；只有预印文字、空框、样件或试生产选择时不满足。"}, "控制计划实际选择SOP或生产")

    def rule_2(self):
        for row in self.rows({"mark": ("特殊特性分类",)}):
            if row["mark"] not in {"关键特殊特性", "重要特殊特性", "NA", "[G]", "[Z]"}:
                return "NG", "特殊特性分类不属于原规则明确允许的集合。"
        return "PASS", "特殊特性分类逐行严格匹配允许集合。"

    def rule_4(self):
        for row in self.rows({"mark": ("特殊特性分类",), "control": ("控制方法",)}):
            if row["mark"] in {"关键特殊特性", "重要特殊特性", "[G]", "[Z]"} and blank(row["control"]):
                return "NG", "关键或重要特殊特性行的控制方法为空。"
        return "PASS", "全部适用特殊特性行均有控制方法。"

    def rule_5(self):
        for row in self.rows({"frequency": ("频率", "频次")}):
            if blank(row["frequency"]):
                return "NG", "控制计划业务行的频率/频次为空。"
        return "PASS", "全部读取的控制计划业务行均有频率/频次。"
