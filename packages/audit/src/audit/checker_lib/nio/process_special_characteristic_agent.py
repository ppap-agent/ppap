"""过程特性管理清单 Excel 审核代理。"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Tuple

from audit.engine.rule_engine import RuleContext, Table
from audit.kinds import PpapDocumentType

from .excel_agent import ExcelAgent, rule


class ProcessSpecialCharacteristicAgent(ExcelAgent):
    """过程特性管理清单专业审核代理。"""

    DOC_TYPE = PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST
    # Every row of a vertically merged 零件编号 block is a row of the list.
    SECTION_OPTIONS = MappingProxyType({"主数据区": {"row_expand_merged_anchors": True}})
    EXPECTED_RULE_CATEGORIES = (
        "产品编号识别",
        "列完整性及内容非空检查",
    )

    @rule("产品编号识别")
    def rule_1(self, ctx: RuleContext) -> Tuple[str, str]:
        sheets = Table(ctx).find_sheets("主表")
        for sheet in sheets:
            for row in range(1, min(sheet.ws.max_row, 3) + 1):
                for col in range(1, min(sheet.ws.max_column, 8) + 1):
                    value = sheet.cell(row, col).value()
                    text = "" if value is None else str(value).strip()
                    if "特殊特性清单" not in text:
                        continue
                    prefix = text.split("特殊特性清单", 1)[0]
                    if re.search(r"[A-Za-z]\d{4,}|P\d{4,}|\d{5,}", prefix):
                        return "PASS", ""
                    return "NG", "特殊特性清单标题前未识别到产品编号"
        return "NG", "未找到特殊特性清单标题"

    @rule("列完整性及内容非空检查")
    def rule_2(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets("主表")
        section = sheets[0].section("主数据区")
        required_columns = (
            "零件编号",
            "零件名称",
            "特性分类",
            "特性描述",
            "特性值",
            "特性类别",
            "特殊特性种类",
            "控制方法（制造过程）",
            "防错方案描述",
            "频次",
            "样本数",
        )
        columns = section.columns(*required_columns)

        checked_rows = 0
        for row in section.rows_from(columns):
            if not row.is_present("零件编号") or not row.is_present("零件名称"):
                continue

            missing = []
            for column_name in required_columns:
                if not row.is_present(column_name):
                    missing.append(column_name)
            if missing:
                return "NG", f"工作表{row.sheet_title} 行{row.row_index}存在空值，空值列: {missing}"
            checked_rows += 1

        if checked_rows == 0:
            return "NG", "特殊特性清单主数据区未识别到有效数据行"
        return "PASS", ""
