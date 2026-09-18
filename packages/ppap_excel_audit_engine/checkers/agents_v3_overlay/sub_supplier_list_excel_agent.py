"""材料、产品及分供方清单审核代理（doc_id=3）。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Tuple

from ppap_core.types import PpapDocumentType

from ppap_excel_audit_engine.rule_engine import (
    RuleContext,
    Table,
)

from .excel_agent import ExcelAgent, rule


def _is_decimal_with_four_places(value: object, number_format: str) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        s_value = value.strip()
        if not s_value:
            return False
        try:
            float(s_value)
        except ValueError:
            return False

    num_format = str(number_format or "")
    if num_format:
        if ".0000" in num_format and ".00000" not in num_format:
            if "%" not in num_format:
                return True

    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return False
    return decimal_value.is_finite() and decimal_value.as_tuple().exponent == -4


class SubSupplierListExcelAgent(ExcelAgent):
    """材料、产品及分供方清单 Excel 文件专业审核代理。"""

    DOC_TYPE = PpapDocumentType.SUB_SUPPLIER_LIST
    EXPECTED_RULE_CATEGORIES = (
        "单件重量列及精度审核",
        "材料信息列完整性及非空审核",
        "部件状态列存在性及互斥性审核",
        "PPAP完成状态逻辑一致性审核",
    )

    @rule("单件重量列及精度审核")
    def rule_1(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets("主表")

        for sheet in sheets:
            section = sheet.section("主数据区")
            columns = section.columns("单件重量", "零件号", optional=("序号",))
            rows = list(section.rows_from(columns))
            if not rows:
                return "NG", f"工作表[{sheet.title}]主数据区为空表"

            checked_rows = 0
            for row in rows:
                if not row.is_present("零件号"):
                    continue
                checked_rows += 1
                weight_cell = row.anchor("单件重量")
                if weight_cell is None:
                    return "NG", f"工作表[{sheet.title}]缺少单件重量列"
                row.expect_condition(
                    "单件重量",
                    lambda value: _is_decimal_with_four_places(value, weight_cell.number_format()),
                    context="单件重量必须是四位小数格式",
                )
            if checked_rows == 0:
                return "NG", f"工作表[{sheet.title}]未识别到有效零件号数据行"

        return "PASS", ""

    @rule("材料信息列完整性及非空审核")
    def rule_2(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets("主表")

        required_material_columns = (
            "材料执行标准",
            "材料类型",
            "材料牌号",
            "材料规格/尺寸",
            "材料供应商名称",
            "通过何种质量管理体系认证",
        )

        for sheet in sheets:
            section = sheet.section("主数据区")
            columns = section.columns(
                "零件号",
                *required_material_columns,
                optional=("序号",),
            )
            rows = list(section.rows_from(columns))
            if not rows:
                return "NG", f"工作表[{sheet.title}]主数据区为空表"

            checked_rows = 0
            for row in rows:
                if not row.is_present("零件号"):
                    continue
                checked_rows += 1
                row.expect(*required_material_columns, context="材料信息缺失")
            if checked_rows == 0:
                return "NG", f"工作表[{sheet.title}]未识别到有效零件号数据行"
        return "PASS", ""

    @rule("部件状态列存在性及互斥性审核")
    def rule_3(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets("主表")

        status_columns = ("自制", "外协-国产", "外协-进口", "领用件")

        for sheet in sheets:
            section = sheet.section("主数据区")
            columns = section.columns(
                *status_columns,
                optional=("序号", "零件号"),
            )
            rows = list(section.rows_from(columns))
            if not rows:
                return "NG", f"工作表[{sheet.title}]主数据区为空表"

            checked_rows = 0
            for row in rows:
                triggered = row.is_present("零件号")
                if not triggered:
                    continue
                checked_rows += 1
                row.expect_condition(
                    status_columns[0],
                    lambda _value: row.count_present(*status_columns) == 1,
                    context="部件状态列必须且仅能有一列有内容",
                )
            if checked_rows == 0:
                return "NG", f"工作表[{sheet.title}]未识别到有效零件号数据行"
        return "PASS", ""

    @rule("PPAP完成状态逻辑一致性审核")
    def rule_4(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets("主表")

        for sheet in sheets:
            section = sheet.section("主数据区")
            columns = section.columns(
                "零件号",
                "外协-国产",
                "外协-进口",
                "完成PPAP",
                optional=("序号",),
            )
            rows = list(section.rows_from(columns))
            if not rows:
                return "NG", f"工作表[{sheet.title}]主数据区为空表"

            checked_rows = 0
            for row in rows:
                triggered = row.is_present("零件号") and (row.is_present("外协-国产") or row.is_present("外协-进口"))
                if not triggered:
                    continue
                checked_rows += 1
                ppap_cell = row.anchor("完成PPAP")
                row_index = ppap_cell.row_index if ppap_cell is not None else 0
                row.expect_equal(
                    "完成PPAP",
                    "Y",
                    case_insensitive=True,
                    compact=True,
                    context=f"工作表[{sheet.title}]第{row_index}行完成PPAP必须严格等于Y",
                )
            if checked_rows == 0:
                continue
        return "PASS", ""
