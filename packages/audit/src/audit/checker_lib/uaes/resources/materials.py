"""Material and subcontractor list originals (U04); aliases read from UAES corpus files."""

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

from . import PER_PAGE, STITCHED, WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

# The three sign-off lines below the table.
SIGNATURES = ("供应商负责工程师签字", "UAES NE工程师签字", "UAES SQD/SQE工程师签字")

# "68501项目 材料及分供方清单", "XXX项目 材料及分供方清单".
DOCUMENT = (
    (SheetModel(canonical_name="材料清单", include_keywords=("材料及分供方清单",)),),
    (
        SectionModel(
            canonical_name="材料清单区",
            sheet_name="材料清单",
            # The header runs over three rows (6-8): 序号 and 零件可视图 are merged down them.
            header_anchors=("材料序号", "零件可视图", "材料名称和牌号"),
            header_hit=2,
            header_search_max_row=WHOLE_SHEET_ROWS,
            header_search_max_col=WHOLE_SHEET_COLS,
            row_anchors=("材料序号",),
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET_ROWS,
            row_stop_markers=SIGNATURES,
        ),
    ),
    (
        ColumnModel(canonical_name="材料序号", section_name="材料清单区", aliases=("序号",)),
        ColumnModel(canonical_name="零件可视图", section_name="材料清单区", aliases=("零件可视图",)),
        ColumnModel(canonical_name="材料名称和牌号", section_name="材料清单区", aliases=("材料名称和材料牌号",)),
    ),
)

PDF_SPECS = (STITCHED, PER_PAGE)

# Header fields above the table, each label written as "中文:\nEnglish:" with its value in the next cell.
PROJECT = ("UAES项目编号：\nUAES Project No.:",)
PART_NAME = ("零件名称:\nPart Name:",)
IMDS = ("IMDS 表单ID号：\nIMDS data sheet ID:",)
PART_NUMBER = ("零件号:\nPart No:",)
HEADER_FIELDS = {"UAES项目编号": PROJECT, "零件名称": PART_NAME, "IMDS表单ID号": IMDS, "零件号": PART_NUMBER}

# One choice spread over several columns: an assembly level 1-5, and a component status of
# 自制 / 外协(国产, 进口) / 指定件. 完成PPAP under 部件状态 is a Y/N field of its own.
CHOICE_GROUPS = ("部件装配级别", "部件状态")
OWN_FIELDS = ("完成PPAP",)
PICTURE = "零件可视图"
