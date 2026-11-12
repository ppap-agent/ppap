"""Special product/process characteristic lists (U12, read by U07:3); aliases read from UAES corpus files."""

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

from . import PER_PAGE, STITCHED, WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

_AREA = "特殊特性区"

# The 776/777/A40 lists sit on a sheet named 特殊特性清单; the V040 PDF page is found by its header row.
DOCUMENT = (
    (SheetModel(canonical_name="特殊特性清单", include_keywords=("特殊特性",)),),
    (
        SectionModel(
            canonical_name=_AREA,
            sheet_name="特殊特性清单",
            header_anchors=("特性编号", "特性名称", "特性值", "特性类别"),
            header_hit=2,
            header_search_max_row=WHOLE_SHEET_ROWS,
            header_search_max_col=WHOLE_SHEET_COLS,
            # A characteristic row is any row that numbers, names or values a characteristic.
            row_anchors=("特性编号", "特性名称", "特性值"),
            row_anchor_match="any",
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET_ROWS,
            row_stop_markers=("内部过程特性符号", "编制/日期", "审核/日期", "编制人", "批准人"),
        ),
    ),
    (
        ColumnModel(canonical_name="特性编号", section_name=_AREA, aliases=("序号", "编号")),
        # "产品名称/零件号\nName / Code": each row names the part it belongs to.
        ColumnModel(canonical_name="零件", section_name=_AREA, aliases=("产品名称/零件号",)),
        # "特殊特性名称 \nSPPC Name " (776/777/A40); "产品特殊特性\nProduct special characteristic" (V040).
        ColumnModel(canonical_name="特性名称", section_name=_AREA, aliases=("特殊特性名称", "产品特殊特性", "产品特性", "过程特性")),
        ColumnModel(canonical_name="过程特性名称", section_name=_AREA, aliases=("过程特殊特性",)),
        ColumnModel(canonical_name="特性值", section_name=_AREA, aliases=("特性值", "特性及公差")),
        # "特性类别\nType"; V040 marks each characteristic under "特殊特性符号\nSpecial characteristic\nsymbol".
        ColumnModel(canonical_name="特性类别", section_name=_AREA, aliases=("特性类别", "特性类型", "特殊特性符号")),
    ),
)

PDF_SPECS = (STITCHED, PER_PAGE)

# Header labels of a list that states its part once ("产品名称\nPart Name", "产品件号\nPart No.").
PART_NAME = ("产品名称\nPart Name", "物料名称", "零件名称")
PART_NUMBER = ("产品件号\nPart No.", "物料编号", "零件号", "零件编号")
