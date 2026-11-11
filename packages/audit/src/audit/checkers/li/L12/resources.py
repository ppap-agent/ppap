"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L12",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.process_special_characteristic_agent:ProcessSpecialCharacteristicAgent",
        ".xls": "audit.checker_lib.li.process_special_characteristic_agent:ProcessSpecialCharacteristicAgent",
        ".xlsm": "audit.checker_lib.li.process_special_characteristic_agent:ProcessSpecialCharacteristicAgent",
        ".pdf": "audit.checker_lib.li.pdf_excel_delegator_agent:ProcessSpecialCharacteristicPdfExcelDelegatorAgent",
    },
    "methods": {"L12:1": "rule_1", "L12:2": "rule_2", "L12:3": "rule_3"},
    "implementation_rules": [
        {"id": "1", "category": "关键字段完整性检查", "rule_to_check": "关键字段完整性检查"},
        {"id": "2", "category": "过程特性完整性检查", "rule_to_check": "过程特性完整性检查"},
        {"id": "3", "category": "高风险管理项检查", "rule_to_check": "高风险管理项检查"},
    ],
}
