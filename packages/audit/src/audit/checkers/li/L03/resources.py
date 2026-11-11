"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L03",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.dpvr_excel_agent:DpvrExcelAgent",
        ".xls": "audit.checker_lib.li.dpvr_excel_agent:DpvrExcelAgent",
        ".xlsm": "audit.checker_lib.li.dpvr_excel_agent:DpvrExcelAgent",
        ".pdf": "audit.checker_lib.li.pdf_excel_delegator_agent:DpvrPdfExcelDelegatorAgent",
    },
    "methods": {"L03:1": "rule_1", "L03:2": "rule_2", "L03:3": "rule_3", "L03:4": "rule_4", "L03:5": "rule_5", "L03:6": "rule_6"},
    "implementation_rules": [
        {"id": "1", "category": "报告编号检查", "rule_to_check": "报告编号检查"},
        {"id": "2", "category": "试验结果检查", "rule_to_check": "试验结果检查"},
        {"id": "3", "category": "试验类型检查", "rule_to_check": "试验类型检查"},
        {"id": "4", "category": "印章检查", "rule_to_check": "印章检查"},
        {"id": "5", "category": "试验阶段图片检查", "rule_to_check": "试验阶段图片检查"},
        {"id": "6", "category": "样品数量检查", "rule_to_check": "样品数量检查"},
    ],
}
