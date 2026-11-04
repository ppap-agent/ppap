"""L13 is scoped by process presence, not by an already filled device cell."""

from dataclasses import replace
from types import MappingProxyType

from audit.checkers.v3.equipment_list_agent import EquipmentListAgent
from audit.checkers.v3.excel_agent import rule
from audit.engine.rule_engine import RuleContext, Table, get_doc_resource_by_doc_type
from audit.kinds import PpapDocumentType

from .facts import NativeFacts, columns_present, identity_values, precheck_fact, sheet_present

_BASE = get_doc_resource_by_doc_type(PpapDocumentType.EQUIPMENT_LIST)
assert _BASE is not None
_SECTIONS = dict(_BASE.sections)
_SECTIONS["主数据区"] = _SECTIONS["主数据区"].model_copy(update={"row_anchors": ("产线/工序",), "row_expand_merged_anchors": True})
_RESOURCE = replace(_BASE, sections=MappingProxyType(_SECTIONS))


class LiAutoEquipmentAgent(NativeFacts, EquipmentListAgent):
    EXPECTED_RULE_CATEGORIES = ("设备开发列完整性与数据填充审核", "设备验收列完整性与整改逻辑审核")
    PRECHECK_METHODS = {"L13:1:d9d7fb687737a02a84aa": "rule_1"}

    def __init__(self, audit_file, rule_input, group_context=None):
        super().__init__(audit_file, rule_input, group_context=group_context, resource=_RESOURCE)

    @rule("设备开发列完整性与数据填充审核")
    def rule_1(self, ctx: RuleContext):
        process_count = sum(row.is_present("产线/工序") for sheet in Table(ctx).find_sheets("主表") for row in sheet.section("主数据区").rows("产线/工序"))
        if not process_count:
            return "UNABLE", "没有产线/工序数据行，尚不能核实设备开发要求。"
        return super().rule_1(ctx)

    def precheck(self):
        def identity(ctx, labels):
            boundary = Table(ctx).sheet("主表").section("主数据区").resolved.header_row
            return identity_values(ctx, "主表", labels, before_row=boundary)

        development = ("设备", "供应商", "设备型号", "利旧设备数量", "新采购设备数量", "设备通用情况", "计划到位时间")
        return [
            precheck_fact(self, "L13:S1", lambda ctx: sheet_present(ctx, "主表"), code="B05"),
            precheck_fact(self, "L13:S2", lambda ctx: columns_present(ctx, "主表", "主数据区", ("产线/工序", "设备开发")), code="B05"),
            precheck_fact(self, "L13:S3", lambda ctx: columns_present(ctx, "主表", "主数据区", development), code="B05"),
            precheck_fact(self, "L13:S4", self.rule_1),
            precheck_fact(self, "L13:S5", lambda ctx: columns_present(ctx, "主表", "主数据区", ("设备验收", "验收结果", "整改措施", "整改计划")), code="B05"),
            precheck_fact(self, "L13:S6", self._precheck_result),
            precheck_fact(self, "L13:S7", self.rule_2),
            precheck_fact(self, "L13:S8", lambda ctx: identity(ctx, ("车型",))),
            precheck_fact(self, "L13:S9", lambda ctx: identity(ctx, ("零件名称", "零件号"))),
            precheck_fact(self, "L13:S10", lambda ctx: identity(ctx, ("供应商",))),
        ]

    def _precheck_result(self, ctx):
        for sheet in Table(ctx).find_sheets("主表"):
            rows = list(sheet.section("主数据区").rows("产线/工序", "验收结果"))
            if not rows:
                return "UNABLE", "没有可核实的产线/工序行。"
            for row in rows:
                if row.is_present("产线/工序"):
                    row.expect("验收结果", context="设备验收结果必填")
        return "PASS", "已核实每行验收结果。"
