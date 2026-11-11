"""N03 local control-plan predicates; no inherited customer business class."""

from __future__ import annotations

import re

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, key
from resources import RESOURCE

_CP = {"kind": ("特殊特性分类",), "product": ("产品",), "process": ("过程",), "number": ("零件/工序号",), "name": ("工序名称/操作描述",), "frequency": ("频率", "频次")}


def create_audit(audit_file, rule, group_context=None):
    return ControlPlanAudit(audit_file, rule, group_context, resource=RESOURCE)


class ControlPlanAudit(SourceAudit):
    def rows(self, aliases):
        return self.native().rows(aliases, title=("控制计划",))

    def stages(self):
        facts = self.native()
        table = facts.table(("控制计划",))
        if facts.has_unread_content():
            raise UnknownFact("控制计划类型仍有未读取图形，原生选项文字不能证明勾选状态。")
        stop = min(table["rows"], 40)
        for row in range(1, stop + 1):
            if any(key(facts.readers[table["id"]].cell(row, column).get("value")) in {key("零件/工序号"), key("工序名称/操作描述")} for column in range(1, table["cols"] + 1)):
                stop = row - 1
                break
        selected = set()
        for row in range(1, stop + 1):
            for column in range(1, table["cols"] + 1):
                item = facts.readers[table["id"]].cell(row, column)
                if item.get("state") != "known":
                    facts.cell(table, row, column)
                text = str(item.get("value") or "")
                if not any(mark in text for mark in "■☑✓√✔☒▄"):
                    continue
                facts.cell(table, row, column)
                stages = re.findall(r"[■☑✓√✔☒▄]\s*(试生产|样件|SOP|生产)", text, flags=re.I)
                if not stages:
                    raise UnknownFact("类型标记与选项标签未能唯一对应，不能借用相邻选项的勾选。")
                selected.update(key(stage) for stage in stages)
        return selected

    def rule_1(self):
        stages = self.stages()
        return ("PASS", "控制计划原生勾选明确包含SOP或生产。") if stages & {"sop", "生产"} else ("NG", "未确认勾选SOP或生产。")

    def rule_2(self):
        rows = self.rows({name: _CP[name] for name in ("kind", "product", "process")})
        applicable = False
        for row in rows:
            if not blank(row["kind"]):
                applicable = True
                if blank(row["product"]) and blank(row["process"]):
                    return "NG", "特殊特性分类已填写，但同行产品与过程均为空。"
        return ("PASS", "全部已分类的特殊特性均有同行产品或过程内容。") if applicable else ("NA", "已读取数据行，没有非空特殊特性分类适用行。")

    def rule_3(self):
        rows = self.rows({"kind": _CP["kind"], "control": ("控制方法",)})
        applicable = False
        for row in rows:
            if key(row["kind"]) in {"关键特殊特性", "重要特殊特性", "[sc]", "[cc]"}:
                applicable = True
                if blank(row["control"]):
                    return "NG", "关键或重要特殊特性缺少同行控制方法。"
        return ("PASS", "全部适用特殊特性均有同行控制方法。") if applicable else ("NA", "没有本条指定类别的适用特性。")

    def rule_4(self):
        rows = self.rows({name: _CP[name] for name in ("number", "name", "frequency")})
        count = 0
        for row in rows:
            if blank(row["number"]) and blank(row["name"]):
                continue
            if key(row["name"]).startswith(("编制/日期", "审核/日期", "编制人", "批准人")):
                continue
            count += 1
            if blank(row["frequency"]):
                return "NG", "工序未填写频率或频次：" + str(row["name"] or row["number"])
        if not count:
            raise UnknownFact("没有可核实工序行，不能按空表通过。")
        return "PASS", "全部实际工序行的频率或频次均已读取。"

    def rule_5(self):
        labels = (
            ("控制计划编号",),
            ("联系人contact/电话phone", "联系人/电话"),
            ("日期date（编制compi.）", "日期（编制）"),
            ("日期date（修订update）", "日期（修订）"),
            ("版本Rev.", "版本"),
            ("页数",),
            ("页码",),
            ("零件号",),
            ("零件名称",),
            ("多功能小组",),
            ("公司",),
            ("公司代码",),
            ("公司批准/日期",),
            ("其它批准/日期（如需要）",),
            ("顾客工程批准/日期(如需要）",),
            ("顾客质量批准/日期(如需要)",),
        )
        for aliases in labels:
            self.native().field(aliases, title=("控制计划",))
        return ("PASS", "十六项表头内容与唯一实际控制计划类型已核实。") if len(self.stages()) == 1 else ("NG", "控制计划类型必须恰好勾选一项。")
