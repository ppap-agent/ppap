"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N13",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.equipment_list_agent:EquipmentListAgent",
        ".xls": "audit.checker_lib.nio.equipment_list_agent:EquipmentListAgent",
        ".xlsm": "audit.checker_lib.nio.equipment_list_agent:EquipmentListAgent",
        ".pdf": "audit.checker_lib.nio.pdf_excel_delegator_agent:EquipmentListPdfExcelDelegatorAgent",
        ".docx": "audit.checker_lib.nio.equipment_list_docx_agent:EquipmentListDocxAgent",
    },
    "methods": {"N13:1": "rule_1", "N13:2": "rule_2"},
    "implementation_rules": [
        {"id": "1", "category": "设备开发列完整性与数据填充审核", "rule_to_check": "设备开发列完整性与数据填充审核"},
        {"id": "2", "category": "设备验收列完整性与整改逻辑审核", "rule_to_check": "设备验收列完整性与整改逻辑审核"},
    ],
}
