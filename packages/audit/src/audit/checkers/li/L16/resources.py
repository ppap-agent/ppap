"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L16",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.annual_test_requirements_agent:AnnualTestRequirementsAgent",
        ".xls": "audit.checker_lib.li.annual_test_requirements_agent:AnnualTestRequirementsAgent",
        ".xlsm": "audit.checker_lib.li.annual_test_requirements_agent:AnnualTestRequirementsAgent",
        ".pdf": "audit.checker_lib.li.pdf_excel_delegator_agent:AnnualTestRequirementsPdfExcelDelegatorAgent",
    },
    "methods": {"L16:1": "rule_1", "L16:2": "rule_2", "L16:3": "rule_3", "L16:4": "rule_4", "L16:5": "rule_5", "L16:6": "rule_6"},
    "implementation_rules": [
        {"id": "1", "category": "质量等级检查", "rule_to_check": "质量等级检查"},
        {"id": "2", "category": "测试频率检查", "rule_to_check": "测试频率检查"},
        {"id": "3", "category": "供应商名称检查", "rule_to_check": "供应商名称检查"},
        {"id": "4", "category": "样本数量检查", "rule_to_check": "样本数量检查"},
        {"id": "5", "category": "试验要求检查", "rule_to_check": "试验要求检查"},
        {"id": "6", "category": "车型检查", "rule_to_check": "车型检查"},
    ],
}
