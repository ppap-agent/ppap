"""C sample release report covers (U19, F-NE-185) and deviation release lists; aliases read from UAES corpus files."""

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

from . import PER_PAGE, STITCHED, WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

# Both PDFs convert to one table per page ("Table_1_p1-1"); an .xls cover keeps its sheet name.
_SHEETS = ("封面", "cover", "认可报告", "偏差", "deviation", "table_", "page-", "sheet")

DOCUMENT = (
    (SheetModel(canonical_name="认可封面", include_keywords=_SHEETS),),
    (
        SectionModel(
            canonical_name="认可区",
            sheet_name="认可封面",
            # "报告内容\nContent of report | 批准\nApproval | 带条件批准\nCondition\napproval | 否决\nReject".
            header_anchors=("报告内容", "带条件批准", "否决"),
            header_hit=3,
            header_search_max_row=WHOLE_SHEET_ROWS,
            header_search_max_col=WHOLE_SHEET_COLS,
            row_anchors=("报告内容",),
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET_ROWS,
            # The checked-by line closes the four report rows.
            row_stop_markers=("审核：", "Checked by"),
        ),
    ),
    (
        ColumnModel(canonical_name="报告内容", section_name="认可区", aliases=("报告内容",)),
        ColumnModel(canonical_name="带条件批准", section_name="认可区", aliases=("带条件批准",)),
        ColumnModel(canonical_name="否决", section_name="认可区", aliases=("否决",)),
    ),
)

DEVIATIONS = (
    (SheetModel(canonical_name="偏差认可汇总", include_keywords=_SHEETS),),
    (
        SectionModel(
            canonical_name="偏差区",
            sheet_name="偏差认可汇总",
            header_anchors=("偏差项", "UAES评估确认", "是否需要偏差"),
            header_hit=3,
            header_search_max_row=WHOLE_SHEET_ROWS,
            header_search_max_col=WHOLE_SHEET_COLS,
            row_anchors=("是否需要偏差",),
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET_ROWS,
            row_stop_markers=("供应商申请人", "Supplier Proposer"),
        ),
    ),
    (
        ColumnModel(canonical_name="偏差项", section_name="偏差区", aliases=("偏差项",)),
        ColumnModel(canonical_name="UAES评估确认", section_name="偏差区", aliases=("UAES 评估确认", "UAES评估确认")),
        # The clause's 是否需要偏差 is headed "是否同意\n偏差\nYes or\nNo" on the list.
        ColumnModel(canonical_name="是否需要偏差", section_name="偏差区", aliases=("是否同意 偏差", "是否同意偏差")),
        ColumnModel(canonical_name="偏差备注", section_name="偏差区", aliases=("备注",)),
    ),
)

PDF_SPECS = (PER_PAGE, STITCHED)

# Labels written beside their values ("零件号\nPart No." | F01RD0YC50).
PART_NUMBER = ("零件号\nPart No.",)
PART_NAME = ("零件名称\nPart name", "零件名称\nPart Name")
REMARK = "UAES批准的前提"
CATEGORIES = ("尺寸", "材料", "性能", "试装")
ROLES = ("组长", "审核", "批准")
