"""材料、产品及分供方清单审核代理（doc_id=3）。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Tuple

from ppap_core.types import PpapDocumentType

from ppap_excel_audit_engine.rule_engine import (
    GuardStop,
    RuleContext,
    Table,
)
from ppap_excel_audit_engine.rule_engine.contracts import Decision

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

    @rule("单件重量列存在性和数据格式检查")
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

    @rule("材料信息列存在性和数据完整性检查")
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

    @rule("部件状态列存在性和数据唯一性检查")
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

    @rule("PPAP完成状态列存在性和数据关联性检查")
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

            ws = section.resolved.ws
            section_spec = ctx.resource.sections["主数据区"]
            part_col = columns["零件号"].header_col
            domestic_col = columns["外协-国产"].header_col
            import_col = columns["外协-进口"].header_col
            ppap_col = columns["完成PPAP"].header_col

            def _norm(value: object) -> str:
                return "" if value is None else str(value).strip().lower()

            def _compact(value: object) -> str:
                return "".join(_norm(value).split())

            def _token_hit(value: str, tokens: tuple[str, ...]) -> bool:
                lowered_tokens = {str(token).strip().lower() for token in tokens if str(token).strip()}
                return value in lowered_tokens

            def _cell_text(row_index: int, col_index: int) -> str:
                return _norm(
                    ctx.ops.get_effective_cell_value(
                        ws,
                        row_index,
                        col_index,
                        strip=True,
                        lower=False,
                        remove_whitespace=False,
                    )
                )

            def _is_present(row_index: int, column_name: str) -> bool:
                value = _cell_text(row_index, columns[column_name].header_col)
                if not value:
                    return False
                column_spec = ctx.resource.columns[column_name]
                if _token_hit(value, column_spec.filled_tokens):
                    return True
                if _token_hit(value, column_spec.missing_tokens):
                    return False
                if _token_hit(value, ctx.resource.tokens.filled_tokens):
                    return True
                if _token_hit(value, ctx.resource.tokens.missing_tokens):
                    return False
                return True

            normalized_stop_markers = tuple(marker.strip().lower() for marker in section_spec.row_stop_markers if marker.strip())

            def _row_hits_stop_marker(row_index: int) -> bool:
                if not normalized_stop_markers:
                    return False
                for col_index in range(1, ws.max_column + 1):
                    text = _cell_text(row_index, col_index)
                    if text and any(marker in text for marker in normalized_stop_markers):
                        return True
                return False

            def _is_repeated_header_row(row_index: int) -> bool:
                part_text = _compact(_cell_text(row_index, part_col))
                domestic_text = _compact(_cell_text(row_index, domestic_col))
                import_text = _compact(_cell_text(row_index, import_col))
                ppap_text = _compact(_cell_text(row_index, ppap_col))
                part_header = ("零件号" in part_text) or ("partno" in part_text) or ("componentnumber" in part_text)
                status_header = (("国产" in domestic_text) or ("local" in domestic_text)) and (("进口" in import_text) or ("import" in import_text))
                ppap_header = ("ppap" in ppap_text) or ("完成ppap" in ppap_text) or ("productionapproved" in ppap_text)
                return part_header and (status_header or ppap_header)

            checked_rows = 0
            has_active_part_context = False
            start_row = min(row.row_index for row in rows)
            for row_index in range(start_row, ws.max_row + 1):
                if _row_hits_stop_marker(row_index):
                    break
                if _is_repeated_header_row(row_index):
                    continue

                if _is_present(row_index, "零件号"):
                    has_active_part_context = True

                subcontracted = _is_present(row_index, "外协-国产") or _is_present(row_index, "外协-进口")
                triggered = subcontracted and has_active_part_context
                if not triggered:
                    continue
                checked_rows += 1
                ppap_value = _cell_text(row_index, ppap_col)
                if _compact(ppap_value) != "y":
                    raise GuardStop(
                        Decision.NG,
                        (f"工作表 {sheet.title} 行号{row_index}（工作表[{sheet.title}]第{row_index}行完成PPAP必须严格等于Y）列[完成PPAP]命中值[{ppap_value}]不等于期望值[Y]"),
                    )
            if checked_rows == 0:
                continue
        return "PASS", ""
