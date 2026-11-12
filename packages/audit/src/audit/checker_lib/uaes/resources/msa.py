"""Measurement system analysis originals (U05); aliases read from UAES corpus files."""

from documents.pdf import GroupingMode, GroupingSpec, OutputMode, OutputSpec, PdfSpec, SheetNameMode, TitleSectionRule

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

from . import PER_PAGE, STITCHED, WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

# Measurement system analysis: the plan, GR&R analysis reports and Kappa (attribute) studies.
DOCUMENT = (
    (
        # The 776 plans sit on a sheet named 目录 and are found by their header row instead.
        SheetModel(canonical_name="MSA计划表", include_keywords=("测量系统分析计划", "msa计划")),
        # 量具重复性和再现性分析表 (776), 双性分析 (V040); their 数据表/双性数据 sheets hold raw readings.
        SheetModel(canonical_name="GRR报告", include_keywords=("重复性和再现性分析", "重复性与再现性分析", "重复性及再现性分析报告", "双性分析"), exclude_keywords=("数据",)),
        # GP12检验人员 Kappa分析 (776); sheet1/Sheet1 attribute studies are found by their A-1/B-1 trial columns.
        SheetModel(canonical_name="Kappa报告", include_keywords=("kappa",), exclude_keywords=("scratch",)),
    ),
    (
        SectionModel(
            header_search_max_row=WHOLE_SHEET_ROWS,
            header_search_max_col=WHOLE_SHEET_COLS,
            canonical_name="MSA计划区",
            sheet_name="MSA计划表",
            header_anchors=("序号", "量具名称", "计划日期"),
            header_hit=2,
            # Found by content (the 776 plans on a sheet named 目录), a plan also carries its dates;
            # a 计数型MSA sheet has 序号 and 量具名称 too.
            sheet_identity_anchors=("计划日期",),
            row_anchors=("序号",),
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET_ROWS,
            row_stop_markers=("编制/日期", "审核/日期", "编制人", "批准人"),
        ),
        SectionModel(
            canonical_name="Kappa试验区",
            sheet_name="Kappa报告",
            header_anchors=("A-1", "B-1"),
            header_hit=2,
            row_anchors=("A-1",),
            header_search_max_row=WHOLE_SHEET_ROWS,
            header_search_max_col=WHOLE_SHEET_COLS,
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET_ROWS,
        ),
    ),
    (
        ColumnModel(canonical_name="序号", section_name="MSA计划区", aliases=("序号", "NO", "No.")),
        ColumnModel(canonical_name="量具名称", section_name="MSA计划区", aliases=("测量工具名称", "量具名称", "测量设备名称")),
        ColumnModel(canonical_name="量具编号", section_name="MSA计划区", aliases=("工具编号", "量具编号", "测量设备编号")),
        ColumnModel(canonical_name="计划日期", section_name="MSA计划区", aliases=("计划开始日期", "计划完成日期", "计划日期")),
        ColumnModel(canonical_name="A-1", section_name="Kappa试验区", aliases=("A-1",)),
        ColumnModel(canonical_name="B-1", section_name="Kappa试验区", aliases=("B-1",)),
    ),
)


# 574/527 MSA PDF: one titled report per page group; plan pages carry no text layer.
PDF_SPECS = (
    PdfSpec(
        grouping=GroupingSpec(
            mode=GroupingMode.TITLE_SECTIONS,
            section_rules=(
                TitleSectionRule(label="测量系统分析计划", all_of=("测量系统分析计划",)),
                TitleSectionRule(label="重复性及再现性分析报告", all_of=("重复性及再现性分析报告",)),
                TitleSectionRule(label="重复性及再现性分析数据", all_of=("重复性及再现性分析数据记录表",)),
                TitleSectionRule(label="重复性及再现性分析控制图", all_of=("均值极差控制图",)),
                TitleSectionRule(label="Kappa Attribute Report", all_of=("MSA—Attribute Report",)),
            ),
        ),
        output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
    ),
    STITCHED,
    PER_PAGE,
)

# Report header labels, as written beside their values.
GAUGE_NAME = ("量具名称", "Measure name", "gauge name 量具名称", "Applicable Gage", "Gage Name")
GAUGE_NUMBER = ("量具编号", "Measure no", "量具编号 gauge No.", "Gage Number", "Gage No")
GRR_VERDICT = ("判定",)
# Each appraiser's conclusion row of a Kappa study (written without a colon).
KAPPA_CONCLUSION = ("Conclusion", "Conclusion结论")
# The closing conclusion of a 计数型MSA sheet without appraiser rows.
ATTRIBUTE_CONCLUSION = ("结论：Conclusion",)
PART_NUMBER = ("零件号", "Part Number零件号", "Product number", "零件代号")
