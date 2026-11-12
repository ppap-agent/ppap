"""PFMEA (潜在过程失效模式及后果分析) originals (U07); aliases read from UAES corpus files."""

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

from . import PER_PAGE, STITCHED, WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

_AREA = "PFMEA失效区"

# The failure analysis table. The 776/A40 workbooks name their sheet 专用PFMEA; converted PDF
# pages (Table_*) are found by their header row. AIAG forms repeat S/O/D under 措施结果
# (Actions Results) one row lower; the scores nearest the header row are the analysis ones.
DOCUMENT = (
    (SheetModel(canonical_name="PFMEA", include_keywords=("pfmea",)),),
    (
        SectionModel(
            canonical_name=_AREA,
            sheet_name="PFMEA",
            header_anchors=("失效模式", "失效起因", "严重度", "频度", "探测度"),
            header_hit=4,
            header_search_max_row=WHOLE_SHEET_ROWS,
            header_search_max_col=WHOLE_SHEET_COLS,
            # A failure item is a row with any of its mode, cause or scores; every physical row is visited.
            row_anchors=("失效模式", "失效起因", "严重度", "频度", "探测度"),
            row_anchor_match="any",
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET_ROWS,
            # 776/A40 close the table with the legend of internal process characteristic symbols.
            row_stop_markers=("内部过程特性符号",),
        ),
    ),
    (
        # "Process/Function/ Requirements 工序/功能/要求" (776/A40), "项目 / 功能\nItem/Function" (V040),
        # "Process Step /\nFunction\n过程步骤 / 功能" (脱敏版).
        ColumnModel(canonical_name="功能", section_name=_AREA, aliases=("工序/功能/要求", "项目 / 功能", "过程步骤 / 功能")),
        ColumnModel(canonical_name="要求", section_name=_AREA, aliases=("Requirement\n要求",)),
        ColumnModel(canonical_name="失效模式", section_name=_AREA, aliases=("潜在的故障模式", "潜在失效模式")),
        ColumnModel(canonical_name="失效后果", section_name=_AREA, aliases=("潜在的故障影响", "潜在失效后果", "潜在失效影响")),
        ColumnModel(canonical_name="严重度", section_name=_AREA, aliases=("SEV严重度", "严重度", "Severity 严重度")),
        ColumnModel(canonical_name="类别", section_name=_AREA, aliases=("CLASS类别", "级别", "Classification 类分")),
        ColumnModel(canonical_name="失效起因", section_name=_AREA, aliases=("潜在的引起故障的机理", "潜在失效起因 / 机理", "潜在失效原因")),
        ColumnModel(canonical_name="频度", section_name=_AREA, aliases=("OCC频度", "频度", "Occurrence 频度")),
        ColumnModel(
            canonical_name="预防控制",
            section_name=_AREA,
            aliases=("Current Process Controls Prevention目前的预防控制", "预防\nPrevent", "Current Process Controls\nPrevention\n现行过程控制\n预防"),
        ),
        ColumnModel(
            canonical_name="探测控制",
            section_name=_AREA,
            aliases=("目前的探测控制", "探测\nDetect", "Current Process Controls\nDetection\n现行过程控制\n探 测"),
        ),
        ColumnModel(canonical_name="探测度", section_name=_AREA, aliases=("D\nE\nT\n探测度", "探测度", "Detection 测度探")),
    ),
)

PDF_SPECS = (STITCHED, PER_PAGE)

# Words that open the area after the failure items: a revision history ("改版理由和内容 Revision
# reason", "Revision Explaination 修改说明"), the symbol legend, or the signature row.
FOOTER_MARKERS = ("改版理由", "revision reason", "修改说明", "内部过程特性符号", "prepared 编制", "approved 批准")
