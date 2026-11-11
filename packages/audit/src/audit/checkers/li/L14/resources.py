"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L14",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.plant_capacity_evaluation_agent:PlantCapacityEvaluationAgent",
        ".xls": "audit.checker_lib.li.plant_capacity_evaluation_agent:PlantCapacityEvaluationAgent",
        ".xlsm": "audit.checker_lib.li.plant_capacity_evaluation_agent:PlantCapacityEvaluationAgent",
        ".pdf": "audit.checker_lib.li.pdf_excel_delegator_agent:PlantCapacityEvaluationPdfExcelDelegatorAgent",
    },
    "methods": {"L14:1": "rule_1", "L14:2": "rule_2", "L14:3": "rule_3", "L14:4": "rule_4", "L14:5": "rule_5", "L14:6": "rule_6"},
    "implementation_rules": [
        {"id": "1", "category": "关键字段完整性检查", "rule_to_check": "关键字段完整性检查"},
        {"id": "2", "category": "行完整性检查", "rule_to_check": "行完整性检查"},
        {"id": "3", "category": "风险评估检查", "rule_to_check": "风险评估检查"},
        {"id": "4", "category": "良率计算检查", "rule_to_check": "良率计算检查"},
        {"id": "5", "category": "产出效率检查", "rule_to_check": "产出效率检查"},
        {"id": "6", "category": "产能预估检查", "rule_to_check": "产能预估检查"},
    ],
}
