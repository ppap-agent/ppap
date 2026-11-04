"""L06 validates finite Cpk values and the literal achievement decision."""

from audit.checkers.v3.excel_agent import rule
from audit.checkers.v3.initial_process_capacity_agent import InitialProcessCapacityAgent
from audit.engine.rule_engine import Table

from .facts import NativeFacts, compact, data_rows, number


class LiAutoCapacityStudyAgent(NativeFacts, InitialProcessCapacityAgent):
    EXPECTED_RULE_CATEGORIES = ("P阶段Cpk结果列审核", "P阶段结果判定列审核", "控制图及短期过程能力审核")

    @rule("P阶段Cpk结果列审核")
    def rule_1(self, ctx):
        for row in data_rows(ctx, "CCSC主表", "CCSC数据区", ("工序流程编号", "P阶段Cpk结果"), trigger="工序流程编号"):
            value = row.value("P阶段Cpk结果")
            if compact(value).upper() != "NA" and number(value) <= number("1.33"):
                return "NG", "P阶段Cpk须为NA或大于1.33的有限数值。"
        return "PASS", ""

    @rule("控制图及短期过程能力审核")
    def rule_3(self, ctx):
        table = Table(ctx)
        for chart in ("I-MR", "Xbar-R", "Xbar-S"):
            table.find_sheets(f"{chart}控制图")
            block = table.field_block(f"{chart}短期能力区")
            block.expect(f"{chart}短期过程能力", f"{chart}-Cpk", context=f"{chart}控制图")
            if number(block.value(f"{chart}-Cpk")) <= number("1.33"):
                return "NG", f"{chart}短期过程能力Cpk未大于1.33。"
        return "PASS", "三类控制图的短期Cpk均大于1.33。"
