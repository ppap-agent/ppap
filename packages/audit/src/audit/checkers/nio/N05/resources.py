"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N05",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.process_flow_chart_excel_agent:ProcessFlowChartExcelAgent",
        ".xls": "audit.checker_lib.nio.process_flow_chart_excel_agent:ProcessFlowChartExcelAgent",
        ".xlsm": "audit.checker_lib.nio.process_flow_chart_excel_agent:ProcessFlowChartExcelAgent",
        ".pdf": "audit.checker_lib.nio.pdf_excel_delegator_agent:ProcessFlowChartPdfExcelDelegatorAgent",
    },
    "methods": {"N05:1": "rule_1", "N05:2": "rule_2", "N05:3": "rule_3", "N05:4": "rule_4", "N05:5": "rule_5", "N05:6": "rule_6", "N05:7": "rule_7", "N05:8": "rule_8", "N05:9": "rule_9"},
    "implementation_rules": [
        {"id": "1", "category": "表头字段完整性检查", "rule_to_check": "表头字段完整性检查"},
        {"id": "2", "category": "产品特殊特性及类别填写逻辑校验", "rule_to_check": "产品特殊特性及类别填写逻辑校验"},
        {"id": "3", "category": "过程特殊特性及类别填写逻辑校验", "rule_to_check": "过程特殊特性及类别填写逻辑校验"},
        {"id": "4", "category": "工序名称与操作描述填写逻辑校验", "rule_to_check": "工序名称与操作描述填写逻辑校验"},
        {"id": "5", "category": "工序名称与图例填写逻辑校验", "rule_to_check": "工序名称与图例填写逻辑校验"},
        {"id": "6", "category": "起始工序拆解完整性识别", "rule_to_check": "起始工序拆解完整性识别"},
        {"id": "7", "category": "最终工序拆解完整性识别", "rule_to_check": "最终工序拆解完整性识别"},
        {"id": "8", "category": "返工/返修工序检验环节识别", "rule_to_check": "返工/返修工序检验环节识别"},
        {"id": "9", "category": "跨文件一致性审核（PFD与特殊特性清单）", "rule_to_check": "跨文件一致性审核（PFD与特殊特性清单）"},
    ],
}
