"""N01 source measurements: literal completeness and deterministic thresholds."""

from __future__ import annotations

from decimal import Decimal

from audit.checker_lib.source import SourceAudit, blank, key, number
from resources import RESOURCE

_DIRECTIONS = {"nd", "gap", "flush", "nd-", "gap-", "flush-"}
_MEASUREMENT = ("测量报告", "Hole&Slot", "Surface", "Trim", "SubDatum", "Gap&Flush")


def create_audit(audit_file, rule, group_context=None):
    return MeasurementAudit(audit_file, rule, group_context, resource=RESOURCE)


class MeasurementAudit(SourceAudit):
    def rows(self, aliases):
        # NativeFacts refuses multiple business candidates. Do not take the
        # first worksheet and claim that the complete report has passed.
        return self.native().rows(aliases, title=_MEASUREMENT)

    def rule_1(self):
        facts = self.native()
        facts.table(("CoverSheet",))
        for aliases in (("零件号",), ("零件名", "零件名称"), ("项目",), ("项目阶段",), ("日期",)):
            facts.field(aliases, title=("CoverSheet",))
        return "PASS", "唯一CoverSheet的五项原件字段及对应非空内容均已读取。"

    def rule_2(self):
        names = ("测点号", "坐标X", "坐标Y", "坐标Z", "上公差", "下公差", "主要控制方向")
        rows = self.rows({name: (name,) for name in names})
        for row in rows:
            if any(blank(row[name]) for name in names):
                return "NG", "测点业务行缺少测点号、坐标、公差或主要控制方向。"
        return "PASS", "全部可确认测点行的坐标、公差及主要控制方向均非空。"

    def rule_3(self):
        rows = self.rows({"direction": ("主要控制方向",), "upper": ("上公差",), "lower": ("下公差",)})
        applicable = False
        for row in rows:
            if key(row["direction"]) not in _DIRECTIONS:
                continue
            applicable = True
            if any(key(row[name]) != "na" for name in ("upper", "lower")):
                return "NG", "指定控制方向的上下公差必须同时为NA。"
        return ("PASS", "全部指定控制方向的上下公差均为NA。") if applicable else ("NA", "已读取测点范围，本条没有指定方向适用行。")

    def rule_5(self):
        rows = self.rows({"point": ("测点号",), "kind": ("类型",)})
        for row in rows:
            if key(row["kind"]) not in {"cc", "sc", "ac"}:
                return "NG", "测点类型必须为CC、SC或AC。"
        return "PASS", "全部测点类型均为CC、SC或AC。"

    def threshold(self, column):
        warnings = []
        for row in self.rows({"point": ("测点号",), "value": (column,)}):
            actual = abs(number(row["value"]))
            if actual > Decimal("0.66"):
                return "NG", f"{column}第{row.row}行绝对值大于0.66。"
            if actual > Decimal("0.33"):
                warnings.append(str(row.row))
        message = column + "全部已读取值的绝对值不大于0.66。"
        if warnings:
            message += "需关注大于0.33的行：" + "、".join(warnings)
        return "PASS", message

    def rule_9(self):
        return self.threshold("Centering")

    def rule_11(self):
        return self.threshold("Range")
