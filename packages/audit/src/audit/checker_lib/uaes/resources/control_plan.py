"""Control plan originals (U08 and its counterparts); aliases read from UAES corpus files."""

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

from . import PER_PAGE, STITCHED, WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

# Control plan: 776/A40 (multi-row AIAG header), CPO-45, V040 (PDF).
DOCUMENT = (
    (SheetModel(canonical_name="控制计划", include_keywords=("cp", "控制计划", "control plan", "sheet1", "table_")),),
    (
        SectionModel(
            header_search_max_row=WHOLE_SHEET_ROWS,
            header_search_max_col=WHOLE_SHEET_COLS,
            canonical_name="控制计划区",
            sheet_name="控制计划",
            header_anchors=("工序名称", "评价测量技术"),
            header_hit=2,
            row_anchors=("评价测量技术",),
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET_ROWS,
            # An operation is named on its first row only (776 operation 150).
            row_carry_columns=("工序名称",),
        ),
    ),
    (
        ColumnModel(canonical_name="工序名称", section_name="控制计划区", aliases=("工序名称/操作描述", "过程名称操作描述", "过程名称/操作描述")),
        # 776/A40 head it "EVALUATION/ MEASUREMENT TECHNIQUE" over three rows with 量具/编号 below.
        ColumnModel(canonical_name="评价测量技术", section_name="控制计划区", aliases=("评价/测量技术", "量具/编号", "评价测量技术")),
        # 设备/工装 (776/A40), 机器、装置、夹具、工装 (CPO-45), "生产设备\ndevice\njig" (V040).
        ColumnModel(canonical_name="CP生产资源", section_name="控制计划区", aliases=("设备/工装", "机器、装置、夹具、工装", "生产设备")),
        # 776/A40 number every characteristic of an operation (序号); CPO-45 and V040 head it 编号.
        ColumnModel(canonical_name="CP特性编号", section_name="控制计划区", aliases=("序号", "编号")),
        ColumnModel(canonical_name="CP产品特性", section_name="控制计划区", aliases=("产品特性", "产品", "产品(Production)")),
        ColumnModel(canonical_name="CP过程特性", section_name="控制计划区", aliases=("过程特性", "过程", "过程(Process)")),
        # 特殊特性 (776/A40), 特殊特性分类 (CPO-45), "特殊/特性/\n分类\n(Special\nChar.\nClass)" (V040).
        ColumnModel(canonical_name="CP特殊特性", section_name="控制计划区", aliases=("特殊特性", "特殊特性分类", "特殊/特性/")),
        # "TOLERANCE 规格/描述" (776/A40), 产品/过程规格/公差 (CPO-45), 产品/过程规范/公差 (V040).
        ColumnModel(canonical_name="CP规格公差", section_name="控制计划区", aliases=("规格/描述", "产品/过程规格/公差", "产品/过程规范/公差")),
        ColumnModel(canonical_name="CP频率", section_name="控制计划区", aliases=("频率",)),
        # CPO-45 writes "控制\n方法".
        ColumnModel(canonical_name="CP控制方法", section_name="控制计划区", aliases=("控制方法", "控制\n方法")),
    ),
)

# A control plan is one table continued over its pages (V040 is four pages).
PDF_SPECS = (STITCHED, PER_PAGE)

# Header labels of the identity fields U08:7 names, as written in the originals.
SUPPLIER_NAME = ("供应商/工厂", "供方/工厂", "供应商名称")
SUPPLIER_CODE = ("供应商代码", "供方代号", "供应商代号")
PREPARER = ("编制人", "编制")
VERSION = ("版本号", "版本")
DATES = ("受控日期", "创建日期", "修订日期", "修改日期", "日期(编制)", "日期(修订)", "首次编制日期", "最近修订日期")
PART_NAME = ("零件名称", "产品名称", "零件名称/描述", "产品名称/描述")
PART_NUMBER = ("零件号",)
