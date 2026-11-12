"""The PGQP1 areas U08 reads beyond the shared declaration; aliases from UAES corpus files."""

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel

from . import WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

# 2. Special Process 关键工序 (776 PGQP1 rows 26-34): "Process/工艺 | Description/描述".
DOCUMENT = (
    (),
    (
        SectionModel(
            canonical_name="U08关键工序区",
            sheet_name="PGQP1",
            header_anchors=("U08PGQP1工艺", "U08PGQP1工艺描述"),
            header_hit=2,
            header_search_max_row=WHOLE_SHEET_ROWS,
            header_search_max_col=WHOLE_SHEET_COLS,
            row_anchors=("U08PGQP1工艺",),
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET_ROWS,
            row_stop_markers=("3.Special requirements", "特殊要求"),
        ),
    ),
    (
        ColumnModel(canonical_name="U08PGQP1工艺", section_name="U08关键工序区", aliases=("Process/工艺",)),
        ColumnModel(canonical_name="U08PGQP1工艺描述", section_name="U08关键工序区", aliases=("Description/描述",)),
    ),
)

# The product audit list (5. Product Audit Requirements/产品审核要求) shares its "Items/项目 |
# Content discription/内容描述" heading with 3. Special requirements, so it is found by its title.
PRODUCT_AUDIT_TITLE = ("Product Audit Requirements", "产品审核要求")
PRODUCT_AUDIT_ITEM = ("Items/项目",)
PRODUCT_AUDIT_CONTENT = ("Content discription/内容描述",)
TYPE_TEST = ("型式试验", "type test")
