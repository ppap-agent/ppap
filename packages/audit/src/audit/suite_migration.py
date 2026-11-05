"""One-time historical source conversion; runtime never consults old routes.

The explicit identities below were transcribed from the maintained baseline.
Each document type has one Suite key across carriers and historical revisions.
No user clause acquires a binding by position, title, type, or historical tier.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from audit.standard_suites import StandardSuite, _json

_BUILTIN_SOURCES = {
    ("v2", "材料产品及分供清单"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.sub_supplier_list_excel_agent:SubSupplierListExcelAgent",
            ".xls": "audit.checkers.v2.sub_supplier_list_excel_agent:SubSupplierListExcelAgent",
            ".xlsm": "audit.checkers.v2.sub_supplier_list_excel_agent:SubSupplierListExcelAgent",
            ".pdf": "audit.checkers.v2.pdf_excel_delegator_agent:SubSupplierListPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "单件重量列存在性和数据格式检查"),
            ("2", "rule_2", "材料信息列存在性和数据完整性检查"),
            ("3", "rule_3", "部件状态列存在性和数据唯一性检查"),
            ("4", "rule_4", "PPAP完成状态列存在性和数据关联性检查"),
        ],
    },
    ("v2", "过程流程图"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.process_flow_chart_excel_agent:ProcessFlowChartExcelAgent",
            ".xls": "audit.checkers.v2.process_flow_chart_excel_agent:ProcessFlowChartExcelAgent",
            ".xlsm": "audit.checkers.v2.process_flow_chart_excel_agent:ProcessFlowChartExcelAgent",
            ".pdf": "audit.checkers.v2.pdf_excel_delegator_agent:ProcessFlowChartPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "表头关键字段完整性检查"),
            ("2", "rule_2", "产品特殊特性检查"),
            ("3", "rule_3", "过程特殊特性检查"),
            ("4", "rule_4", "工序名称与操作描述检查"),
            ("5", "rule_5", "工序定义完整性检查"),
            ("6", "rule_6", "工序定义完整性检查（连线）"),
        ],
    },
    ("v2", "控制计划"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.control_plan_agent:ControlPlanAgent",
            ".xls": "audit.checkers.v2.control_plan_agent:ControlPlanAgent",
            ".xlsm": "audit.checkers.v2.control_plan_agent:ControlPlanAgent",
            ".pdf": "audit.checkers.v2.pdf_excel_delegator_agent:ControlPlanPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "文件适用阶段检查"),
            ("2", "rule_2", "特殊特性分类列检查"),
            ("3", "rule_3", "特殊特性分类、特性列列检查"),
            ("4", "rule_4", "特殊特性分类、控制方法列检查"),
            ("5", "rule_5", "频率/频次列检查"),
            ("6", "rule_6", "表头关键信息检查"),
        ],
    },
    ("v2", "过程特殊特性清单"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.process_special_characteristic_agent:ProcessSpecialCharacteristicAgent",
            ".xls": "audit.checkers.v2.process_special_characteristic_agent:ProcessSpecialCharacteristicAgent",
            ".xlsm": "audit.checkers.v2.process_special_characteristic_agent:ProcessSpecialCharacteristicAgent",
            ".pdf": "audit.checkers.v2.pdf_excel_delegator_agent:ProcessSpecialCharacteristicPdfExcelDelegatorAgent",
        },
        "clauses": [("1", "rule_1", "关键字段完整性检查"), ("2", "rule_2", "过程特性完整性检查"), ("3", "rule_3", "高风险管理项检查")],
    },
    ("v2", "生产系统产能评估报告"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.plant_capacity_evaluation_agent:PlantCapacityEvaluationAgent",
            ".xls": "audit.checkers.v2.plant_capacity_evaluation_agent:PlantCapacityEvaluationAgent",
            ".xlsm": "audit.checkers.v2.plant_capacity_evaluation_agent:PlantCapacityEvaluationAgent",
            ".pdf": "audit.checkers.v2.pdf_excel_delegator_agent:PlantCapacityEvaluationPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "关键字段完整性检查"),
            ("2", "rule_2", "行完整性检查"),
            ("3", "rule_3", "风险评估检查"),
            ("4", "rule_4", "良率计算检查"),
            ("5", "rule_5", "产出效率检查"),
            ("6", "rule_6", "产能预估检查"),
        ],
    },
    ("v2", "项目问题清单"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.xir_assembly_issue_agent:XirAssemblyIssueAgent",
            ".xls": "audit.checkers.v2.xir_assembly_issue_agent:XirAssemblyIssueAgent",
            ".xlsm": "audit.checkers.v2.xir_assembly_issue_agent:XirAssemblyIssueAgent",
            ".pdf": "audit.checkers.v2.pdf_excel_delegator_agent:XirAssemblyIssuePdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "关键字段完整性检查"),
            ("2", "rule_2", "问题描述完整性检查"),
            ("3", "rule_3", "对应措施完整性检查"),
            ("4", "rule_4", "整车/总成功/性能性、SOP+3产能风险、安全性、法律法规风险完整性检查"),
        ],
    },
    ("v2", "测量系统分析评价"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.msa_report_agent:MsaReportAgent",
            ".xls": "audit.checkers.v2.msa_report_agent:MsaReportAgent",
            ".xlsm": "audit.checkers.v2.msa_report_agent:MsaReportAgent",
            ".pdf": "audit.checkers.v2.pdf_excel_delegator_agent:MsaReportPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", '检查"任务项一"和"任务项二"的状态'),
            ("2", "rule_2", '检查"测量系统分析报告(R&R)"的填写情况'),
            ("3", "rule_3", "检查R&R分析报告的数据完整性"),
            ("4", "rule_4", "检查R&R分析的结论"),
        ],
    },
    ("v2", "零件提交保证书"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.psw_excel_agent:PswExcelAgent",
            ".xls": "audit.checkers.v2.psw_excel_agent:PswExcelAgent",
            ".xlsm": "audit.checkers.v2.psw_excel_agent:PswExcelAgent",
            ".pdf": "audit.checkers.v2.psw_pdf_agent:PswPDFAgent",
            ".docx": "audit.checkers.v2.psw_docx_agent:PswDocxAgent",
        },
        "clauses": [
            ("1", "rule_1", "附件列内容检查"),
            ("2", "rule_2", "重量字段检查"),
            ("3", "rule_3", "印章检查"),
            ("4", "rule_4", "供应商名称/供应商代码格式检查"),
            ("5", "rule_5", "签字和盖章检查"),
            ("6", "rule_6", "必填字段检查"),
            ("7", "rule_7", "提交原因检查"),
            ("8", "rule_8", "认可情况检查"),
            ("9", "rule_9", "条件批准日期检查"),
            ("10", "rule_10", "条件批准补充评论检查"),
        ],
    },
    ("v2", "全功能检验报告"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.dimension_measurement_excel_agent:DimensionMeasurementExcelAgent",
            ".xls": "audit.checkers.v2.dimension_measurement_excel_agent:DimensionMeasurementExcelAgent",
            ".xlsm": "audit.checkers.v2.dimension_measurement_excel_agent:DimensionMeasurementExcelAgent",
            ".pdf": "audit.checkers.v2.dimension_measurement_pdf_agent:DimensionMeasurementPDFAgent",
        },
        "clauses": [
            ("1", "rule_1", "标题识别检查"),
            ("2", "rule_2", "重要度标记检查"),
            ("3", "rule_3", "尺寸区数值公差带检查"),
            ("4", "rule_4", "检查方法/工具列检查"),
            ("5", "rule_5", "尺寸区数值数据唯一性检查"),
            ("6", "rule_6", "非尺寸数据检查"),
            ("7", "rule_7", "尺寸区OK数据方法匹配检查"),
            ("8", "rule_8", "关键字段识别检查"),
        ],
    },
    ("v2", "零部件设计验证计划和报告"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.dvp_r_excel_agent:DvpRExcelAgent",
            ".xls": "audit.checkers.v2.dvp_r_excel_agent:DvpRExcelAgent",
            ".xlsm": "audit.checkers.v2.dvp_r_excel_agent:DvpRExcelAgent",
            ".pdf": "audit.checkers.v2.pdf_excel_delegator_agent:DvpRPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "检查版本号、修订日期等字段是否存在且不为空"),
            ("2", "rule_2", "检查试验项目等字段是否存在且不为空"),
            ("3", "rule_3", "检查试验类型是否为PV且状态为硬模件"),
            ("4", "rule_4", "检查计划开始和结束日期"),
            ("5", "rule_5", "检查实际开始和结束日期"),
            ("6", "rule_6", "检查实际试验结果"),
            ("7", "rule_7", "检查试验报告编号"),
        ],
    },
    ("v2", "潜在过程失效模式及后果分析"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.pfmea_agent:PfmeaAgent",
            ".xls": "audit.checkers.v2.pfmea_agent:PfmeaAgent",
            ".xlsm": "audit.checkers.v2.pfmea_agent:PfmeaAgent",
            ".pdf": "audit.checkers.v2.pdf_excel_delegator_agent:PfmeaPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "表头字段完整性检查"),
            ("2", "rule_2", "PFMEA开始/修订日期与变更履历一致性检查"),
            ("3", "rule_3", "过程步骤与工序名称一致性检查"),
            ("4", "rule_4", "过程项功能维度检查"),
            ("5", "rule_5", "特殊特性一致性检查"),
            ("6", "rule_6", "失效模式、失效影响、失效严重度维度检查"),
            ("7", "rule_7", "现行探测控制及探测度维度检查"),
        ],
    },
    ("v2", "设备清单"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.equipment_list_agent:EquipmentListAgent",
            ".xls": "audit.checkers.v2.equipment_list_agent:EquipmentListAgent",
            ".xlsm": "audit.checkers.v2.equipment_list_agent:EquipmentListAgent",
            ".pdf": "audit.checkers.v2.pdf_excel_delegator_agent:EquipmentListPdfExcelDelegatorAgent",
            ".docx": "audit.checkers.v2.equipment_list_docx_agent:EquipmentListDocxAgent",
        },
        "clauses": [("1", "rule_1", "产线/工序、设备开发检查"), ("2", "rule_2", "设备验收检查")],
    },
    ("v2", "零部件年度试验要求"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.annual_test_requirements_agent:AnnualTestRequirementsAgent",
            ".xls": "audit.checkers.v2.annual_test_requirements_agent:AnnualTestRequirementsAgent",
            ".xlsm": "audit.checkers.v2.annual_test_requirements_agent:AnnualTestRequirementsAgent",
            ".pdf": "audit.checkers.v2.pdf_excel_delegator_agent:AnnualTestRequirementsPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "质量等级检查"),
            ("2", "rule_2", "测试频率检查"),
            ("3", "rule_3", "供应商名称检查"),
            ("4", "rule_4", "样本数量检查"),
            ("5", "rule_5", "试验要求检查"),
            ("6", "rule_6", "车型检查"),
        ],
    },
    ("v2", "模具验收报告"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.tooling_approval_agent:ToolingApprovalAgent",
            ".xls": "audit.checkers.v2.tooling_approval_agent:ToolingApprovalAgent",
            ".xlsm": "audit.checkers.v2.tooling_approval_agent:ToolingApprovalAgent",
            ".pdf": "audit.checkers.v2.pdf_excel_delegator_agent:ToolingApprovalPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "名称和日期检查"),
            ("2", "rule_2", "工艺类型及相关字段检查"),
            ("3", "rule_3", "模具设计尺寸格式检查"),
            ("4", "rule_4", "实物照片检查"),
            ("5", "rule_5", "编制和审核字段检查"),
            ("6", "rule_6", "实物确认检查"),
        ],
    },
    ("v2", "过程能力研究"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.initial_process_capacity_agent:InitialProcessCapacityAgent",
            ".xls": "audit.checkers.v2.initial_process_capacity_agent:InitialProcessCapacityAgent",
            ".xlsm": "audit.checkers.v2.initial_process_capacity_agent:InitialProcessCapacityAgent",
            ".pdf": "audit.checkers.v2.pdf_excel_delegator_agent:InitialProcessCapacityPdfExcelDelegatorAgent",
        },
        "clauses": [("1", "rule_1", "P阶段Cpk结果列内容检查"), ("2", "rule_2", "P阶段结果判定列内容检查"), ("3", "rule_3", "控制图短期过程能力检查")],
    },
    ("v2", "检具验收报告"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.checking_fixture_approval_excel_agent:CheckingFixtureApprovalExcelAgent",
            ".xls": "audit.checkers.v2.checking_fixture_approval_excel_agent:CheckingFixtureApprovalExcelAgent",
            ".xlsm": "audit.checkers.v2.checking_fixture_approval_excel_agent:CheckingFixtureApprovalExcelAgent",
            ".pdf": "audit.checkers.v2.checking_fixture_approval_agent:CheckingFixtureApprovalAgent",
        },
        "clauses": [("1", "rule_1", "文件完整性检查 (Completeness Check)"), ("2", "rule_2", "前缀检查 (Prefix Check)"), ("3", "rule_3", "不合格项与结论的关联性检查 (Correlation Check)")],
    },
    ("v2", "DV报告_PV报告_试验报告"): {
        "carriers": {
            ".xlsx": "audit.checkers.v2.dpvr_excel_agent:DpvrExcelAgent",
            ".xls": "audit.checkers.v2.dpvr_excel_agent:DpvrExcelAgent",
            ".xlsm": "audit.checkers.v2.dpvr_excel_agent:DpvrExcelAgent",
            ".pdf": "audit.checkers.v2.pdf_excel_delegator_agent:DpvrPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "报告编号检查"),
            ("2", "rule_2", "试验结果检查"),
            ("3", "rule_3", "试验类型检查"),
            ("4", "rule_4", "印章检查"),
            ("5", "rule_5", "试验阶段图片检查"),
            ("6", "rule_6", "样品数量检查"),
        ],
    },
    ("v3", "材料产品及分供清单"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.sub_supplier_list_excel_agent:SubSupplierListExcelAgent",
            ".xls": "audit.checkers.v3.sub_supplier_list_excel_agent:SubSupplierListExcelAgent",
            ".xlsm": "audit.checkers.v3.sub_supplier_list_excel_agent:SubSupplierListExcelAgent",
            ".pdf": "audit.checkers.v3.pdf_excel_delegator_agent:SubSupplierListPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "单件重量列及精度审核"),
            ("2", "rule_2", "材料信息列完整性及非空审核"),
            ("3", "rule_3", "部件状态列存在性及互斥性审核"),
            ("4", "rule_4", "PPAP完成状态逻辑一致性审核"),
        ],
    },
    ("v3", "过程流程图"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.process_flow_chart_excel_agent:ProcessFlowChartExcelAgent",
            ".xls": "audit.checkers.v3.process_flow_chart_excel_agent:ProcessFlowChartExcelAgent",
            ".xlsm": "audit.checkers.v3.process_flow_chart_excel_agent:ProcessFlowChartExcelAgent",
            ".pdf": "audit.checkers.v3.pdf_excel_delegator_agent:ProcessFlowChartPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "表头字段完整性检查"),
            ("2", "rule_2", "产品特殊特性及类别填写逻辑校验"),
            ("3", "rule_3", "过程特殊特性及类别填写逻辑校验"),
            ("4", "rule_4", "工序名称与操作描述填写逻辑校验"),
            ("5", "rule_5", "工序名称与图例填写逻辑校验"),
            ("6", "rule_6", "起始工序拆解完整性识别"),
            ("7", "rule_7", "最终工序拆解完整性识别"),
            ("8", "rule_8", "返工/返修工序检验环节识别"),
            ("9", "rule_9", "跨文件一致性审核（PFD与特殊特性清单）"),
        ],
    },
    ("v3", "控制计划"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.control_plan_agent:ControlPlanAgent",
            ".xls": "audit.checkers.v3.control_plan_agent:ControlPlanAgent",
            ".xlsm": "audit.checkers.v3.control_plan_agent:ControlPlanAgent",
            ".pdf": "audit.checkers.v3.pdf_excel_delegator_agent:ControlPlanPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "文件类型勾选审核"),
            ("2", "rule_2", "过程流程图与控制计划列项及一致性审核"),
            ("3", "rule_3", "PFMEA与控制计划特殊特性一致性审核"),
            ("4", "rule_4", "特殊特性分类与产品/过程列存在性审核"),
            ("5", "rule_5", "特殊特性分类与控制方法关联审核"),
            ("6", "rule_6", "频率或频次列完整性审核"),
            ("7", "rule_7", "控制计划表头字段完整性及必填项审核"),
            ("8", "rule_8", "流程一致性审核"),
            ("9", "rule_9", "特性一致性审核"),
            ("10", "rule_10", "措施一致性审核（PFMEA与CP）"),
            ("11", "rule_11", "措施一致性审核（特殊特性清单与CP）"),
        ],
    },
    ("v3", "过程特殊特性清单"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.process_special_characteristic_agent:ProcessSpecialCharacteristicAgent",
            ".xls": "audit.checkers.v3.process_special_characteristic_agent:ProcessSpecialCharacteristicAgent",
            ".xlsm": "audit.checkers.v3.process_special_characteristic_agent:ProcessSpecialCharacteristicAgent",
            ".pdf": "audit.checkers.v3.pdf_excel_delegator_agent:ProcessSpecialCharacteristicPdfExcelDelegatorAgent",
        },
        "clauses": [("1", "rule_1", "产品编号识别"), ("2", "rule_2", "列完整性及内容非空检查")],
    },
    ("v3", "生产系统产能评估报告"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.plant_capacity_evaluation_agent:PlantCapacityEvaluationAgent",
            ".xls": "audit.checkers.v3.plant_capacity_evaluation_agent:PlantCapacityEvaluationAgent",
            ".xlsm": "audit.checkers.v3.plant_capacity_evaluation_agent:PlantCapacityEvaluationAgent",
            ".pdf": "audit.checkers.v3.pdf_excel_delegator_agent:PlantCapacityEvaluationPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "基础字段完整性与内容校验"),
            ("2", "rule_2", "线体工序数据完整性校验"),
            ("3", "rule_3", "风险评估与措施一致性校验"),
            ("4", "rule_4", "良率计算逻辑校验"),
            ("5", "rule_5", "单线体总产出JPH计算逻辑校验"),
            ("6", "rule_6", "预估月度产能计算逻辑校验"),
        ],
    },
    ("v3", "项目问题清单"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.xir_assembly_issue_agent:XirAssemblyIssueAgent",
            ".xls": "audit.checkers.v3.xir_assembly_issue_agent:XirAssemblyIssueAgent",
            ".xlsm": "audit.checkers.v3.xir_assembly_issue_agent:XirAssemblyIssueAgent",
            ".pdf": "audit.checkers.v3.pdf_excel_delegator_agent:XirAssemblyIssuePdfExcelDelegatorAgent",
        },
        "clauses": [("1", "rule_1", "基础字段完整性审核"), ("2", "rule_2", "问题关闭状态与描述关联审核"), ("3", "rule_3", "整改措施要素完整性及时间有效性审核"), ("4", "rule_4", "风险分类标识审核")],
    },
    ("v3", "测量系统分析评价"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.msa_report_agent:MsaReportAgent",
            ".xls": "audit.checkers.v3.msa_report_agent:MsaReportAgent",
            ".xlsm": "audit.checkers.v3.msa_report_agent:MsaReportAgent",
            ".pdf": "audit.checkers.v3.pdf_excel_delegator_agent:MsaReportPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "任务项字段识别与状态审核"),
            ("2", "rule_2", "测量系统分析计划表完整性审核"),
            ("3", "rule_3", "量具重复性和再现性样本数量审核"),
            ("4", "rule_4", "量具重复性和再现性结论判定审核"),
        ],
    },
    ("v3", "零件提交保证书"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.psw_excel_agent:PswExcelAgent",
            ".xls": "audit.checkers.v3.psw_excel_agent:PswExcelAgent",
            ".xlsm": "audit.checkers.v3.psw_excel_agent:PswExcelAgent",
            ".pdf": "audit.checkers.v3.psw_pdf_agent:PswPDFAgent",
            ".docx": "audit.checkers.v3.psw_docx_agent:PswDocxAgent",
        },
        "clauses": [
            ("1", "rule_1", "生产件批准交付物文件清单附件检查"),
            ("2", "rule_2", "重量字段格式检查"),
            ("3", "rule_3", "印章及授权声明检查"),
            ("4", "rule_4", "供应商名称格式检查"),
            ("5", "rule_5", "签字与盖章完整性检查"),
            ("6", "rule_6", "关键字段非空检查"),
            ("7", "rule_7", "提交原因选项检查"),
            ("8", "rule_8", "认可情况及结果勾选检查"),
            ("9", "rule_9", "条件批准日期时效检查"),
            ("10", "rule_10", "条件批准补充评论检查"),
        ],
    },
    ("v3", "全功能检验报告"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.dimension_measurement_excel_agent:DimensionMeasurementExcelAgent",
            ".xls": "audit.checkers.v3.dimension_measurement_excel_agent:DimensionMeasurementExcelAgent",
            ".xlsm": "audit.checkers.v3.dimension_measurement_excel_agent:DimensionMeasurementExcelAgent",
            ".pdf": "audit.checkers.v3.dimension_measurement_pdf_agent:DimensionMeasurementPDFAgent",
        },
        "clauses": [
            ("1", "rule_1", "CoverSheet页存在性及内容完整性审核"),
            ("2", "rule_2", "测量报告列存在性及数据非空审核"),
            ("3", "rule_3", "特定控制方向的公差NA值一致性审核"),
            ("4", "rule_4", "名义值为空时的偏差值存在性审核"),
            ("5", "rule_5", "类型列内容合规性审核"),
            ("6", "rule_6", "测点号与测点图标记点对应性审核"),
            ("7", "rule_7", "特定表类型的偏差值在公差范围内审核"),
            ("8", "rule_8", "中性值（Centering）计算准确性审核"),
            ("9", "rule_9", "中性值（Centering）阈值判定及预警审核"),
            ("10", "rule_10", "极差值（Range）计算准确性审核"),
            ("11", "rule_11", "极差值（Range）阈值判定及预警审核"),
        ],
    },
    ("v3", "零部件设计验证计划和报告"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.dvp_r_excel_agent:DvpRExcelAgent",
            ".xls": "audit.checkers.v3.dvp_r_excel_agent:DvpRExcelAgent",
            ".xlsm": "audit.checkers.v3.dvp_r_excel_agent:DvpRExcelAgent",
            ".pdf": "audit.checkers.v3.pdf_excel_delegator_agent:DvpRPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "基础信息字段完整性审核"),
            ("2", "rule_2", "试验计划列完整性审核"),
            ("3", "rule_3", "PV试验样本状态一致性审核"),
            ("4", "rule_4", "计划时间逻辑审核"),
            ("5", "rule_5", "实际试验时间逻辑审核"),
            ("6", "rule_6", "试验结果有效性审核"),
            ("7", "rule_7", "试验报告编号完整性审核"),
        ],
    },
    ("v3", "潜在过程失效模式及后果分析"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.pfmea_agent:PfmeaAgent",
            ".xls": "audit.checkers.v3.pfmea_agent:PfmeaAgent",
            ".xlsm": "audit.checkers.v3.pfmea_agent:PfmeaAgent",
            ".pdf": "audit.checkers.v3.pdf_excel_delegator_agent:PfmeaPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1_tablehead_keywords", "表头字段完整性审核"),
            ("2", "rule_2_pfmea_start_revision_date", "PFMEA与变更履历一致性审核"),
            ("3", "rule_3_process_item_function_dimensions", "过程项功能维度审核"),
            ("4", "rule_4_failure_effect_dimensions_severity", "失效影响及严重度审核"),
            ("5", "rule_5_current_detection_controls_detection", "现行探测控制及探测度审核"),
            ("6", "rule_6_process_steps_consistency", "流程一致性审核"),
            ("7", "rule_7_special_characteristics_consistency", "特性一致性审核"),
        ],
    },
    ("v3", "设备清单"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.equipment_list_agent:EquipmentListAgent",
            ".xls": "audit.checkers.v3.equipment_list_agent:EquipmentListAgent",
            ".xlsm": "audit.checkers.v3.equipment_list_agent:EquipmentListAgent",
            ".pdf": "audit.checkers.v3.pdf_excel_delegator_agent:EquipmentListPdfExcelDelegatorAgent",
            ".docx": "audit.checkers.v3.equipment_list_docx_agent:EquipmentListDocxAgent",
        },
        "clauses": [("1", "rule_1", "设备开发列完整性与数据填充审核"), ("2", "rule_2", "设备验收列完整性与整改逻辑审核")],
    },
    ("v3", "零部件年度试验要求"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.annual_test_requirements_agent:AnnualTestRequirementsAgent",
            ".xls": "audit.checkers.v3.annual_test_requirements_agent:AnnualTestRequirementsAgent",
            ".xlsm": "audit.checkers.v3.annual_test_requirements_agent:AnnualTestRequirementsAgent",
            ".pdf": "audit.checkers.v3.pdf_excel_delegator_agent:AnnualTestRequirementsPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "质量等级字段识别"),
            ("2", "rule_2", "试验项目与测试频率一致性"),
            ("3", "rule_3", "供应商名称格式审核"),
            ("4", "rule_4", "样本数量列及内容识别"),
            ("5", "rule_5", "试验要求栏完整性审核"),
            ("6", "rule_6", "车型字段存在性及内容审核"),
        ],
    },
    ("v3", "模具验收报告"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.tooling_approval_agent:ToolingApprovalAgent",
            ".xls": "audit.checkers.v3.tooling_approval_agent:ToolingApprovalAgent",
            ".xlsm": "audit.checkers.v3.tooling_approval_agent:ToolingApprovalAgent",
            ".pdf": "audit.checkers.v3.pdf_excel_delegator_agent:ToolingApprovalPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "零件供应商名称与提交日期字段检查"),
            ("2", "rule_2", "关键列完整性与内容非空检查"),
            ("3", "rule_3", "模具设计尺寸格式检查"),
            ("4", "rule_4", "实物照片列检查"),
            ("5", "rule_5", "编制与审核字段检查"),
            ("6", "rule_6", "实物确认列内容检查"),
        ],
    },
    ("v3", "过程能力研究"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.initial_process_capacity_agent:InitialProcessCapacityAgent",
            ".xls": "audit.checkers.v3.initial_process_capacity_agent:InitialProcessCapacityAgent",
            ".xlsm": "audit.checkers.v3.initial_process_capacity_agent:InitialProcessCapacityAgent",
            ".pdf": "audit.checkers.v3.pdf_excel_delegator_agent:InitialProcessCapacityPdfExcelDelegatorAgent",
        },
        "clauses": [("1", "rule_1", "P阶段Cpk结果列审核"), ("2", "rule_2", "P阶段结果判定列审核"), ("3", "rule_3", "控制图及短期过程能力审核")],
    },
    ("v3", "检具验收报告"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.checking_fixture_approval_excel_agent:CheckingFixtureApprovalExcelAgent",
            ".xls": "audit.checkers.v3.checking_fixture_approval_excel_agent:CheckingFixtureApprovalExcelAgent",
            ".xlsm": "audit.checkers.v3.checking_fixture_approval_excel_agent:CheckingFixtureApprovalExcelAgent",
            ".pdf": "audit.checkers.v3.checking_fixture_approval_agent:CheckingFixtureApprovalAgent",
        },
        "clauses": [("1", "rule_1", "检具清单与认可报告完整性审核"), ("2", "rule_2", "检具清单标题规范性审核"), ("3", "rule_3", "所附文件与结论一致性审核")],
    },
    ("v3", "DV报告_PV报告_试验报告"): {
        "carriers": {
            ".xlsx": "audit.checkers.v3.dpvr_excel_agent:DpvrExcelAgent",
            ".xls": "audit.checkers.v3.dpvr_excel_agent:DpvrExcelAgent",
            ".xlsm": "audit.checkers.v3.dpvr_excel_agent:DpvrExcelAgent",
            ".pdf": "audit.checkers.v3.pdf_excel_delegator_agent:DpvrPdfExcelDelegatorAgent",
        },
        "clauses": [
            ("1", "rule_1", "文件编号审核"),
            ("2", "rule_2", "试验结论审核"),
            ("3", "rule_3", "试验类型审核"),
            ("4", "rule_4", "印章审核"),
            ("5", "rule_5", "试验图片审核"),
            ("6", "rule_6", "样品数量审核"),
        ],
    },
}


def migrated_builtin_suite(profile: str, document_type: str) -> StandardSuite:
    """Resolve an explicit migration source, not a runtime route or fallback."""
    try:
        source = _BUILTIN_SOURCES[(profile, document_type)]
    except KeyError:
        raise ValueError("unknown_builtin_migration_source") from None
    methods = {clause: method for clause, method, _description in source["clauses"]}
    resources = {"carriers": source["carriers"], "methods": methods}
    entrypoints = dict.fromkeys(methods, "checker:check")
    rules = {
        "kind": "engine",
        "type": document_type,
        "title": document_type,
        "checker_binding": entrypoints,
        "rules": [{"id": clause, "category": description, "priority": "P0", "rule_to_check": description, "type_of_rule": "text"} for clause, _method, description in source["clauses"]],
    }
    files = {
        "checker.py": (f"from audit.standard_library import check_clause\nRESOURCES = {resources!r}\ndef check(context):\n    return check_clause(context, RESOURCES)\n").encode(),
        "rules.json": _json(rules),
        "appendix.json": b"[]",
        "templates.json": b"[]",
        "tests.json": _json([{"oracle": "assets/corpus/oracle.csv", "dataset": profile, "document_type": document_type, "correctness": "independent-human-oracle-required"}]),
    }
    return StandardSuite(f"PPAP:{document_type}", document_type, "PPAP", document_type, (), MappingProxyType(files), MappingProxyType(entrypoints), "assets/corpus/oracle.csv")


def generic_standard_suites(*, include_history: bool = False) -> tuple[StandardSuite, ...]:
    return tuple(migrated_builtin_suite(profile, document_type) for profile, document_type in _BUILTIN_SOURCES if include_history or profile == "v3")


def user_rule_files(
    rule_body: Mapping[str, Any],
    *,
    appendix: Sequence[Mapping[str, Any]] = (),
    checker_files: Mapping[str, bytes] | None = None,
    entrypoints: Mapping[str, str] | None = None,
) -> dict[str, bytes]:
    """Preserve user clauses with no official baseline or inferred coverage.

    The caller owns tenant/object authorization and writes base_revision_id=None.
    Only explicitly supplied, already-authored checker bindings are carried over.
    Unbound rules remain unsupported until Deep creates a reviewed candidate.
    """
    from audit.checker_core import RuleBinding, checker_digest, clause_ids

    rules = json.loads(_json(dict(rule_body)))
    ids = clause_ids(rules)
    bindings = dict(entrypoints or {})
    if set(bindings) - set(ids) or (bindings and checker_files is None):
        raise ValueError("user_rule_binding_without_artifact")
    programs = dict(checker_files) if checker_files is not None else {"checker.py": b'"""No generated checker yet; all clauses are unsupported."""\n'}
    if set(programs) - {"checker.py", "resources.py"} or "checker.py" not in programs:
        raise ValueError("invalid_checker_program_files")
    RuleBinding("user-import", checker_digest(programs), bindings)
    # Historical selection hints cannot turn user-written rules into a builtin.
    for field in ("rule_profile", "preset_key", "preset_customer", "checker_binding"):
        rules.pop(field, None)
    rules["checker_binding"] = bindings
    return {**programs, "rules.json": _json(rules), "appendix.json": _json(list(appendix)), "templates.json": b"[]", "tests.json": b"[]"}
