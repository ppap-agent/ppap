"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N08",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.sub_supplier_list_excel_agent:SubSupplierListExcelAgent",
        ".xls": "audit.checker_lib.nio.sub_supplier_list_excel_agent:SubSupplierListExcelAgent",
        ".xlsm": "audit.checker_lib.nio.sub_supplier_list_excel_agent:SubSupplierListExcelAgent",
        ".pdf": "audit.checker_lib.nio.pdf_excel_delegator_agent:SubSupplierListPdfExcelDelegatorAgent",
    },
    "methods": {"N08:1": "rule_1", "N08:2": "rule_2", "N08:3": "rule_3", "N08:4": "rule_4"},
    "implementation_rules": [
        {"id": "1", "category": "单件重量列及精度审核", "rule_to_check": "单件重量列及精度审核"},
        {"id": "2", "category": "材料信息列完整性及非空审核", "rule_to_check": "材料信息列完整性及非空审核"},
        {"id": "3", "category": "部件状态列存在性及互斥性审核", "rule_to_check": "部件状态列存在性及互斥性审核"},
        {"id": "4", "category": "PPAP完成状态逻辑一致性审核", "rule_to_check": "PPAP完成状态逻辑一致性审核"},
    ],
}
