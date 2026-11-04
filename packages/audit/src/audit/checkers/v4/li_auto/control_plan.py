"""L08 local CP clauses; no unrelated customer cross-file checks."""

from audit.checkers.v2.control_plan_agent import ControlPlanAgent as LegacyControlPlan
from audit.checkers.v3.excel_agent import rule
from audit.engine.rule_engine import Table
from audit.kinds import PpapDocumentType

from .facts import NativeFacts, data_rows


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
    _iter_detail_rows = LegacyControlPlan._iter_detail_rows
    rule_3 = LegacyControlPlan.rule_3
    rule_4 = LegacyControlPlan.rule_4
    rule_5 = LegacyControlPlan.rule_5
    rule_6 = LegacyControlPlan.rule_6

    @rule("生产或SOP选项勾选")
    def rule_1(self, ctx):
        table = Table(ctx)
        block = table.field_block("控制计划头部字段区")
        flags = self._resolve_stage_flags(block, table.sheet("控制计划"))
        block.expect_condition("控制计划类型字段", lambda _: flags["production"], context="必须勾选生产或SOP")
        return "PASS", "已核实生产阶段勾选。"

    @rule("特殊特性分类允许集合")
    def rule_2(self, ctx):
        rows = data_rows(ctx, "控制计划", "控制计划主数据区", ("特殊特性分类", "工序名称"), trigger="工序名称")
        for row in rows:
            row.expect_condition("特殊特性分类", lambda value: str(value or "").strip() in {"关键特殊特性", "重要特殊特性", "NA", "[G]", "[Z]"}, context="仅接受源条款列明的分类")
        return "PASS", "特殊特性分类已逐行核对。"
