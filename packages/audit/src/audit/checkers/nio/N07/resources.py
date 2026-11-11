"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N07",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.dpvr_excel_agent:DpvrExcelAgent",
        ".xls": "audit.checker_lib.nio.dpvr_excel_agent:DpvrExcelAgent",
        ".xlsm": "audit.checker_lib.nio.dpvr_excel_agent:DpvrExcelAgent",
        ".pdf": "audit.checker_lib.nio.pdf_excel_delegator_agent:DpvrPdfExcelDelegatorAgent",
    },
    "methods": {"N07:1": "rule_1", "N07:2": "rule_2", "N07:3": "rule_3", "N07:4": "rule_4", "N07:5": "rule_5", "N07:6": "rule_6"},
    "implementation_rules": [
        {"id": "1", "category": "文件编号审核", "rule_to_check": "文件编号审核"},
        {"id": "2", "category": "试验结论审核", "rule_to_check": "试验结论审核"},
        {"id": "3", "category": "试验类型审核", "rule_to_check": "试验类型审核"},
        {"id": "4", "category": "印章审核", "rule_to_check": "印章审核"},
        {"id": "5", "category": "试验图片审核", "rule_to_check": "试验图片审核"},
        {"id": "6", "category": "样品数量审核", "rule_to_check": "样品数量审核"},
    ],
}
