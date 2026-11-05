"""L02 requires an actual PV hard-tool sample, not a vacuous non-PV pass."""

from audit.checkers.v3.dvp_r_excel_agent import DvpRExcelAgent
from audit.checkers.v3.excel_agent import rule
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.engine.rule_engine import Decision, GuardStop, Row, Table

from .facts import NativeFacts, compact


class LiAutoValidationPlanAgent(NativeFacts, DvpRExcelAgent):
    EXPECTED_RULE_CATEGORIES = (
        "基础信息字段完整性审核",
        "试验计划列完整性审核",
        "PV试验样本状态一致性审核",
        "计划时间逻辑审核",
        "实际试验时间逻辑审核",
        "试验结果有效性审核",
        "试验报告编号完整性审核",
    )
    PRECHECK_METHODS = {"L02:1": "rule_1", "L02:2": "rule_2"}

    def _trial_rows(self, ctx, required):
        result = []
        for sheet in Table(ctx).find_sheets("DVP&R工作表"):
            section = sheet.section("DVP&R主数据区")
            optional = tuple(name for name in ctx.resource.columns if name not in required)
            columns = section.columns(*required, optional=optional)
            left, right = min(column.header_col for column in columns.values()), max(column.header_col for column in columns.values())
            aliases = {compact(alias).casefold() for name in columns for alias in ctx.resource.columns[name].aliases}
            for index in range(max(column.data_start_row for column in columns.values()), sheet.ws.max_row + 1):
                if any(
                    merged.min_row <= index <= merged.max_row and merged.min_col <= left and merged.max_col >= right and merged.min_col != merged.max_col for merged in sheet.ws.merged_cells.ranges
                ):
                    continue
                raw = [compact(sheet.ws.cell(index, column.header_col).value) for column in columns.values()]
                filled = [value for value in raw if value]
                if not filled or len(filled) >= 2 and all(value.casefold() in aliases for value in filled):
                    continue
                result.append(Row(ctx, sheet.ws, columns, index, section.resolved))
        ctx.note_scan(yielded=len(result), finished=True)
        if not result:
            raise GuardStop(Decision.UNABLE, "没有可核实的试验业务行。")
        return result

    @rule("试验计划列完整性审核")
    def rule_2(self, ctx):
        required = ("序号", "试验项目", "试验要求", "试验标准方法", "试验类型", "完成节点", "试验地点", "数量", "状态", "外观属性", "计划开始", "计划结束")
        for row in self._trial_rows(ctx, required):
            row.expect(*required, context="非整行合并的试验计划业务行")
        return "PASS", "试验计划列及各业务行必填内容已核实。"

    @rule("PV试验样本状态一致性审核")
    def rule_3(self, ctx):
        rows = self._trial_rows(ctx, ("试验类型", "状态"))
        if not rows:
            return "UNABLE", "没有可核实的试验样本行。"
        for row in rows:
            if not row.exact("试验类型", "PV", case_insensitive=True, compact=True) or not row.exact("状态", "硬模件", case_insensitive=False, compact=True):
                return "NG", "试验类型须为PV，且样本状态须为硬模件。"
        return "PASS", ""

    def _date_pair(self, ctx, start, end):
        rows = self._trial_rows(ctx, (start, end))
        if not rows:
            return "UNABLE", "没有可核实的日期数据行。"
        for row in rows:
            left, right = row.value(start), row.value(end)
            if left is None or right is None or not str(left).strip() or not str(right).strip():
                return "NG", "试验开始/结束日期未填写。"
            first, last = normalize_excel_date(left), normalize_excel_date(right)
            if first is None or last is None:
                return "UNABLE", "试验日期尚不能可靠解析。"
            if first >= last:
                return "NG", "试验开始日期须早于结束日期。"
        return "PASS", ""

    @rule("计划时间逻辑审核")
    def rule_4(self, ctx):
        return self._date_pair(ctx, "计划开始", "计划结束")

    @rule("实际试验时间逻辑审核")
    def rule_5(self, ctx):
        return self._date_pair(ctx, "试验实际开始日期", "试验实际结束日期")

    @rule("试验结果有效性审核")
    def rule_6(self, ctx):
        for row in self._trial_rows(ctx, ("实际试验结果",)):
            row.expect_condition("实际试验结果", lambda value: compact(value) in {"合格", "视同", "偏差认可"}, context="实际试验结果须为合格、视同或偏差认可")
        return "PASS", "试验结果已逐行核实。"

    @rule("试验报告编号完整性审核")
    def rule_7(self, ctx):
        for row in self._trial_rows(ctx, ("试验报告编号",)):
            row.expect("试验报告编号", context="每个非整行合并试验业务行的报告编号")
        return "PASS", "试验报告编号已逐行核实。"

    def precheck(self):
        from .precheck_l1 import precheck_plan

        return precheck_plan(self)
