"""初始过程能力研究 Excel 审核代理。"""

from __future__ import annotations

import logging
from typing import Tuple

from audit.engine.rule_engine import RuleContext, Table
from audit.kinds import PpapDocumentType

from .excel_agent import ExcelAgent, rule

logger = logging.getLogger(__name__)


def _is_na_or_cpk_above_threshold(value: object, *, threshold: float) -> bool:
    compact_value = "".join(str(value or "").split()).lower()
    if not compact_value:
        return False
    if compact_value == "na":
        return True
    try:
        return float(compact_value) > threshold
    except ValueError:
        return False


def _is_cpk_above_threshold(value: object, *, threshold: float) -> bool:
    compact_value = "".join(str(value or "").split()).lower()
    if not compact_value:
        return False
    try:
        return float(compact_value) > threshold
    except ValueError:
        return False


class InitialProcessCapacityAgent(ExcelAgent):
    """初始过程能力研究专业审核代理。"""

    DOC_TYPE = PpapDocumentType.INITIAL_PROCESS_CAPACITY_STUDY
    EXPECTED_RULE_CATEGORIES = (
        "P阶段Cpk结果列审核",
        "P阶段结果判定列审核",
        "控制图及短期过程能力审核",
    )

    @rule("P阶段Cpk结果列审核")
    def rule_1(self, ctx: RuleContext) -> Tuple[str, str]:
        section = Table(ctx).sheet("CCSC主表").section("CCSC数据区")
        columns = section.columns("工序流程编号", "P阶段Cpk结果")
        rows = list(section.rows_from(columns))
        if not rows:
            return "NG", "CCSC数据区未识别到任何数据行"
        checked_rows = 0

        for row in rows:
            if not row.is_present("工序流程编号"):
                continue
            checked_rows += 1
            row.expect(
                "P阶段Cpk结果",
                context="工序流程编号",
            ).expect_condition(
                "P阶段Cpk结果",
                lambda value: _is_na_or_cpk_above_threshold(value, threshold=1.33),
                context="P阶段Cpk结果值必须为NA或可转换为数字且大于1.33",
            )

        if checked_rows == 0:
            return "NG", "CCSC数据区未识别到有效的工序流程编号数据行"
        return "PASS", ""

    @rule("P阶段结果判定列审核")
    def rule_2(self, ctx: RuleContext) -> Tuple[str, str]:
        section = Table(ctx).sheet("CCSC主表").section("CCSC数据区")
        columns = section.columns("工序流程编号", "P阶段结果判定")
        rows = list(section.rows_from(columns))
        if not rows:
            return "NG", "CCSC数据区未识别到任何数据行"
        checked_rows = 0

        for row in rows:
            if not row.is_present("工序流程编号"):
                continue
            checked_rows += 1
            row.expect(
                "P阶段结果判定",
                context="工序流程编号",
            ).expect_equal(
                "P阶段结果判定",
                "达标",
                case_insensitive=False,
                compact=True,
                context="P阶段结果判定必须严格等于'达标'",
            )

        if checked_rows == 0:
            return "NG", "CCSC数据区未识别到有效的工序流程编号数据行"
        return "PASS", ""

    @rule("控制图及短期过程能力审核")
    def rule_3(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        chart_specs = (
            ("I-MR控制图", "I-MR短期能力区", "I-MR短期过程能力", "I-MR-Cpk"),
            ("Xbar-R控制图", "Xbar-R短期能力区", "Xbar-R短期过程能力", "Xbar-R-Cpk"),
            ("Xbar-S控制图", "Xbar-S短期能力区", "Xbar-S短期过程能力", "Xbar-S-Cpk"),
        )

        for sheet_name, block_name, phrase_field_name, cpk_field_name in chart_specs:
            table.find_sheets(sheet_name)
            block = table.field_block(block_name)
            block.expect(
                phrase_field_name,
                cpk_field_name,
                context=sheet_name,
            ).expect_condition(
                cpk_field_name,
                lambda value: _is_cpk_above_threshold(value, threshold=1.33),
                context=f"{sheet_name}图表短期过程能力Cpk值必须可转换为数字且大于1.33",
            )

        return "PASS", ""
