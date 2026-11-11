"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

import re

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, header
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def metadata(self, name):
        facts = self.native()
        try:
            return facts.field((name,))
        except UnknownFact:
            tables = facts.tables()
            if len(tables) != 1 or tables[0]["cols"] != 1:
                raise
            rows = facts.rows({"value": (name,)})
            values = {str(row["value"]).strip() for row in rows if not blank(row["value"])}
            if len(values) != 1:
                raise UnknownFact("单列元信息没有唯一可靠的内容。") from None
            return values.pop()

    def annual_rows(self, aliases):
        facts = self.native()
        table = facts.table()
        start, _columns = facts.columns(table, aliases)
        aliases = dict(aliases)
        for name in ("试验项目", "试验要求", "试验标准/方法", "样本数量"):
            if name not in aliases and any(header(facts.readers[table["id"]].cell(start, column).get("value"), (name,)) for column in range(1, table["cols"] + 1)):
                aliases[name] = (name,)
        return facts.rows(aliases, title=(table["id"],))

    def rule_1(self):
        value = self.metadata("质量等级")
        return ("PASS", "质量等级属于明确允许集合。") if value in {"关键件", "重要件", "一般件"} else ("NG", "质量等级必须为关键件、重要件或一般件。")

    def rule_2(self):
        for row in self.annual_rows({"item": ("试验项目",), "frequency": ("测试频率",)}):
            item = row["item"]
            if blank(item):
                raise UnknownFact("试验项目为空，不能决定适用频率。")
            required = {"每两年"} if str(item) in {"ELV试验", "禁限用", "EMC试验"} else {"每年", "每半年"}
            if row["frequency"] not in required:
                return "NG", "试验项目与同行测试频率不符合原规则。"
        return "PASS", "每个试验项目均已按实际项目类型核对测试频率。"

    def rule_3(self):
        value = self.metadata("供应商名称")
        return ("PASS", "供应商名称为代码-全称格式。") if re.fullmatch(r"[A-Za-z0-9]+-.+", value) else ("NG", "供应商名称必须为供应商代码-供应商全称。")

    def rule_4(self):
        for row in self.annual_rows({"quantity": ("样本数量",)}):
            value = row["quantity"]
            if re.search(r"\d", str(value if value is not None else "")) is None:
                return "NG", "样本数量必须包含阿拉伯数字；不增加原规则没有的正数门槛。"
        return "PASS", "每个样本数量均有原规则要求的数字。"

    def rule_5(self):
        aliases = {"item": ("试验项目",), "requirement": ("试验要求",), "standard": ("试验标准/方法",)}
        for row in self.annual_rows(aliases):
            if any(blank(row[name]) for name in aliases):
                return "NG", "试验要求栏存在空白的试验项目、要求或标准/方法。"
        return "PASS", "试验要求全部物理行的三项内容均完整。"

    def rule_6(self):
        self.metadata("车型")
        return "PASS", "已读取实际车型内容。"
