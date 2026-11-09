"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from audit.checker_lib.source import MissingFact, SourceAudit, UnknownFact, blank
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def grouped_rows(self, group, names):
        facts = self.native()
        aliases = {"process": ("产线/工序",), "group": (group,), **{name: (name,) for name in names}}
        table = facts.table(("设备", "验收清单"))
        try:
            _start, columns = facts.columns(table, aliases)
        except MissingFact as exc:
            raise UnknownFact("当前读取器尚未完整确认设备开发/验收分组与叶列，需核对原件。") from exc
        if any(columns[name] <= columns["group"] for name in names):
            raise UnknownFact("设备分组和子列的排列归属尚不能唯一确认。")
        other_group = "设备验收" if group == "设备开发" else "设备开发"
        for column in range(columns["group"] + 1, table["cols"] + 1):
            value = facts.readers[table["id"]].cell(_start, column)
            if value.get("state") == "known" and str(value.get("value")).strip() == other_group and any(columns[name] >= column for name in names):
                raise UnknownFact("设备子列跨越另一分组，尚不能确认开发/验收归属。")
        return facts.rows(aliases, title=(table["id"],), anchor=("process",))

    def rule_1(self):
        names = ("设备", "供应商", "设备型号", "利旧设备数量", "新采购设备数量", "设备通用情况", "计划到位时间")
        for row in self.grouped_rows("设备开发", names):
            if any(blank(row[name]) for name in names):
                return "NG", "已有产线/工序的设备开发行存在空白必填内容。"
        return "PASS", "设备开发子列及全部适用行内容已读取。"

    def rule_2(self):
        for row in self.grouped_rows("设备验收", ("验收结果", "整改措施", "整改计划")):
            if blank(row["验收结果"]):
                return "NG", "设备验收结果为空。"
            if row["验收结果"] in {"不通过", "条件通过"} and (blank(row["整改措施"]) or blank(row["整改计划"])):
                return "NG", "设备验收不通过或条件通过，但整改措施或计划为空。"
        return "PASS", "全部设备验收行的结果及条件整改要求已读取。"
