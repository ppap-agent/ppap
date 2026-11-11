"""
### 生产系统产能评估报告评审规则
1.识别零件产能评估表中是否包含“零件名称”、“零件号”、“供应商名称”、“供应商编码”、“产能评估总体结论”字段，如果不包含则不通过，否则继续识别；
当零件产能评估表中的“零件名称”、“零件号”、“供应商名称”、“供应商编码”、“产能评估总体结论”字段对应内容均不为空则通过，否则不通过

2.当“线体/工序类别”列下内容不为空，且对应行的所有列均不为空则通过，否则不通过

3.当“SOP+1达成风险”、“SOP+2达成风险”、“SOP+3达成风险”列下内容存在“中风险”或“高风险”，且“产能评估总体结论”对应内容包含“条件通过”或“未通过”，且“后续措施”对应内容不为空则通过，否则不通过

4.识别“良率”、“合格品数”、“产出数量”三列，如果三列都存在则继续识别；当每一行的“良率”=“合格品数”÷“产出数量”则通过，否则不通过

5.识别“单线体/工序总产出JPH”、“产出数量”、“净利用时间”、“参与测试线体数量”四列，如果四列都存在则继续识别；
当每一行的“单线体/工序总产出JPH”=“产出数量”÷“净利用时间”÷“参与测试线体数量”则通过，否则不通过

6.识别“线体/工序预估月度产能”、“单线体/工序总产出JPH”、“现有线体数”、“班次/天”、“每班净利用时间”、“每月标准工作天数”、“累积良率”七列，如果七列都存在则继续识别；
计算值有小数时，“线体/工序预估月度产能”在向下取整值和向上取整值之间则通过；计算值为整数时必须等于计算值。
"""

import logging
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Tuple

from audit.engine.rule_engine import GuardStop, RuleContext, Table
from audit.kinds import PpapDocumentType

from .excel_agent import ExcelAgent, rule

logger = logging.getLogger(__name__)


class PlantCapacityEvaluationAgent(ExcelAgent):
    """生产系统产能评估报告专业审核代理"""

    DOC_TYPE = PpapDocumentType.PLANT_CAPACITY_EVALUATION_REPORT
    EXPECTED_RULE_CATEGORIES = (
        "基础字段完整性与内容校验",
        "线体工序数据完整性校验",
        "风险评估与措施一致性校验",
        "良率计算逻辑校验",
        "单线体总产出JPH计算逻辑校验",
        "预估月度产能计算逻辑校验",
    )

    def _is_value_numeric(self, value: object) -> bool:
        if isinstance(value, str) and value.strip() == "":
            return False
        try:
            float(str(value))
            return True
        except (TypeError, ValueError):
            return False

    def _is_ocr_table_sheet(self, sheet_name: str) -> bool:
        return str(sheet_name or "").lower().startswith("table_")

    def _row_label(self, row: Any, *column_names: str) -> str:
        for column_name in column_names:
            anchor = row.anchor(column_name, strict=False)
            if anchor is not None:
                return str(anchor.row_index)
        return "未知"

    @rule("基础字段完整性与内容校验")
    def rule_1(self, ctx: RuleContext) -> Tuple[str, str]:
        """规则 1: 关键字段完整性检查
        识别零件产能评估表中是否包含“零件名称”、“零件号”、“供应商名称”、“供应商编码”、“产能评估总体结论”字段，如果不包含则不通过，否则继续识别；
        当零件产能评估表中的“零件名称”、“零件号”、“供应商名称”、“供应商编码”、“产能评估总体结论”字段对应内容均不为空则通过，否则不通过
        """
        header = Table(ctx).field_block("头部字段区")
        required_fields = (
            "零件名称",
            "零件号",
            "供应商名称",
            "供应商编码",
            "产能评估总体结论",
        )

        header.expect(*required_fields, context="产能评估表")
        return "PASS", ""

    @rule("线体工序数据完整性校验")
    def rule_2(self, ctx: RuleContext) -> Tuple[str, str]:
        """规则 2: 线体/工序类别完整性检查
        当“线体/工序类别”列下内容不为空，且对应行的所有列均不为空则通过，否则不通过
        """
        section = Table(ctx).sheet("主表").section("产能数据区")
        required_columns = (
            "线体/工序类别",
            "参与测试线体数量",
            "测试总时间（h）",
            "净利用时间（h）",
            "产出数量",
            "合格品数",
            "良率",
            "单线体/工序总产出JPH",
            "现有线体数",
            "班次/天",
            "小时/班（h）",
            "每班净利用时间（h）",
            "每月标准工作天数",
            "累积良率",
            "线体/工序预估月度产能",
            "SOP+1量纲",
            "SOP+1达成风险",
            "SOP+2预估产能",
            "SOP+2量纲",
            "SOP+2达成风险",
            "SOP+3预估产能",
            "SOP+3量纲",
            "SOP+3达成风险",
        )
        columns = section.columns(*required_columns)

        for row in section.rows_from(columns):
            row.expect(*required_columns, context="线体/工序类别")
        return "PASS", ""

    @rule("风险评估与措施一致性校验")
    def rule_3(self, ctx: RuleContext) -> Tuple[str, str]:
        """规则 3: SOP达成风险与产能评估总体结论一致性检查
        当“SOP+1达成风险”、“SOP+2达成风险”、“SOP+3达成风险”列下内容存在“中风险”或“高风险”，且“产能评估总体结论”对应内容包含“条件通过”或“未通过”，且“后续措施”对应内容不为空则通过，否则不通过
        """
        table = Table(ctx)
        section = table.sheet("主表").section("产能数据区")
        risk_columns = section.columns(
            "SOP+1达成风险",
            "SOP+2达成风险",
            "SOP+3达成风险",
        )

        header = table.field_block("头部字段区")
        header.expect("产能评估总体结论", "后续措施", context="产能评估头部字段区")

        conclusion_value = header.value("产能评估总体结论", compact=True).lower()
        has_valid_conclusion = ("条件通过" in conclusion_value) or ("未通过" in conclusion_value)

        for row in section.rows_from(risk_columns):
            risk_values = {
                row.value("SOP+1达成风险", compact=True).lower(),
                row.value("SOP+2达成风险", compact=True).lower(),
                row.value("SOP+3达成风险", compact=True).lower(),
            }
            if risk_values.intersection({"中风险", "高风险"}):
                if not has_valid_conclusion:
                    return (
                        "NG",
                        f"第{self._row_label(row, 'SOP+1达成风险', 'SOP+2达成风险', 'SOP+3达成风险')}行存在中/高风险，但“产能评估总体结论”未体现“条件通过”或“未通过”。",
                    )
                header.expect("后续措施", context="中高风险后续措施")

        return "PASS", ""

    @rule("良率计算逻辑校验")
    def rule_4(self, ctx: RuleContext) -> Tuple[str, str]:
        """规则 4: 良率计算检查
        识别“良率”、“合格品数”、“产出数量”三列，如果三列都存在则继续识别；当每一行的“良率”=“合格品数”÷“产出数量”则通过，否则不通过
        """
        try:
            unreadable_rows: list[str] = []
            section = Table(ctx).sheet("主表").section("产能数据区")
            columns = section.columns("良率", "合格品数", "产出数量")
            is_ocr_table = self._is_ocr_table_sheet(section.sheet_title)

            for row in section.rows_from(columns):
                yield_value = row.value("良率", compact=True).lower()
                qualified_qty_value = row.value("合格品数", compact=True).lower()
                output_qty_value = row.value("产出数量", compact=True).lower()

                if self._is_value_numeric(yield_value) and self._is_value_numeric(qualified_qty_value) and self._is_value_numeric(output_qty_value):
                    yield_num = float(yield_value)
                    qualified_qty_num = float(qualified_qty_value)
                    output_qty_num = float(output_qty_value)
                    if output_qty_num == 0:
                        return (
                            "NG",
                            f"第{self._row_label(row, '产出数量')}行产出数量为0，无法计算良率。",
                        )
                    calculated_yield = qualified_qty_num / output_qty_num
                    if abs(calculated_yield - yield_num) > 0.0001:
                        return (
                            "NG",
                            f"第{self._row_label(row, '良率')}行良率与“合格品数÷产出数量”不一致。",
                        )
                else:
                    if yield_value in {"", "none", "nan"} and qualified_qty_value in {"", "none", "nan"} and output_qty_value in {"", "none", "nan"}:
                        continue
                    if is_ocr_table:
                        # A converted PDF table may have misread this row; it cannot pass unseen.
                        unreadable_rows.append(self._row_label(row, *columns))
                        continue
                    return (
                        "NG",
                        f"第{self._row_label(row, '良率', '合格品数', '产出数量')}行良率、合格品数或产出数量存在无效值。",
                    )
            if unreadable_rows:
                return "UNABLE", f"PDF识别表第{'、'.join(unreadable_rows[:8])}行数值无法识别，不能核对计算。"
            return "PASS", ""
        except GuardStop:
            raise
        except Exception as exc:
            logger.error(f"良率计算检查异常: {exc}")
            return "NG", "良率计算检查发生异常，请检查表格数据格式。"

    @rule("单线体总产出JPH计算逻辑校验")
    def rule_5(self, ctx: RuleContext) -> Tuple[str, str]:
        """规则 5: 单线体/工序总产出JPH计算检查
        识别“单线体/工序总产出JPH”、“产出数量”、“净利用时间”、“参与测试线体数量”四列，如果四列都存在则继续识别；
        当每一行的“单线体/工序总产出JPH”=“产出数量”÷“净利用时间”÷“参与测试线体数量”则通过，否则不通过
        """
        try:
            unreadable_rows: list[str] = []
            section = Table(ctx).sheet("主表").section("产能数据区")
            is_ocr_table = self._is_ocr_table_sheet(section.sheet_title)
            columns = section.columns(
                "单线体/工序总产出JPH",
                "产出数量",
                "净利用时间（h）",
                "参与测试线体数量",
            )

            for row in section.rows_from(columns):
                jph_value = row.value("单线体/工序总产出JPH", compact=True).lower()
                output_qty_value = row.value("产出数量", compact=True).lower()
                net_util_time_value = row.value("净利用时间（h）", compact=True).lower()
                test_line_qty_value = row.value("参与测试线体数量", compact=True).lower()

                if self._is_value_numeric(jph_value) and self._is_value_numeric(output_qty_value) and self._is_value_numeric(net_util_time_value) and self._is_value_numeric(test_line_qty_value):
                    jph_num = float(jph_value)
                    output_qty_num = float(output_qty_value)
                    net_util_time_num = float(net_util_time_value)
                    test_line_qty_num = float(test_line_qty_value)
                    if net_util_time_num == 0 or test_line_qty_num == 0:
                        return (
                            "NG",
                            f"第{self._row_label(row, '净利用时间（h）', '参与测试线体数量')}行净利用时间或参与测试线体数量为0，无法计算JPH。",
                        )
                    calculated_jph = output_qty_num / net_util_time_num / test_line_qty_num
                    if abs(calculated_jph - jph_num) > 0.0001:
                        return (
                            "NG",
                            f"第{self._row_label(row, '单线体/工序总产出JPH')}行JPH与计算结果不一致。",
                        )
                else:
                    if jph_value in {"", "none", "nan"} and output_qty_value in {"", "none", "nan"} and net_util_time_value in {"", "none", "nan"} and test_line_qty_value in {"", "none", "nan"}:
                        continue
                    if is_ocr_table:
                        # A converted PDF table may have misread this row; it cannot pass unseen.
                        unreadable_rows.append(self._row_label(row, *columns))
                        continue
                    return (
                        "NG",
                        f"第{self._row_label(row, '单线体/工序总产出JPH', '产出数量', '净利用时间（h）', '参与测试线体数量')}行JPH、产出数量、净利用时间或参与测试线体数量存在无效值。",
                    )
            if unreadable_rows:
                return "UNABLE", f"PDF识别表第{'、'.join(unreadable_rows[:8])}行数值无法识别，不能核对计算。"
            return "PASS", ""
        except GuardStop:
            raise
        except Exception as exc:
            logger.error(f"单线体/工序总产出JPH计算检查异常: {exc}")
            return "NG", "产出效率检查发生异常，请检查表格数据格式。"

    @rule("预估月度产能计算逻辑校验")
    def rule_6(self, ctx: RuleContext) -> Tuple[str, str]:
        """规则 6: 线体/工序预估月度产能计算检查
        识别“线体/工序预估月度产能”、“单线体/工序总产出JPH”、“现有线体数”、“班次/天”、“每班净利用时间”、“每月标准工作天数”、“累积良率”七列，如果七列都存在则继续识别；
        计算值有小数时，“线体/工序预估月度产能”在向下取整值和向上取整值之间则通过；计算值为整数时必须等于计算值。
        """
        try:
            unreadable_rows: list[str] = []
            section = Table(ctx).sheet("主表").section("产能数据区")
            columns = section.columns(
                "线体/工序预估月度产能",
                "单线体/工序总产出JPH",
                "现有线体数",
                "班次/天",
                "每班净利用时间（h）",
                "每月标准工作天数",
                "累积良率",
            )

            is_ocr_table = self._is_ocr_table_sheet(section.sheet_title)
            for row in section.rows_from(columns):
                monthly_capacity_value = row.value("线体/工序预估月度产能", compact=True).lower()
                jph_value = row.value("单线体/工序总产出JPH", compact=True).lower()
                existing_line_qty_value = row.value("现有线体数", compact=True).lower()
                shifts_per_day_value = row.value("班次/天", compact=True).lower()
                net_util_time_per_shift_value = row.value("每班净利用时间（h）", compact=True).lower()
                standard_work_days_per_month_value = row.value("每月标准工作天数", compact=True).lower()
                cumulative_yield_value = row.value("累积良率", compact=True).lower()

                if all(
                    self._is_value_numeric(v)
                    for v in [
                        monthly_capacity_value,
                        jph_value,
                        existing_line_qty_value,
                        shifts_per_day_value,
                        net_util_time_per_shift_value,
                        standard_work_days_per_month_value,
                        cumulative_yield_value,
                    ]
                ):
                    monthly_capacity_num = Decimal(monthly_capacity_value)
                    jph_num = Decimal(jph_value)
                    existing_line_qty_num = Decimal(existing_line_qty_value)
                    shifts_per_day_num = Decimal(shifts_per_day_value)
                    net_util_time_per_shift_num = Decimal(net_util_time_per_shift_value)
                    standard_work_days_per_month_num = Decimal(standard_work_days_per_month_value)
                    cumulative_yield_num = Decimal(cumulative_yield_value)

                    calculated_monthly_capacity = jph_num * existing_line_qty_num * shifts_per_day_num * net_util_time_per_shift_num * standard_work_days_per_month_num * cumulative_yield_num
                    lower_bound = calculated_monthly_capacity.to_integral_value(rounding=ROUND_FLOOR)
                    upper_bound = calculated_monthly_capacity.to_integral_value(rounding=ROUND_CEILING)
                    if not lower_bound <= monthly_capacity_num <= upper_bound:
                        return (
                            "NG",
                            f"第{self._row_label(row, '线体/工序预估月度产能')}行预估月度产能与计算结果不一致。",
                        )
                else:
                    if all(
                        v in {"", "none", "nan"}
                        for v in [
                            monthly_capacity_value,
                            jph_value,
                            existing_line_qty_value,
                            shifts_per_day_value,
                            net_util_time_per_shift_value,
                            standard_work_days_per_month_value,
                            cumulative_yield_value,
                        ]
                    ):
                        continue
                    if is_ocr_table:
                        # A converted PDF table may have misread this row; it cannot pass unseen.
                        unreadable_rows.append(self._row_label(row, *columns))
                        continue
                    row_label = self._row_label(
                        row,
                        "线体/工序预估月度产能",
                        "单线体/工序总产出JPH",
                        "现有线体数",
                        "班次/天",
                        "每班净利用时间（h）",
                        "每月标准工作天数",
                        "累积良率",
                    )
                    return (
                        "NG",
                        f"第{row_label}行月度产能计算相关字段存在无效值。",
                    )
            if unreadable_rows:
                return "UNABLE", f"PDF识别表第{'、'.join(unreadable_rows[:8])}行数值无法识别，不能核对计算。"
            return "PASS", ""
        except GuardStop:
            raise
        except Exception as exc:
            logger.error(f"线体/工序预估月度产能计算检查异常: {exc}")
            return "NG", "产能预估检查发生异常，请检查表格数据格式。"
