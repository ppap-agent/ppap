"""PFMEAs as U08 reads them: their process steps and classified rows; aliases from UAES corpus files."""

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

from . import PER_PAGE, STITCHED, WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

# 专用PFMEA-776/A40, 6、P FMEA（磁性罩）.pdf, PFMEA_脱敏版.pdf.
DOCUMENT = (
    (SheetModel(canonical_name="U08PFMEA", include_keywords=("pfmea", "fmea", "table_", "page")),),
    (
        SectionModel(
            canonical_name="U08PFMEA区",
            sheet_name="U08PFMEA",
            header_anchors=("U08PFMEA工序", "U08PFMEA分类"),
            header_hit=2,
            header_search_max_row=WHOLE_SHEET_ROWS,
            header_search_max_col=WHOLE_SHEET_COLS,
            # A class is written on a failure-mode row whose step cell is merged above it.
            row_anchors=("U08PFMEA工序", "U08PFMEA分类"),
            row_anchor_match="any",
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET_ROWS,
        ),
    ),
    (
        # "Process/Function/ Requirements 工序/功能/要求" (776/A40), "项目 / 功能 Item/Function" (V040),
        # "Process Step / Function 过程步骤 / 功能" (PFMEA_脱敏版).
        ColumnModel(canonical_name="U08PFMEA工序", section_name="U08PFMEA区", aliases=("工序/功能/要求", "项目 / 功能", "过程步骤 / 功能")),
        # "CLASS类别" (776/A40), "级别 Level" (V040), "Classification 类分" (PFMEA_脱敏版).
        ColumnModel(canonical_name="U08PFMEA分类", section_name="U08PFMEA区", aliases=("CLASS类别", "级别", "类分")),
    ),
)

PDF_SPECS = (STITCHED, PER_PAGE)
