"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L13",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.equipment_list_agent:EquipmentListAgent",
        ".xls": "audit.checker_lib.li.equipment_list_agent:EquipmentListAgent",
        ".xlsm": "audit.checker_lib.li.equipment_list_agent:EquipmentListAgent",
        ".pdf": "audit.checker_lib.li.pdf_excel_delegator_agent:EquipmentListPdfExcelDelegatorAgent",
        ".docx": "audit.checker_lib.li.equipment_list_docx_agent:EquipmentListDocxAgent",
    },
    "methods": {"L13:1": "rule_1", "L13:2": "rule_2"},
    "implementation_rules": [{"id": "1", "category": "产线/工序、设备开发检查", "rule_to_check": "产线/工序、设备开发检查"}, {"id": "2", "category": "设备验收检查", "rule_to_check": "设备验收检查"}],
}
