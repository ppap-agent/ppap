"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L11",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.process_flow_chart_excel_agent:ProcessFlowChartExcelAgent",
        ".xls": "audit.checker_lib.li.process_flow_chart_excel_agent:ProcessFlowChartExcelAgent",
        ".xlsm": "audit.checker_lib.li.process_flow_chart_excel_agent:ProcessFlowChartExcelAgent",
        ".pdf": "audit.checker_lib.li.pdf_excel_delegator_agent:ProcessFlowChartPdfExcelDelegatorAgent",
    },
    "methods": {"L11:1": "rule_1", "L11:2": "rule_2", "L11:3": "rule_3", "L11:4": "rule_4", "L11:5": "rule_5", "L11:6": "rule_6"},
    "implementation_rules": [
        {"id": "1", "category": "表头关键字段完整性检查", "rule_to_check": "表头关键字段完整性检查"},
        {"id": "2", "category": "产品特殊特性检查", "rule_to_check": "产品特殊特性检查"},
        {"id": "3", "category": "过程特殊特性检查", "rule_to_check": "过程特殊特性检查"},
        {"id": "4", "category": "工序名称与操作描述检查", "rule_to_check": "工序名称与操作描述检查"},
        {"id": "5", "category": "工序定义完整性检查", "rule_to_check": "工序定义完整性检查"},
        {"id": "6", "category": "工序定义完整性检查（连线）", "rule_to_check": "工序定义完整性检查（连线）"},
    ],
}
