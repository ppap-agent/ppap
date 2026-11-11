"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L04",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.sub_supplier_list_excel_agent:SubSupplierListExcelAgent",
        ".xls": "audit.checker_lib.li.sub_supplier_list_excel_agent:SubSupplierListExcelAgent",
        ".xlsm": "audit.checker_lib.li.sub_supplier_list_excel_agent:SubSupplierListExcelAgent",
        ".pdf": "audit.checker_lib.li.pdf_excel_delegator_agent:SubSupplierListPdfExcelDelegatorAgent",
    },
    "methods": {"L04:1": "rule_1", "L04:2": "rule_2", "L04:3": "rule_3", "L04:4": "rule_4"},
    "implementation_rules": [
        {"id": "1", "category": "单件重量列存在性和数据格式检查", "rule_to_check": "单件重量列存在性和数据格式检查"},
        {"id": "2", "category": "材料信息列存在性和数据完整性检查", "rule_to_check": "材料信息列存在性和数据完整性检查"},
        {"id": "3", "category": "部件状态列存在性和数据唯一性检查", "rule_to_check": "部件状态列存在性和数据唯一性检查"},
        {"id": "4", "category": "PPAP完成状态列存在性和数据关联性检查", "rule_to_check": "PPAP完成状态列存在性和数据关联性检查"},
    ],
}
