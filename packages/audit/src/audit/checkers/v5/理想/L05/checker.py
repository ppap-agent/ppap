"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from audit.checker_lib.excel import column_letter
from audit.checker_lib.source import SourceAudit, UnknownFact, blank, key, number
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def rule_1(self):
        values = [self.native().field((name,)) for name in ("任务项一", "任务项二")]
        return ("PASS", "两个任务项均为OK。") if all(value == "OK" for value in values) else ("NG", "任务项一、任务项二必须全部为OK。")

    def rule_2(self):
        facts = self.native()
        table = facts.table(("测量系统分析计划",))
        start, columns = facts.columns(table, {"serial": ("序号", "NO", "NO.")})
        required = {}
        for column in range(1, table["cols"] + 1):
            value = facts.cell(table, start, column)
            if not blank(value) and "备注" not in str(value):
                required[column] = value
        if len(required) < 2:
            raise UnknownFact("MSA计划尚不能确认完整非备注字段范围。")
        checked = 0
        for row in range(start + 1, table["rows"] + 1):
            values = {column: facts.cell(table, row, column) for column in required}
            serial = values[columns["serial"]]
            if key(serial).rstrip(":") in {"编制", "审核", "批准", "备注"}:
                continue
            if all(blank(value) for value in values.values()) or all(key(value) == key(required[column]) for column, value in values.items()):
                continue
            checked += 1
            if any(blank(value) for value in values.values()):
                return "NG", "MSA计划业务行存在空白的非备注字段。"
        if not checked:
            raise UnknownFact("MSA计划没有可核实的业务数据行。")
        return "PASS", "MSA计划全部物理业务行的非备注字段均已填写。"

    def rule_4(self):
        self.native().table(("量具重复性和再现性",))
        return self.visual_result({"rr_accepted": "分析结论实际勾选了判定指标R&R＜10%；该量具可接受，预印文字和空框不能代替勾选。"}, "R&R可接受结论的实际勾选")

    def rule_3(self):
        facts = self.native()
        tables = [table for table in facts.tables(("量具重复性和再现性",)) if not any(word in key(table["id"]) for word in ("参考", "指南", "说明", "示例"))]
        if not tables:
            raise UnknownFact("尚无实际量具重复性和再现性报告；参考页或填表指南不作为测量记录。")
        ranges = (("A", 8, 10), ("B", 13, 15), ("C", 18, 20))
        for table in tables:
            if table["rows"] < 20 or table["cols"] < 12:
                raise UnknownFact("报告未形成可核对的A/B/C十样本三次测量范围。")
            if any(number(facts.cell(table, 7, column)) != column - 2 for column in range(3, 13)):
                raise UnknownFact("样本编号未能对应原表的1至10，不能猜测样本范围。")
            for evaluator, first, last in ranges:
                if key(facts.cell(table, first + 1, 1)) != key(evaluator) or any(number(facts.cell(table, row, 2)) != row - first + 1 for row in range(first, last + 1)):
                    raise UnknownFact("评价人或试验次数与原表范围不符，不能猜测测量区域。")
                if any(r1 <= last and r2 >= first and c1 <= 12 and c2 >= 3 for r1, c1, r2, c2 in table.get("merges", [])):
                    raise UnknownFact("测量格存在合并范围，尚不能确认90个独立样本格。")
                for row in range(first, last + 1):
                    for column in range(3, 13):
                        if blank(facts.cell(table, row, column)):
                            return "NG", f"{table['id']}评价人{evaluator}的{column_letter(column)}{row}为空；须有10个样本各3次的30格内容。"
        return "PASS", "每份实际报告的A/B/C均有10个样本各3次，90个独立测量格均有原件内容。"
