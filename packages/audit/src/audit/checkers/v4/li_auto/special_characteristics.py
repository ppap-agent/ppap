"""L12: reuse CCSC checks, with header identity distinct from NIO identity cells."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from audit.checkers.v2.process_special_characteristic_agent import ProcessSpecialCharacteristicAgent as CcscChecks
from audit.checkers.v3.excel_agent import ExcelAgent, rule
from audit.engine.rule_engine import Row, RuleContext, Table, get_doc_resource_by_doc_type
from audit.kinds import PpapDocumentType

_BASE = get_doc_resource_by_doc_type(PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST)
assert _BASE is not None
_SECTIONS = dict(_BASE.sections)
# NIO's part number/name columns also occur in Li Auto's identity block. They
# cannot identify Li Auto's data header, whose T1 group has a second header row.
_SECTIONS["主数据区"] = _SECTIONS["主数据区"].model_copy(update={"header_anchors": ("过程/流程步骤", "特性名称"), "header_hit": 2, "row_anchors": ("过程/流程步骤",)})
_RESOURCE = replace(_BASE, sections=MappingProxyType(_SECTIONS))


class LiAutoSpecialCharacteristicsAgent(ExcelAgent):
    EXPECTED_RULE_CATEGORIES = ("关键字段完整性检查", "CCSC结构与逐行必填", "高风险行必须有预防措施")
    PRECHECK_METHODS = {"L12:1:c437062e97210ecb39ba": "rule_1", "L12:2:9344878425e7576fad48": "rule_2"}
    DOC_TYPE = PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST
    rule_1 = CcscChecks.rule_1

    def __init__(self, audit_file, rule_input, group_context=None):
        super().__init__(audit_file, rule_input, group_context=group_context, resource=_RESOURCE)

    @rule("CCSC结构与逐行必填")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        rows = 0
        for sheet in Table(ctx).find_sheets("主表"):
            section = sheet.section("主数据区")
            for row in section.rows_from(section.columns("过程/流程步骤")):
                if row.is_present("过程/流程步骤"):
                    rows += 1
        if not rows:
            return "UNABLE", "没有有效的过程/流程步骤，尚不能核实逐行要求。"
        return CcscChecks.rule_2(self, ctx)

    @rule("高风险行必须有预防措施")
    def rule_3(self, ctx: RuleContext) -> tuple[str, str]:
        scanned = 0
        for sheet in Table(ctx).find_sheets("主表"):
            section = sheet.section("主数据区")
            columns = section.columns("现有管理方式风险评估", "高风险管理项预防措施")
            # L12 clause 3 applies to every risk row. A missing process step
            # must not remove that row from the scope of the risk requirement.
            start = max(column.data_start_row for column in columns.values())
            for index in range(start, sheet.ws.max_row + 1):
                row = Row(ctx, sheet.ws, columns, index, section.resolved)
                scanned += 1
                if row.exact("现有管理方式风险评估", "高风险", compact=True):
                    row.expect("高风险管理项预防措施", context="理想高风险行")
        ctx.note_scan(yielded=scanned, finished=True)
        return ("PASS", "") if scanned else ("UNABLE", "未找到风险评估数据行，尚不能核实高风险措施。")
