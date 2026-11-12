"""Process flow chart originals (U16); aliases read from UAES corpus files."""

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

from . import PER_PAGE, STITCHED, WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

# The notes (DP4Z, V040) or sign-off (776/A40) rows follow the last operation.
STOP_MARKERS = ("注释Notes", "prepared 编制")

# A flow chart table: a step column and an operation description column with the activity
# symbol lanes between them (776/A40 "PFD (2)", DP4Z "P0", V040 PDF page 1). The sheets are
# named neither alike nor after the document, so they are found by this header row.
DOCUMENT = (
    (SheetModel(canonical_name="过程流程图", include_keywords=("过程流程图",)),),
    (
        SectionModel(
            canonical_name="流程区",
            sheet_name="过程流程图",
            header_anchors=("工序号", "过程描述"),
            header_hit=2,
            header_compact_match=True,
            header_search_max_row=WHOLE_SHEET_ROWS,
            header_search_max_col=WHOLE_SHEET_COLS,
            row_anchors=("过程描述",),
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET_ROWS,
            row_stop_markers=STOP_MARKERS,
        ),
    ),
    (
        ColumnModel(canonical_name="工序号", section_name="流程区", aliases=("STP工序号", "步骤\nStep")),
        ColumnModel(canonical_name="过程描述", section_name="流程区", aliases=("OPERATION DESCRIPTION过程描述", "过程描述\nProcess description")),
        # DP4Z marks key operations in its own column.
        ColumnModel(canonical_name="标识", section_name="流程区", aliases=("标识Identification",)),
    ),
)

PDF_SPECS = (STITCHED, PER_PAGE)

# Activity symbols of the charts and their legends.
SYMBOLS = "◇○△□▽"
# Key operation marks named in the rule remarks: K, K1…, ★, ◆.
KEY_MARK = r"(?<![A-Za-z])K\d*(?![A-Za-z])|[★◆☆]"
# A legend entry naming the inspection symbol: "检验" (776, DP4Z), "检验 Inspection" (V040).
INSPECTION_NAME = r"检验|inspection"
# Operations that inspect or test the product (U16:2, clarified with the user).
INSPECTION_ACTIVITY = r"检验|检查|检测|测试|试验|气密|泄漏|inspect|check|test|GP12"
