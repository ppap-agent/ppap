"""PGQP1 (采购件质量计划) originals; aliases read from UAES corpus files."""

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

from . import PER_PAGE, STITCHED, WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

# PGQP1 (采购件质量计划): its 4. Important Character List, repeated per section (毛坯, 机加工).
DOCUMENT = (
    (SheetModel(canonical_name="PGQP1", include_keywords=("pgqp1",), match_mode="exact"),),
    (
        SectionModel(
            header_search_max_row=WHOLE_SHEET_ROWS,
            header_search_max_col=WHOLE_SHEET_COLS,
            canonical_name="重要特性区",
            sheet_name="PGQP1",
            header_anchors=("特性项目", "特殊特性", "测量设备"),
            header_hit=3,
            # Headings are bilingual with a line break: "Measuring Equipment\n测量设备".
            header_compact_match=True,
            row_anchors=("特性项目",),
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET_ROWS,
            row_stop_markers=("5. Product Audit", "产品审核"),
        ),
    ),
    (
        ColumnModel(canonical_name="特性项目", section_name="重要特性区", aliases=("Items/项目",)),
        ColumnModel(canonical_name="特殊特性", section_name="重要特性区", aliases=("SC/特殊特性",)),
        ColumnModel(canonical_name="测量设备", section_name="重要特性区", aliases=("Measuring Equipment\n测量设备",)),
    ),
)

PDF_SPECS = (STITCHED, PER_PAGE)
