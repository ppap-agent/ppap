"""U12: material identity and UAES characteristic columns, not NIO columns."""

from __future__ import annotations

from audit.checkers.v3.excel_agent import ExcelAgent, rule
from audit.engine.rule_engine import RuleContext, Table
from audit.engine.rule_engine.contracts import ColumnModel, FieldBlockModel, FieldModel, RuleResourceModel, SectionModel, SheetModel, index_rule_resource
from audit.kinds import PpapDocumentType

_ALIASES = {
    "编号": ("编号", "序号"),
    "特性名称": ("产品特性", "过程特性", "特殊特性名称"),
    "特性值": ("特性及公差", "特性值"),
    "特性类别": ("特性类型", "特性类别"),
}
_RESOURCE = index_rule_resource(
    RuleResourceModel(
        sheets=(SheetModel(canonical_name="主表", include_keywords=("特殊特性", "特性清单")),),
        sections=(
            SectionModel(
                canonical_name="主数据区",
                sheet_name="主表",
                header_anchors=tuple(_ALIASES),
                row_anchors=("编号",),
                header_hit=2,
                header_search_max_row=20,
                header_search_max_col=64,
                header_compact_match=True,
                column_band=2,
                row_stop_on_first_blank=False,
                row_max_blank_streak=8,
            ),
        ),
        columns=tuple(ColumnModel(canonical_name=key, section_name="主数据区", aliases=aliases) for key, aliases in _ALIASES.items()),
        field_blocks=(FieldBlockModel(canonical_name="物料信息", sheet_name="主表", search_max_row=10, search_max_col=24),),
        fields=tuple(FieldModel(canonical_name=name, block_name="物料信息", aliases=(name,), value_mode="right", scan_width=4) for name in ("物料名称", "物料编号")),
    )
)


class UaesSpecialCharacteristicsAgent(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST

    def __init__(self, audit_file, rule_input, group_context=None):
        super().__init__(audit_file, rule_input, group_context=group_context, resource=_RESOURCE)

    @rule("物料名称与编号必填")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        Table(ctx).field_block("物料信息").expect("物料名称", "物料编号", context="UAES物料信息")
        return "PASS", ""

    @rule("特殊特性列与逐行必填")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        count = 0
        for sheet in Table(ctx).find_sheets("主表"):
            section = sheet.section("主数据区")
            columns = section.columns(*_ALIASES)
            for row in section.rows_from(columns):
                if not row.is_present("编号"):
                    continue
                count += 1
                row.expect("特性名称", "特性值", "特性类别", context="UAES特殊特性")
        return ("PASS", "") if count else ("UNABLE", "没有已编号的特性行，尚不能核实逐行要求。")
