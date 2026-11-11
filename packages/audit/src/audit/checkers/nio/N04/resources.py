"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N04",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.process_special_characteristic_agent:ProcessSpecialCharacteristicAgent",
        ".xls": "audit.checker_lib.nio.process_special_characteristic_agent:ProcessSpecialCharacteristicAgent",
        ".xlsm": "audit.checker_lib.nio.process_special_characteristic_agent:ProcessSpecialCharacteristicAgent",
        ".pdf": "audit.checker_lib.nio.pdf_excel_delegator_agent:ProcessSpecialCharacteristicPdfExcelDelegatorAgent",
    },
    "methods": {"N04:1": "rule_1", "N04:2": "rule_2"},
    "implementation_rules": [{"id": "1", "category": "产品编号识别", "rule_to_check": "产品编号识别"}, {"id": "2", "category": "列完整性及内容非空检查", "rule_to_check": "列完整性及内容非空检查"}],
}
