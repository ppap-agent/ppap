"""
### 测量系统分析评价评审规则

**规则 1: 任务项完成状态检查**
识别是否包含“任务项一”和“任务项二”字段，如果包含则继续识别“任务项一”、“任务项二”的内容是否全部为OK，如果是则通过，否则不通过。

**规则 2: R&R报告填写检查**
识别是否包含“测量系统分析计划”表，如果包含则继续识别；当序号/NO列下内容不为空，且对应行中除备注之外的所有列内容均不为空则通过，否则不通过

**规则 3: 样本数据完整性检查**
识别是否有表格的标题包含“量具重复性和再现性”，如果有则继续识别该表格下A、B、C都至少取10个样本，且90个单元格内容不为空

**规则 4: 分析结论勾选检查**
识别是否有表格的标题包含“量具重复性和再现性”，如果没有，该项判定为NG，否则继续识别该表格下的分析结论是否勾选“判定指标R&R＜10%；该量具可接受”，如果是则判定为PASS，否则判定为NG
"""

import logging
from typing import Tuple

from audit.engine.rule_engine import Cell, Decision, GuardStop, RuleContext, Table
from audit.kinds import PpapDocumentType

from .excel_agent import ExcelAgent, rule

logger = logging.getLogger(__name__)

_RULE2_REQUIRED_COLUMNS = (
    "零件名称",
    "零件号",
    "被测参数",
    "基准值",
    "公差",
    "量具名称",
    "量具编号",
    "测量周期",
    "测量人数",
    "产品数量",
    "测量次数/人",
    "计划日期",
    "实际测量日期",
)

_RULE3_EVALUATOR_RANGES: dict[str, tuple[int, int]] = {
    "A": (8, 10),
    "B": (13, 15),
    "C": (18, 20),
}

_RULE3_SAMPLE_COL_RANGE = range(3, 13)


class MsaReportAgent(ExcelAgent):
    """测量系统分析报告专业审核代理"""

    DOC_TYPE = PpapDocumentType.MSA_REPORT
    EXPECTED_RULE_CATEGORIES = (
        "任务项字段识别与状态审核",
        "测量系统分析计划表完整性审核",
        "量具重复性和再现性样本数量审核",
        "量具重复性和再现性结论判定审核",
    )

    def _get_rule_priority(self, rule_no: int) -> str:
        """
        获取MSA规则的优先级

        Args:
            rule_no: 规则编号（1-4）

        Returns:
            str: 优先级
        """
        # 规则1,3,4为P0级别，规则2为P1级别
        return "P0" if rule_no in [1, 3, 4] else "P1"

    @rule("任务项字段识别与状态审核")
    def rule_1(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则1: 检查"任务项一"和"任务项二"的状态
        - 首先检查是否存在这两个字段，如果不存在则判定为ERR
        - 如果存在，检查内容是否都为"OK"
        - 两项都为"OK"则PASS，否则NG
        """
        table = Table(ctx)
        if table.find_sheets("任务状态表", on_missing=Decision.IGNORE):
            task_block = table.field_block("任务汇总状态字段区")
            mission1_field = "汇总任务项一状态"
            mission2_field = "汇总任务项二状态"
        else:
            task_block = table.field_block("任务状态字段区")
            mission1_field = "任务项一状态"
            mission2_field = "任务项二状态"
        task_block.expect(mission1_field, mission2_field, context="任务状态字段区")

        if task_block.exact(mission1_field, "OK", case_insensitive=False, compact=True) and task_block.exact(
            mission2_field,
            "OK",
            case_insensitive=False,
            compact=True,
        ):
            return "PASS", ""
        mission1_status = task_block.value(mission1_field, compact=True)
        mission2_status = task_block.value(mission2_field, compact=True)
        return "NG", f"任务项状态不符合要求: 任务项一={mission1_status}, 任务项二={mission2_status}"

    @rule("测量系统分析计划表完整性审核")
    def rule_2(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则2: 检查"测量系统分析计划"的填写情况
        - "测量系统分析计划"表必须填写
        - 如果为空，则必须附上"报告空白页"
        """
        rr_sheets = Table(ctx).find_sheets("测量系统分析计划表")
        logger.info(f"测量系统分析计划表: {[sheet.title for sheet in rr_sheets]}")

        is_any_rr_sheet_filled = False
        plan_tables = []
        first_failure: GuardStop | None = None
        for fluent_sheet in rr_sheets:
            # A sheet matched only by its name (e.g. any "MSA" sheet) without the plan table is not the plan.
            try:
                section = fluent_sheet.section("计划主数据区")
                columns = section.columns("序号", *_RULE2_REQUIRED_COLUMNS, optional=("备注",))
            except GuardStop as failure:
                first_failure = first_failure or failure
                continue
            plan_tables.append((fluent_sheet, section, columns))
        if not plan_tables and first_failure is not None:
            raise first_failure

        for fluent_sheet, section, columns in plan_tables:
            logger.info(f"检查表: {fluent_sheet.title}")

            has_data_row = False
            for row in section.rows_from(columns):
                if not row.is_present("序号"):
                    continue
                has_data_row = True
                row.expect(*_RULE2_REQUIRED_COLUMNS, context="测量系统分析计划")

            if has_data_row:
                is_any_rr_sheet_filled = True

        if is_any_rr_sheet_filled:
            return "PASS", ""
        return "NG", "测量系统分析计划未正确填写"

    @rule("量具重复性和再现性样本数量审核")
    def rule_3(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则3: 检查R&R分析报告的数据完整性
        - 评价人A、B、C都必须至少采集10个样本
        - 确保报告中总共有90个数据单元格的内容不为空
        """
        rr_data_sheets = Table(ctx).find_sheets("R&R数据表")
        logger.info(f"R&R数据表: {[sheet.title for sheet in rr_data_sheets]}")
        # One R&R table must be complete on its own; two half-filled tables
        # do not add up to one complete study.
        shortfalls: list[str] = []
        for fluent_sheet in rr_data_sheets:
            evaluator_data = {"A": 0, "B": 0, "C": 0}
            for evaluator, (start_row, end_row) in _RULE3_EVALUATOR_RANGES.items():
                evaluator_data[evaluator] = len(
                    fluent_sheet.scan_non_empty(
                        row_min=start_row,
                        row_max=end_row,
                        col_min=min(_RULE3_SAMPLE_COL_RANGE),
                        col_max=max(_RULE3_SAMPLE_COL_RANGE),
                    )
                )
            total_data_cells = sum(evaluator_data.values())
            logger.info(f"{fluent_sheet.title} 评价人数据统计: {evaluator_data}, 总数据单元格数: {total_data_cells}")
            if evaluator_data["A"] >= 10 and evaluator_data["B"] >= 10 and evaluator_data["C"] >= 10 and total_data_cells >= 90:
                return "PASS", f"工作表{fluent_sheet.title} 评价人A、B、C样本及90个单元格均已填写"
            shortfalls.append(f"工作表{fluent_sheet.title}: 评价人A={evaluator_data['A']}个, 评价人B={evaluator_data['B']}个, 评价人C={evaluator_data['C']}个, 总数据单元格数={total_data_cells}个")
        return "NG", "数据完整性不符合要求: " + "；".join(shortfalls)

    @rule("量具重复性和再现性结论判定审核")
    def rule_4(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则4: 检查R&R分析的结论
        - 分析结论必须勾选"判定指标 R&R < 10%; 该量具可接受"
        """
        Table(ctx).find_sheets("R&R数据表")

        block = Table(ctx).field_block("R&R结论字段区")
        block.expect("R&R可接受结论", context="R&R结论字段区")

        conclusion = block.field("R&R可接受结论").resolved
        if conclusion is None or conclusion.label_col <= 1:
            return "NG", "R&R分析结论未提供相邻勾选位置"
        # The choice is adjacent to its label. A wider leftward search can
        # consume an unrelated R&R calculation instead of the actual marker.
        check_cell_value = Cell(ctx, conclusion.ws, conclusion.label_row, conclusion.label_col - 1).value(compact=True)
        logger.info(f"找到R&R结论勾选状态: {check_cell_value}")
        if any(marker in str(check_cell_value or "") for marker in {"√", "☑", "✔", "✓"}):
            return "PASS", ""
        return "NG", "R&R分析结论不符合要求"
