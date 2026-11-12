"""Control plan originals (U08 and its counterparts); aliases read from UAES corpus files."""

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

from . import PER_PAGE, STITCHED

# Control plan: 776/A40 (multi-row AIAG header), CPO-45, V040 (PDF).
DOCUMENT = (
    (SheetModel(canonical_name="控制计划", include_keywords=("cp", "控制计划", "control plan", "sheet1", "table_")),),
    (
        SectionModel(
            canonical_name="控制计划区",
            sheet_name="控制计划",
            header_anchors=("工序名称", "评价测量技术"),
            header_hit=2,
            header_search_max_row=60,
            row_anchors=("评价测量技术",),
            row_stop_on_first_blank=False,
            row_max_blank_streak=20,
            # An operation is named on its first row only (776 operation 150).
            row_carry_columns=("工序名称",),
        ),
    ),
    (
        ColumnModel(canonical_name="工序名称", section_name="控制计划区", aliases=("工序名称/操作描述", "过程名称操作描述", "过程名称/操作描述")),
        # 776/A40 head it "EVALUATION/ MEASUREMENT TECHNIQUE" over three rows with 量具/编号 below.
        ColumnModel(canonical_name="评价测量技术", section_name="控制计划区", aliases=("评价/测量技术", "量具/编号", "评价测量技术")),
    ),
)

# A control plan is one table continued over its pages (V040 is four pages).
PDF_SPECS = (STITCHED, PER_PAGE)
