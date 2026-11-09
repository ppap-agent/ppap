"""Source-owned UAES checks; original rules and explicit evidence decide results."""

from __future__ import annotations

from audit.checker_lib.source import MissingFact, SourceAudit, UnknownFact, blank, header, key
from resources import RESOURCE

_ALIASES = {
    "编号": ("编号", "序号"),
    "特性名称": ("产品特性", "过程特性", "特殊特性名称"),
    "特性值": ("特性及公差", "特性值"),
    "特性类别": ("特性类型", "特性类别"),
}


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def identity(self, name):
        facts = self.native()
        try:
            return facts.field((name,))
        except UnknownFact:
            for table in facts.tables():
                for row in range(1, min(10, table["rows"]) + 1):
                    for column in range(1, min(24, table["cols"])):
                        item = facts.readers[table["id"]].cell(row, column)
                        if item.get("state") == "known" and header(item.get("value"), (name,)):
                            merge = next((span for span in table.get("merges", []) if span[0] <= row <= span[2] and span[1] <= column <= span[3]), None)
                            next_column = merge[3] + 1 if merge else column + 1
                            if next_column <= table["cols"] and blank(facts.cell(table, row, next_column)):
                                raise MissingFact(name + "对应内容为空。") from None
            raise

    def rule_1(self):
        for name in ("物料名称", "物料编号"):
            self.identity(name)
        return ("PASS", "已从原件核实物料名称与编号实际内容。")

    def rule_2(self):
        rows = self.native().rows(_ALIASES, title=("特殊特性", "特性清单"))
        count = 0
        for row in rows:
            number = row["编号"]
            if any((key(number).startswith(marker) for marker in ("编制/日期", "审核/日期", "编制人", "批准人"))):
                continue
            if blank(number):
                return ("NG", "特性业务行编号为空。")
            count += 1
            for name in ("特性名称", "特性值", "特性类别"):
                if blank(row[name]):
                    return ("NG", f"已编号特性行{name}为空。")
        if not count:
            raise UnknownFact("没有可核实特性业务行，不能按空范围通过。")
        return ("PASS", "已读取全部物理业务行的编号、特性名称、值和类别。")
