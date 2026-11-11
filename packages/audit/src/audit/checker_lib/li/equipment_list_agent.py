"""设备清单 Excel 审核代理。"""

from __future__ import annotations

from typing import Any, Tuple

from audit.engine.rule_engine import RuleContext, Table
from audit.kinds import PpapDocumentType

from .excel_agent import ExcelAgent, rule


class EquipmentListAgent(ExcelAgent):
    """设备清单专业审核代理。"""

    DOC_TYPE = PpapDocumentType.EQUIPMENT_LIST

    @rule("产线/工序、设备开发检查")
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

        any_line_process = False
        for sheet in table.find_sheets("主表"):
            section = sheet.section("主数据区")
            columns = section.columns(
                "产线/工序",
                "设备开发",
                *key_columns,
            )

            for row in section.rows_from(columns):
                has_line_process = row.is_present("产线/工序")
                any_line_process = any_line_process or has_line_process

                if not has_line_process:

                    def empty_development_columns(_value: object, current_row: Any = row) -> bool:
                        return bool(current_row.count_present(*key_columns) == 0)

                    row.expect_condition(
                        "产线/工序",
                        empty_development_columns,
                        context="产线/工序为空时，设备开发相关列不应填写内容",
                    )
                    continue

                row.expect(*key_columns, context="产线/工序")

        if not any_line_process:
            return "NG", "未识别到任何有效的“产线/工序”数据行。"
        return "PASS", ""

    @rule("设备验收检查")
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

                    def empty_accept_columns(_value: object, current_row: Any = row) -> bool:
                        return bool(current_row.count_present(*accept_columns) == 0)

                    row.expect_condition(
                        "产线/工序",
                        empty_accept_columns,
                        context="产线/工序为空时，验收结果/整改措施/整改计划不应填写内容",
                    )
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
