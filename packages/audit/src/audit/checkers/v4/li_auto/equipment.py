"""L13 is scoped by process presence, not by an already filled device cell."""

from dataclasses import replace
from types import MappingProxyType

from audit.checkers.v3.equipment_list_agent import EquipmentListAgent
from audit.checkers.v3.excel_agent import rule
from audit.engine.rule_engine import RuleContext, Table, get_doc_resource_by_doc_type
from audit.kinds import PpapDocumentType

_BASE = get_doc_resource_by_doc_type(PpapDocumentType.EQUIPMENT_LIST)
assert _BASE is not None
_SECTIONS = dict(_BASE.sections)
_SECTIONS["主数据区"] = _SECTIONS["主数据区"].model_copy(update={"row_anchors": ("产线/工序",), "row_expand_merged_anchors": True})
_RESOURCE = replace(_BASE, sections=MappingProxyType(_SECTIONS))


class LiAutoEquipmentAgent(EquipmentListAgent):
    def __init__(self, audit_file, rule_input, group_context=None):
        super().__init__(audit_file, rule_input, group_context=group_context, resource=_RESOURCE)

    @rule("设备开发列完整性与数据填充审核")
    def rule_1(self, ctx: RuleContext):
        process_count = sum(row.is_present("产线/工序") for sheet in Table(ctx).find_sheets("主表") for row in sheet.section("主数据区").rows("产线/工序"))
        if not process_count:
            return "UNABLE", "没有产线/工序数据行，尚不能核实设备开发要求。"
        return super().rule_1(ctx)
