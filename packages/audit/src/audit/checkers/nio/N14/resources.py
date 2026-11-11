"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N14",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.plant_capacity_evaluation_agent:PlantCapacityEvaluationAgent",
        ".xls": "audit.checker_lib.nio.plant_capacity_evaluation_agent:PlantCapacityEvaluationAgent",
        ".xlsm": "audit.checker_lib.nio.plant_capacity_evaluation_agent:PlantCapacityEvaluationAgent",
        ".pdf": "audit.checker_lib.nio.pdf_excel_delegator_agent:PlantCapacityEvaluationPdfExcelDelegatorAgent",
    },
    "methods": {"N14:1": "rule_1", "N14:2": "rule_2", "N14:3": "rule_3", "N14:4": "rule_4", "N14:5": "rule_5", "N14:6": "rule_6"},
    "implementation_rules": [
        {"id": "1", "category": "基础字段完整性与内容校验", "rule_to_check": "基础字段完整性与内容校验"},
        {"id": "2", "category": "线体工序数据完整性校验", "rule_to_check": "线体工序数据完整性校验"},
        {"id": "3", "category": "风险评估与措施一致性校验", "rule_to_check": "风险评估与措施一致性校验"},
        {"id": "4", "category": "良率计算逻辑校验", "rule_to_check": "良率计算逻辑校验"},
        {"id": "5", "category": "单线体总产出JPH计算逻辑校验", "rule_to_check": "单线体总产出JPH计算逻辑校验"},
        {"id": "6", "category": "预估月度产能计算逻辑校验", "rule_to_check": "预估月度产能计算逻辑校验"},
    ],
}
