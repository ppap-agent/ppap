"""Process capability study originals (U06); aliases read from UAES corpus files."""

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

from . import PER_PAGE, STITCHED, WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

_WHOLE = dict(header_search_max_row=WHOLE_SHEET_ROWS, header_search_max_col=WHOLE_SHEET_COLS, row_stop_on_first_blank=False, row_max_blank_streak=WHOLE_SHEET_ROWS)

# A capability study: one certification report per characteristic sheet (776, A40, V040), a 目录
# sheet that lists every characteristic with its CPK/PPK result and target (776, A40), and in the
# 574 PDF a "Ppk能力值汇总" page followed by one report page per characteristic.
DOCUMENT = (
    (
        SheetModel(canonical_name="能力目录", include_keywords=("目录",)),
        # Found by name (目录) or by its result/target header row (574 page 1, "Ppk能力值汇总").
        SheetModel(canonical_name="能力汇总表", include_keywords=("目录", "汇总")),
        # Report sheets are named after their characteristic ("2#电机舱直径", "14.2( 0 -0.1)") and are
        # found by their statistics table (xlsx) or their measured-readings grid (574 PDF).
        SheetModel(canonical_name="能力报告", include_keywords=("能力认证报告",)),
    ),
    (
        SectionModel(
            canonical_name="能力汇总区",
            sheet_name="能力汇总表",
            header_anchors=("目标CPK", "结果CPK", "目标PPK", "结果PPK"),
            header_hit=2,
            # Rows are the named characteristics; a list without a name column is read by its results.
            row_anchors=("能力特性", "结果PPK", "结果CPK"),
            row_stop_markers=("编制/日期", "审核/日期", "编制人", "批准人"),
            **_WHOLE,
        ),
        # "DESCRIPTIVE   STATISTICS | VALUES": a label column and its value column.
        SectionModel(canonical_name="能力统计区", sheet_name="能力报告", header_anchors=("统计项", "统计值"), header_hit=2, row_anchors=("统计项",), **_WHOLE),
        # 574 report pages: "team | No.1 … No.25 | average" over the sample rows.
        SectionModel(canonical_name="实测数据区", sheet_name="能力报告", header_anchors=("组号", "第1组"), header_hit=2, row_anchors=("组号",), **_WHOLE),
    ),
    (
        # 776/A40 head it "工程名"; the 574 summary "尺寸编号".
        ColumnModel(canonical_name="能力特性", section_name="能力汇总区", aliases=("工程名", "尺寸编号")),
        ColumnModel(canonical_name="目标CPK", section_name="能力汇总区", aliases=("目标\n(CPK)",)),
        ColumnModel(canonical_name="结果CPK", section_name="能力汇总区", aliases=("结果\n(CPK)",)),
        ColumnModel(canonical_name="目标PPK", section_name="能力汇总区", aliases=("目标\n(PPK)", "客户要求能力值")),
        ColumnModel(canonical_name="结果PPK", section_name="能力汇总区", aliases=("结果\n(PPK)", "实测能力值")),
        ColumnModel(canonical_name="反应计划", section_name="能力汇总区", aliases=("反应计划",)),
        ColumnModel(canonical_name="汇总备注", section_name="能力汇总区", aliases=("备注",)),
        # "特别特性" over the two mark columns "产品" and "过程" (776/A40 目录 row 6).
        ColumnModel(canonical_name="过程特性标记", section_name="能力汇总区", aliases=("过程",)),
        ColumnModel(canonical_name="统计项", section_name="能力统计区", aliases=("DESCRIPTIVE   STATISTICS", "統計値 DESCRIPTIVE   STATISTICS")),
        ColumnModel(canonical_name="统计值", section_name="能力统计区", aliases=("VALUES",)),
        ColumnModel(canonical_name="组号", section_name="实测数据区", aliases=("team",)),
        ColumnModel(canonical_name="第1组", section_name="实测数据区", aliases=("No.1",)),
    ),
)

# The 574 PDF reads as two tables: the summary page and the report pages stacked.
PDF_SPECS = (STITCHED, PER_PAGE)

# Statistics labels of a certification report (776/A40, then V040's bilingual spelling).
READINGS = ("Number of readings", "データ数 Number of readings")
CPK = ("Process Capability  (Cpk)", "工程能力指数 Process Capability (Cpk)")
PPK = ("Performance index (Ppk)", "工程性能指数 (Ppk) Performance index")
# 574 report pages state their indices outside the ruled table, in the text layer.
PDF_CPK, PDF_PPK = "CPK=", "PpK="
# Measured rows of a 574 readings grid start "sample 1"; the control-limit rows that follow do not.
SAMPLE_ROW = "sample"
# A No.k subgroup column of the readings grid.
SUBGROUP = r"No\.\d+"
DIRECTORY_PART = ("零件号码",)


# The PGQP1 "2.Special Process关键工序" list (No. | Process/工艺 | Description/描述), read with the
# shared PGQP1 sheet of `pgqp1`.
PGQP1_PROCESS = (
    (),
    (
        SectionModel(
            canonical_name="关键工序区",
            sheet_name="PGQP1",
            header_anchors=("关键工艺", "工艺描述"),
            header_hit=2,
            row_anchors=("关键工艺",),
            row_stop_markers=("3.Special requirements",),
            **_WHOLE,
        ),
    ),
    (
        ColumnModel(canonical_name="关键工艺", section_name="关键工序区", aliases=("Process/工艺",)),
        ColumnModel(canonical_name="工艺描述", section_name="关键工序区", aliases=("Description/描述",)),
    ),
)
PGQP1_PART = ("Part No/零件号:",)
