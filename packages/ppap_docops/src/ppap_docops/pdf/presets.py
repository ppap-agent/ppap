"""Built-in PDF extraction specs for common document templates."""

from __future__ import annotations

from .spec import (
    ColumnPolicy,
    CrossCellTextSplitScope,
    CrossCellTextSplitSpec,
    CrossCellTextSplitStrategy,
    ExtractionSpec,
    FailureMode,
    GroupingMode,
    GroupingSpec,
    OutputMode,
    OutputSpec,
    PdfSpec,
    SheetNameMode,
    StitchSpec,
    TitleSectionRule,
)

PPAP_4_CHECKING_FIXTURE_APPROVAL_REPORT_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(label="填写指南-检具清单", all_of=("项目检具清单", "检具号/检具名称可与理想主管tpm进行确认")),
            TitleSectionRule(label="填写指南-检具认可报告", all_of=("检 具 认 可 报 告", "gd&t图号处填写图")),
            TitleSectionRule(label="填写指南-检具制造认可检查表", all_of=("检具制造认可检查表-b表", "评审证据")),
            TitleSectionRule(label="检具清单", any_of=("项目检具清单",)),
            TitleSectionRule(label="检具认可报告", any_of=("检 具 认 可 报 告",)),
            TitleSectionRule(label="检具设计认可检查表-A表", all_of=("检具设计认可检查表", "a表")),
            TitleSectionRule(label="检具制造认可检查表-B表", all_of=("检具制造认可检查表", "b表")),
        ),
    ),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
    failure_mode=FailureMode.STRICT,
)

PPAP_1_DIMENSION_MEASUREMENT_REPORT_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(TitleSectionRule(label="主数据页", all_of=("全功能检验报告",)),),
    ),
    stitch=StitchSpec(
        drop_repeated_header=False,
        split_on_large_y_gap=False,
        column_policy=ColumnPolicy.PAD_TO_MAX,
        split_multirow_value_by_neighbor_labels=True,
        row_split_markers=("序号", "检查项目", "判定标准", "供方检查方法/检查工具", "检查数据", "判定结果"),
    ),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
)

PPAP_6_MSA_REPORT_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(label="汇总任务页", all_of=("理想汽车测量系统分析", "任务项一", "任务项二")),
            TitleSectionRule(label="测量系统分析计划", all_of=("测量系统分析计划", "测量次数/人", "计划日期", "实际测量日期")),
            TitleSectionRule(
                label="测量系统分析报告（R&R）填表指南",
                all_of=("量具重复性和再现性R&R分析报告", "97.17%", "97.32%"),
            ),
            TitleSectionRule(
                label="测量系统分析报告（R&R）仅供参考",
                all_of=("量具重复性和再现性R&R分析报告", "#DIV/0!"),
            ),
        ),
    ),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
)

PPAP_7_INITIAL_PROCESS_CAPACITY_STUDY_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(label="汇总任务页", all_of=("理想汽车过程能力分析", "任务项一", "过程能力分析报告")),
            TitleSectionRule(label="产品过程CCSC特性管理清单", all_of=("特性规格要求", "特性等级划分", "符合性", "客户要求", "功能要求")),
            TitleSectionRule(label="I-MR单值移动控制图", all_of=("I单值控制图信息", "MR移动极差控制图信息")),
            TitleSectionRule(label="Xbar-R控制图", all_of=("Xbar控制图信息", "R极差图信息")),
            TitleSectionRule(label="Xbar-S控制图", all_of=("Xbar控制图信息", "S标准差控制图信息")),
        ),
    ),
    extraction=ExtractionSpec(merge_tolerance=0.5),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
)

PPAP_9_PFMEA_REPORT_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(
                label="PFMEA",
                all_of=("公司名称", "车型项目", "零件名称", "零件号", "制造地址", "PFMEA开始日期", "PFMEA修订日期"),
            ),
            TitleSectionRule(label="变更履历", all_of=("变更履历", "修订日期")),
        ),
    ),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
)

PPAP_2_1_DVP_R_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(label="子计划 - 试验项目", all_of=("试验计划栏", "试验报告栏", "序号", "试验项目")),
            TitleSectionRule(label="DVP&R", all_of=("零部件设计验证计划和报告", "版本号", "修订日期")),
        ),
    ),
    extraction=ExtractionSpec(merge_tolerance=0.5),
    stitch=StitchSpec(drop_repeated_header=False),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
)

PPAP_2_2_DPVR_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(
                label="DPVR报告",
                any_of=("试 验 报 告", "试验报告", "TEST REPORT"),
                all_of=("编号",),
            ),
            TitleSectionRule(
                label="DPVR报告",
                any_of=("试验报告", "DVP试验报告", "PV试验报告", "检测报告"),
                all_of=("报告编号", "试验类型", "试验结果"),
            ),
            TitleSectionRule(
                label="DPVR报告",
                any_of=("试验报告", "DVP试验报告", "PV试验报告", "检测报告", "TEST REPORT"),
                all_of=("报告编号", "试验结果", "判定结果"),
            ),
            TitleSectionRule(
                label="DPVR报告",
                any_of=("TEST REPORT",),
                all_of=("Report No", "Conclusion"),
            ),
            TitleSectionRule(
                label="DPVR报告",
                any_of=("检测报告", "TEST REPORT"),
                all_of=("报告编号", "检测项目", "判定结论"),
            ),
        ),
    ),
    extraction=ExtractionSpec(
        merge_tolerance=0.5,
        cross_cell_text_split=CrossCellTextSplitSpec(
            enabled=True,
            scope=CrossCellTextSplitScope.WHOLE_TABLE,
            strategy=CrossCellTextSplitStrategy.WORDS_THEN_CHAR,
        ),
    ),
    stitch=StitchSpec(drop_repeated_header=False),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
    failure_mode=FailureMode.STRICT,
)

PPAP_3_SUB_SUPPLIER_LIST_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(label="更新履历", all_of=("文件更新履历", "更新内容描述", "修订日期")),
            TitleSectionRule(
                label="主清单",
                any_of=(
                    "材料 、 产品及分供方清单",
                    "材料产品及分供方清单",
                    "材料及分供方清单",
                    "material product and subcontractor list",
                    "material and subcontractor list",
                ),
                restart_all_of=(
                    "表单编号",
                    "零件 / 分部件零件号",
                ),
            ),
        ),
    ),
    extraction=ExtractionSpec(
        cross_cell_text_split=CrossCellTextSplitSpec(
            enabled=True,
            scope=CrossCellTextSplitScope.WHOLE_TABLE,
            strategy=CrossCellTextSplitStrategy.WORDS_THEN_CHAR,
        )
    ),
    stitch=StitchSpec(
        drop_repeated_header=False,
    ),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
)

PPAP_5_TOOLING_APPROVAL_REPORT_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(label="更新履历", all_of=("文件更新履历", "更新内容描述", "修订日期")),
            TitleSectionRule(
                label="模具验收报告",
                any_of=("理想汽车模具验收报告", "模具验收报告"),
            ),
        ),
    ),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
)

PPAP_12_EQUIPMENT_LIST_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(
                label="设备开发验收清单",
                any_of=("理想项目设备开发/验收清单", "设备开发/验收清单"),
            ),
        ),
    ),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
)

PPAP_13_PLANT_CAPACITY_EVALUATION_REPORT_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(
                label="table_产能测试及评估",
                any_of=("产能测试及评估", "零件产能评估表"),
            ),
        ),
    ),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
)

PPAP_14_XIR_ASSEMBLY_ISSUE_REPORT_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(
                label="项目问题清单汇总",
                any_of=("APQP项目问题清单", "项目问题清单汇总"),
            ),
        ),
    ),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
)

PPAP_15_ANNUAL_TEST_REQUIREMENTS_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(
                label="表格",
                any_of=("零部件年度试验要求",),
            ),
        ),
    ),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
)

PPAP_16_PSW_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(
                label="生产件批准交付物文件清单",
                any_of=("生产件批准交付物文件清单", "the list of ppap document"),
            ),
            TitleSectionRule(
                label="零件提交保证书",
                any_of=("零件提交保证书", "part submission warrant", "psw #:"),
            ),
            TitleSectionRule(
                label="部门章授权声明",
                any_of=("部门章授权声明",),
            ),
        ),
    ),
    output=OutputSpec(
        mode=OutputMode.SHEET_PER_GROUP,
        sheet_name_mode=SheetNameMode.GROUP_LABEL,
        preserve_pdf_embedded_images=True,
    ),
)

PPAP_8_PROCESS_FLOW_CHART_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(label="更新履历", all_of=("文件更新履历", "更新内容描述", "修订日期")),
            # 指南页在部分样本中跨两页，锚定第一页并让后一页按 unmatched 规则并入同组。
            TitleSectionRule(label="填写指南", all_of=("过程流程图process flow cha", "1、工序号基于")),
            TitleSectionRule(
                label="过程流程图",
                all_of=("工序号", "工序名称", "制造", "运输", "存储", "检验", "返工", "报废"),
            ),
        ),
    ),
    stitch=StitchSpec(
        split_on_large_y_gap=False,
        column_policy=ColumnPolicy.PAD_TO_MAX,
        split_multirow_value_by_neighbor_labels=True,
    ),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
)

PPAP_11_PROCESS_SPECIAL_CHARACTERISTIC_LIST_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(
                label="变更履历",
                any_of=("变更履历", "文件更新履历", "更改内容"),
                all_of=("版本",),
            ),
            TitleSectionRule(label="主驾驶特性管理清单", any_of=("主驾驶特性管理清单",)),
            TitleSectionRule(label="副驾驶特性管理清单", any_of=("副驾驶特性管理清单",)),
            TitleSectionRule(label="填写说明", all_of=("填写说明", "表格使用目的")),
            TitleSectionRule(
                label="特性清单",
                any_of=("零件CCSC特性管理清单", "特性管理清单", "特性清单"),
            ),
        ),
    ),
    extraction=ExtractionSpec(
        merge_tolerance=0.5,
        cross_cell_text_split=CrossCellTextSplitSpec(
            enabled=True,
            scope=CrossCellTextSplitScope.WHOLE_TABLE,
            strategy=CrossCellTextSplitStrategy.WORDS_THEN_CHAR,
        ),
    ),
    stitch=StitchSpec(split_on_large_y_gap=False, column_policy=ColumnPolicy.PAD_TO_MAX),
    output=OutputSpec(mode=OutputMode.SHEET_PER_GROUP, sheet_name_mode=SheetNameMode.GROUP_LABEL),
)

__all__ = [
    "PPAP_1_DIMENSION_MEASUREMENT_REPORT_SPEC",
    "PPAP_2_1_DVP_R_SPEC",
    "PPAP_2_2_DPVR_SPEC",
    "PPAP_3_SUB_SUPPLIER_LIST_SPEC",
    "PPAP_5_TOOLING_APPROVAL_REPORT_SPEC",
    "PPAP_12_EQUIPMENT_LIST_SPEC",
    "PPAP_13_PLANT_CAPACITY_EVALUATION_REPORT_SPEC",
    "PPAP_14_XIR_ASSEMBLY_ISSUE_REPORT_SPEC",
    "PPAP_15_ANNUAL_TEST_REQUIREMENTS_SPEC",
    "PPAP_16_PSW_SPEC",
    "PPAP_8_PROCESS_FLOW_CHART_SPEC",
    "PPAP_6_MSA_REPORT_SPEC",
    "PPAP_4_CHECKING_FIXTURE_APPROVAL_REPORT_SPEC",
    "PPAP_7_INITIAL_PROCESS_CAPACITY_STUDY_SPEC",
    "PPAP_9_PFMEA_REPORT_SPEC",
    "PPAP_11_PROCESS_SPECIAL_CHARACTERISTIC_LIST_SPEC",
]
