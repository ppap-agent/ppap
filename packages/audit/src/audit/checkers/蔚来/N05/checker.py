"""N05 owns its source-clause checks. Shared code only reads source facts."""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, key
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return ProcessFlowAudit(audit_file, rule, group_context, resource=RESOURCE)


class ProcessFlowAudit(SourceAudit):
    def rows(self, aliases, *, anchor=()):
        return self.native().rows(aliases, title=("过程流程图",), anchor=anchor)

    def rule_1(self):
        facts = self.native()
        facts.table(("过程流程图",))
        for aliases in (("零件号", "零件编号"), ("零件名称",), ("项目车型",), ("供应商名称",), ("代码",), ("编制日期",), ("修订日期",), ("版本",), ("团队",)):
            facts.field(aliases, title=("过程流程图",))
        return "PASS", "过程流程图的九项原件表头内容均已读取。"

    def characteristics(self, *, process_columns=False):
        aliases = {name: (name,) for name in ("产品特殊特性", "产品特性类别")}
        if process_columns:
            aliases.update({name: (name,) for name in ("过程特殊特性", "过程特性类别")})
        rows = self.rows(aliases)
        applicable = False
        for row in rows:
            if not blank(row["产品特性类别"]):
                applicable = True
                if blank(row["产品特殊特性"]):
                    return "NG", "产品特性类别已填写，但同行产品特殊特性为空。"
        return ("PASS", "全部已声明产品特性类别均有同行产品特殊特性。") if applicable else ("NA", "已读取业务行，没有已填写产品特性类别的适用行。")

    def rule_2(self):
        return self.characteristics()

    def rule_3(self):
        # The accepted clause asks for process columns, then explicitly states
        # a product-column condition. Do not silently rewrite that contract.
        return self.characteristics(process_columns=True)

    def rule_4(self):
        rows = self.rows({"name": ("工序名称",), "description": ("操作描述",)}, anchor=("name",))
        for row in rows:
            if blank(row["description"]):
                return "NG", "工序缺少操作描述：" + str(row["name"])
        return "PASS", "全部工序名称均有同行操作描述。"

    def rule_5(self):
        kinds = ("制造", "运输", "存储", "检验", "返工", "报废")
        rows = self.rows({"name": ("工序名称",), **{name: (name,) for name in kinds}}, anchor=("name",))
        for row in rows:
            if not any(not blank(row[name]) for name in kinds):
                if self.native().has_unread_content():
                    raise UnknownFact("工序图例仍可能在未读取图形中，不能用空文字证明缺少图例。")
                return "NG", "工序的六类图例均为空：" + str(row["name"])
        return "PASS", "全部工序均有至少一种实际图例内容。"

    def names(self):
        return self.rows({"name": ("工序名称",)}, anchor=("name",))

    def rule_6(self):
        names = self.names()
        if any("进料检验" in str(row["name"]) or "iqc" in key(row["name"]) for row in names):
            return "PASS", "原件工序名称包含进料检验或IQC。"
        return "NG", "工序名称缺少进料检验或IQC。"

    def rule_7(self):
        names = self.names()
        if any("出货" in str(row["name"]) or any(token in key(row["name"]) for token in ("fqc", "oqc")) for row in names):
            return "PASS", "原件工序名称包含出货、FQC或OQC。"
        return "NG", "工序名称缺少出货、FQC或OQC。"

    def rule_8(self):
        rows = self.names()
        applicable = False
        for index, row in enumerate(rows):
            name = str(row["name"])
            if "返工" not in name and "返修" not in name:
                continue
            applicable = True
            if index + 1 >= len(rows) or rows[index + 1].row != row.row + 1 or not any(term in str(rows[index + 1]["name"]) for term in ("检验", "重检")):
                return "NG", "返工或返修工序的下一物理行不是检验或重检：" + name
        return ("PASS", "全部返工或返修工序的下一物理行均为检验或重检。") if applicable else ("NA", "已读取全部工序，没有返工或返修适用行。")
