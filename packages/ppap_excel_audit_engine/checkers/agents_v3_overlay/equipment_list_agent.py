"""设备清单 Excel 审核代理。"""

from __future__ import annotations

from typing import Tuple

from ppap_core.types import PpapDocumentType

from ppap_excel_audit_engine.rule_engine import RuleContext, Table

from .excel_agent import ExcelAgent, rule


class EquipmentListAgent(ExcelAgent):
    """设备清单专业审核代理。"""

    DOC_TYPE = PpapDocumentType.EQUIPMENT_LIST
    EXPECTED_RULE_CATEGORIES = (
        "设备开发列完整性与数据填充审核",
        "设备验收列完整性与整改逻辑审核",
    )

    @rule("设备开发列完整性与数据填充审核")
    def rule_1(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        key_columns = (
            "设备",
            "供应商",
            "设备型号",
            "利旧设备数量",
            "新采购设备数量",
            "设备通用情况",
            "计划到位时间",
        )

        for sheet in table.find_sheets("主表"):
            section = sheet.section("主数据区")
            columns = section.columns(
                "产线/工序",
                "设备开发",
                *key_columns,
            )

            for row in section.rows_from(columns):
                has_line_process = row.is_present("产线/工序")

                if not has_line_process:
                    continue

                row.expect(*key_columns, context="产线/工序")

        return "PASS", ""

    @rule("设备验收列完整性与整改逻辑审核")
    def rule_2(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        accept_columns = ("验收结果", "整改措施", "整改计划")

        has_line_process = False
        for sheet in table.find_sheets("主表"):
            section = sheet.section("主数据区")
            columns = section.columns(
                "产线/工序",
                "设备验收",
                *accept_columns,
            )

            for row in section.rows_from(columns):
                has_process = row.is_present("产线/工序")
                if not has_process:
                    continue

                has_line_process = True

                row.expect("验收结果", context="设备验收")

                if row.exact("验收结果", "不通过", compact=True) or row.exact(
                    "验收结果",
                    "条件通过",
                    compact=True,
                ):
                    row.expect("整改措施", "整改计划", context="验收结果异常整改")

        if not has_line_process:
            return "NG", "未识别到任何有效的“产线/工序”数据行。"

        return "PASS", ""
