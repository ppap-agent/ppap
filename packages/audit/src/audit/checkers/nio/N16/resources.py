"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N16",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.annual_test_requirements_agent:AnnualTestRequirementsAgent",
        ".xls": "audit.checker_lib.nio.annual_test_requirements_agent:AnnualTestRequirementsAgent",
        ".xlsm": "audit.checker_lib.nio.annual_test_requirements_agent:AnnualTestRequirementsAgent",
        ".pdf": "audit.checker_lib.nio.pdf_excel_delegator_agent:AnnualTestRequirementsPdfExcelDelegatorAgent",
    },
    "methods": {"N16:1": "rule_1", "N16:2": "rule_2", "N16:3": "rule_3", "N16:4": "rule_4", "N16:5": "rule_5", "N16:6": "rule_6"},
    "implementation_rules": [
        {"id": "1", "category": "质量等级字段识别", "rule_to_check": "质量等级字段识别"},
        {"id": "2", "category": "试验项目与测试频率一致性", "rule_to_check": "试验项目与测试频率一致性"},
        {"id": "3", "category": "供应商名称格式审核", "rule_to_check": "供应商名称格式审核"},
        {"id": "4", "category": "样本数量列及内容识别", "rule_to_check": "样本数量列及内容识别"},
        {"id": "5", "category": "试验要求栏完整性审核", "rule_to_check": "试验要求栏完整性审核"},
        {"id": "6", "category": "车型字段存在性及内容审核", "rule_to_check": "车型字段存在性及内容审核"},
    ],
}
