"""L08 local CP clauses; no unrelated customer cross-file checks."""

from dataclasses import replace
from types import MappingProxyType

from audit.checkers.v2.control_plan_agent import ControlPlanAgent as LegacyControlPlan
from audit.checkers.v3.excel_agent import rule
from audit.engine.checkers.control_plan_section_headers import detail_section_header_merge, detail_table_bounds
from audit.engine.rule_engine import Decision, GuardStop, Row, Table, get_doc_resource_by_doc_type
from audit.kinds import PpapDocumentType

from .facts import NativeFacts, compact

_BASE = get_doc_resource_by_doc_type(PpapDocumentType.CONTROL_PLAN)
assert _BASE is not None
_COLUMNS = dict(_BASE.columns)
_COLUMNS["产品"] = _COLUMNS["产品"].model_copy(update={"aliases": (*_COLUMNS["产品"].aliases, "产品特性", "特性产品", "product characteristic", "characteristic product", "product")})
_COLUMNS["过程"] = _COLUMNS["过程"].model_copy(update={"aliases": (*_COLUMNS["过程"].aliases, "过程特性", "特性过程", "process characteristic", "characteristic process", "process")})
_RESOURCE = replace(_BASE, columns=MappingProxyType(_COLUMNS))


class LiAutoControlPlanAgent(NativeFacts):
    EXPECTED_RULE_CATEGORIES = ("生产或SOP选项勾选", "特殊特性分类允许集合", "特殊特性分类、特性列列检查", "特殊特性分类、控制方法列检查", "频率/频次列检查", "表头关键信息检查")
    DOC_TYPE = PpapDocumentType.CONTROL_PLAN
    _extract_inline_value = LegacyControlPlan._extract_inline_value
    _normalize_stage_text = staticmethod(LegacyControlPlan._normalize_stage_text)
    _contains_check_mark = LegacyControlPlan._contains_check_mark
    _contains_checkbox_marker = LegacyControlPlan._contains_checkbox_marker
    _normalized_stage_token_groups = staticmethod(LegacyControlPlan._normalized_stage_token_groups)
    _collect_stage_signal_cells = LegacyControlPlan._collect_stage_signal_cells
    _is_stage_checked = LegacyControlPlan._is_stage_checked
    _resolve_stage_flags = LegacyControlPlan._resolve_stage_flags
    rule_6 = LegacyControlPlan.rule_6

    def __init__(self, audit_file, rule_input, group_context=None):
        super().__init__(audit_file, rule_input, group_context=group_context, resource=_RESOURCE)

    @rule("生产或SOP选项勾选")
    def rule_1(self, ctx):
        table = Table(ctx)
        block = table.field_block("控制计划头部字段区")
        flags = self._resolve_stage_flags(block, table.sheet("控制计划"))
        block.expect_condition("控制计划类型字段", lambda _: flags["production"], context="必须勾选生产或SOP")
        return "PASS", "已核实生产阶段勾选。"

    def _business_rows(self, ctx, required):
        result = []
        names = ("工序名称", "特殊特性分类", "产品", "过程", "控制方法", "频率频次")
        for sheet in Table(ctx).find_sheets("控制计划"):
            section = sheet.section("控制计划主数据区")
            columns = section.columns(*required, optional=tuple(name for name in names if name not in required))
            left, right = detail_table_bounds(sheet.ws, header_row=section.resolved.header_row, columns=(column.header_col for column in columns.values()))
            aliases = {compact(alias).casefold() for name in columns for alias in ctx.resource.columns[name].aliases}
            for index in range(max(column.data_start_row for column in columns.values()), sheet.ws.max_row + 1):
                if detail_section_header_merge(sheet.ws, index, table_left=left, table_right=right) is not None:
                    continue
                raw = [sheet.ws.cell(index, column.header_col).value for column in columns.values()]
                filled = [compact(value) for value in raw if compact(value)]
                if not filled:
                    continue
                if len(filled) >= 2 and all(value.casefold() in aliases for value in filled):
                    continue
                result.append(Row(ctx, sheet.ws, columns, index, section.resolved))
        ctx.note_scan(yielded=len(result), finished=True)
        if not result:
            raise GuardStop(Decision.UNABLE, "没有可核实的控制计划业务行。")
        return result

    @rule("特殊特性分类允许集合")
    def rule_2(self, ctx):
        for row in self._business_rows(ctx, ("特殊特性分类",)):
            row.expect_condition("特殊特性分类", lambda value: str(value or "").strip() in {"关键特殊特性", "重要特殊特性", "NA", "[G]", "[Z]"}, context="仅接受源条款列明的分类")
        return "PASS", "特殊特性分类已逐行核对。"

    def _require_unique_characteristic_columns(self, ctx, rows):
        checked = set()
        for row in rows:
            signature = (row.sheet_title, row.section.header_row)
            if signature in checked:
                continue
            checked.add(signature)
            product, process = row.columns["产品"], row.columns["过程"]
            end = max(product.data_start_row, process.data_start_row) - 1
            candidates = {"产品": set(), "过程": set()}
            aliases = {
                "产品": {compact(alias).casefold() for alias in ctx.resource.columns["产品"].aliases} | {"产品特性", "特性产品", "product", "productcharacteristic", "characteristicproduct"},
                "过程": {compact(alias).casefold() for alias in ctx.resource.columns["过程"].aliases} | {"过程特性", "特性过程", "process", "processcharacteristic", "characteristicprocess"},
            }
            for cells in row.ws.iter_rows(min_row=row.section.header_row, max_row=end):
                for cell in cells:
                    for name in candidates:
                        if compact(cell.value).casefold() in aliases[name]:
                            ctx.note_cell(row.sheet_title, cell.row, cell.column)
                            candidates[name].add(cell.column)
            if candidates["产品"] != {product.header_col} or candidates["过程"] != {process.header_col}:
                raise GuardStop(Decision.UNABLE, "产品/过程特性叶列存在同名竞争或归属不清，尚不能唯一还原业务结构。")

    @rule("特殊特性分类、特性列列检查")
    def rule_3(self, ctx):
        rows = self._business_rows(ctx, ("特殊特性分类", "产品", "过程"))
        self._require_unique_characteristic_columns(ctx, rows)
        for row in rows:
            if row.is_present("特殊特性分类"):
                row.expect_condition("产品", lambda _, current=row: current.count_present("产品", "过程") > 0, context="分类有值时产品或过程特性至少一项非空")
        return "PASS", "对应产品或过程特性已逐行核对。"

    @rule("特殊特性分类、控制方法列检查")
    def rule_4(self, ctx):
        for row in self._business_rows(ctx, ("特殊特性分类", "控制方法")):
            if compact(row.value("特殊特性分类")) in {"关键特殊特性", "重要特殊特性", "[G]", "[Z]"}:
                row.expect("控制方法", context="G/Z或关键重要特性的控制方法")
        return "PASS", "适用分类的控制方法已逐行核对。"

    @rule("频率/频次列检查")
    def rule_5(self, ctx):
        for row in self._business_rows(ctx, ("频率频次",)):
            row.expect("频率频次", context="每个业务行的频率或频次")
        return "PASS", "每个业务行的频率或频次均已核实。"
