"""Simple UAES records (U02, U03, U09, U10/U14, U11, U15, U18); spellings read from UAES originals.

Each record is a namespace with its DOCUMENT (sheets, sections, columns) and PDF_SPECS, and
the literal words its clause names. Free-form records declare no table: their clauses read
words and labelled values, and a PDF is read page by page.
"""

from types import SimpleNamespace

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

from . import PER_PAGE, STITCHED, WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

NO_TABLE = ((), (), ())

# 样品阶段履历 (U11): the 工程更改一览表 of F01RD0YA40 sits on "Sheet1" and is found by its header row.
ENGINEERING_HISTORY = SimpleNamespace(
    DOCUMENT=(
        (SheetModel(canonical_name="工程更改一览表", include_keywords=("工程更改",)),),
        (
            SectionModel(
                canonical_name="工程更改区",
                sheet_name="工程更改一览表",
                header_anchors=("项目", "零件号", "变更内容", "变更原因", "时间"),
                header_hit=3,
                header_search_max_row=WHOLE_SHEET_ROWS,
                header_search_max_col=WHOLE_SHEET_COLS,
                row_anchors=("零件号",),
                row_stop_on_first_blank=False,
                row_max_blank_streak=WHOLE_SHEET_ROWS,
            ),
        ),
        (
            # F01RD0YA40: 项目 | 件号 | 变更内容简述 | 初始版本图纸时间 | 变更版本图纸时间 | 更改原因.
            # The other spellings are the business aliases the clause allows (意思相近).
            ColumnModel(canonical_name="项目", section_name="工程更改区", aliases=("项目", "项目名称", "项目代号")),
            ColumnModel(canonical_name="零件号", section_name="工程更改区", aliases=("零件号", "件号", "物料编号", "零件编号")),
            ColumnModel(canonical_name="变更内容", section_name="工程更改区", aliases=("变更内容简述", "变更内容", "更改内容")),
            ColumnModel(canonical_name="变更原因", section_name="工程更改区", aliases=("变更原因", "更改原因")),
            ColumnModel(canonical_name="时间", section_name="工程更改区", aliases=("时间", "初始版本图纸时间", "变更版本图纸时间", "变更日期", "更改日期", "日期")),
        ),
    ),
    PDF_SPECS=(STITCHED, PER_PAGE),
)
HISTORY_TITLE = "工程更改一览表"

# 零件追溯检查单 (U18): sheet 零件追溯清单 titled "Supplier Component Traceability\n供应商零件追溯",
# with numbered area headings ("1. 基本信息" ... "5. 追溯状态总结").
TRACEABILITY = SimpleNamespace(DOCUMENT=NO_TABLE, PDF_SPECS=(STITCHED, PER_PAGE))
TRACEABILITY_TITLE = "供应商零件追溯"
# Each area with the template's own labels and hints; text left after removing them is content.
TRACEABILITY_AREAS = {
    "基本信息": (
        "供应商",
        "零件名称",
        "零件号码",
        "事业部",
        "项目名称",
        "工厂",
        "物料类型",
        "零件等级",
        "追溯等级",
        "零件照片",
        "本体追溯说明",
        "二维码、钢印、标签 …",
    ),
    "分供方追溯": (
        "子零件名称",
        "子零件供应商",
        "是否有PTC特性？",
        "追溯等级\n（单件追溯；批次追溯）",
        "可追溯的信息\n（描述可追溯的内容，可增加附件）",
        "最小可追溯数量",
        "追溯风险评估",
        "子零件",
        "…",
    ),
    "生产过程追溯": (
        "工序名称",
        "是否有关键特性？\n(PGQP1定义的关键特性)",
        "追溯方法",
        "追溯内容",
        "检测数据/关键参数是否\n上传MES系统？",
        "最小可追溯数量",
        "追溯风险评估",
    ),
    "交付追溯": ("最小包装标签", "包装箱标签", "托盘标签", "照片", "生产批次", "供应商发货批次与联电看板批次关联说明"),
    "追溯状态总结": ("优点", "弱点", "后续计划"),
}

# Free-form records: no table, the PDF text of each page.
TEXT = SimpleNamespace(DOCUMENT=NO_TABLE, PDF_SPECS=(PER_PAGE,))

# 包装方案 (U09): the title and the six content blocks the clause names.
PACKAGING_TITLE = "包装方案"
PACKAGING_BLOCKS = ("零件基础信息", "供应商基础信息", "包装设计的基本要求", "针对该零件包装的特殊要求", "包装方案信息", "包装材料清单")

# 试装单 (U15): its three areas, the two flow states, and the two selections.
TRIAL_AREAS = ("流转记录", "试装结论", "检验结论")
TRIAL_FLOW = ("流程结束", "流程完成")
TRIAL_CHOICES = (("试装结论", "通过", "未通过"), ("检验结论", "合格", "不合格"))
# Marks that select an option when written right before or after it.
SELECTED = "☑✓√✔■"

# 实验室资质 (U03): the accreditation body, its mark, and the certificate deadline labels.
LAB_BODY = "中国合格评定国家认可委员会"
LAB_MARK = "CNAS"
LAB_DEADLINE = ("截止日期", "有效期至", "有效期截止日期", "证书有效期至")
LAB_REPORT_NAMES = ("试验报告", "验证报告", "DVR", "DV报告", "PV报告")

# 测量比较报告 (U02, F-PU-166): the report title and the four sign-off labels on one row at its foot
# ("供应商\nSupplier", "UAES技术中心\nUAES engineering department", ...).
COMPARISON_TITLE = "测量比较报告"
COMPARISON_SIGNERS = ("供应商", "UAES技术中心", "UAES测量部门", "UAES供应商质量开发工程师")
