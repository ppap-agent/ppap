"""L02 requires an actual PV hard-tool sample, not a vacuous non-PV pass."""

from audit.checkers.v3.dvp_r_excel_agent import DvpRExcelAgent
from audit.checkers.v3.excel_agent import rule
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.engine.rule_engine import Table

from .facts import NativeFacts


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
    PRECHECK_METHODS = {"L02:1:12bdf5c15f23163a60b3": "rule_1", "L02:2:87c488de4762850210ef": "rule_2"}

    @rule("PV试验样本状态一致性审核")
    def rule_3(self, ctx):
        rows = list(self._iter_main_rows(Table(ctx), ("序号", "试验类型", "状态")))
        if not rows:
            return "UNABLE", "没有可核实的试验样本行。"
        for row in rows:
            if not row.exact("试验类型", "PV", case_insensitive=True, compact=True) or not row.exact("状态", "硬模件", case_insensitive=False, compact=True):
                return "NG", "试验类型须为PV，且样本状态须为硬模件。"
        return "PASS", ""

    def _date_pair(self, ctx, start, end):
        rows = list(self._iter_main_rows(Table(ctx), ("序号", start, end)))
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

    def precheck(self):
        from .precheck_l1 import precheck_plan

        return precheck_plan(self)
