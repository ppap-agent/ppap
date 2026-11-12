"""Process flow charts as U08 reads them: their steps and marked characteristics; aliases from UAES corpus files."""

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

from . import PER_PAGE, STITCHED, WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

# 流程图776/A40 ("PFD (2)"), 5、Flow chart磁性罩.pdf, 过程流程图.xls ("P0").
DOCUMENT = (
    (SheetModel(canonical_name="U08流程图", include_keywords=("pfd", "流程图", "flow", "p0", "table_", "page")),),
    (
        SectionModel(
            canonical_name="U08流程区",
            sheet_name="U08流程图",
            header_anchors=("U08过程描述", "U08流程产品特性", "U08流程特殊特性"),
            header_hit=2,
            header_search_max_row=WHOLE_SHEET_ROWS,
            header_search_max_col=WHOLE_SHEET_COLS,
            row_anchors=("U08过程描述",),
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET_ROWS,
            row_stop_markers=("注释Notes", "备注："),
        ),
    ),
    (
        # "OPERATION DESCRIPTION过程描述" (776/A40), "过程描述\nProcess description" (V040, .xls).
        ColumnModel(canonical_name="U08过程描述", section_name="U08流程区", aliases=("OPERATION DESCRIPTION过程描述", "过程描述")),
        ColumnModel(canonical_name="U08流程产品特性", section_name="U08流程区", aliases=("产品特性",)),
        ColumnModel(canonical_name="U08流程过程特性", section_name="U08流程区", aliases=("PROCESS CHARACTERISTICS过程特性", "过程特性")),
        # "特殊特性 Special characteristics" (.xls); V040 heads its mark columns "符号\nSymbol".
        ColumnModel(canonical_name="U08流程特殊特性", section_name="U08流程区", aliases=("特殊特性", "符号")),
    ),
)

PDF_SPECS = (STITCHED, PER_PAGE)
