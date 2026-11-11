"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L08",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.control_plan_agent:ControlPlanAgent",
        ".xls": "audit.checker_lib.li.control_plan_agent:ControlPlanAgent",
        ".xlsm": "audit.checker_lib.li.control_plan_agent:ControlPlanAgent",
        ".pdf": "audit.checker_lib.li.pdf_excel_delegator_agent:ControlPlanPdfExcelDelegatorAgent",
    },
    "methods": {"L08:1": "rule_1", "L08:2": "rule_2", "L08:3": "rule_3", "L08:4": "rule_4", "L08:5": "rule_5", "L08:6": "rule_6"},
    "implementation_rules": [
        {"id": "1", "category": "文件适用阶段检查", "rule_to_check": "文件适用阶段检查"},
        {"id": "2", "category": "特殊特性分类列检查", "rule_to_check": "特殊特性分类列检查"},
        {"id": "3", "category": "特殊特性分类、特性列列检查", "rule_to_check": "特殊特性分类、特性列列检查"},
        {"id": "4", "category": "特殊特性分类、控制方法列检查", "rule_to_check": "特殊特性分类、控制方法列检查"},
        {"id": "5", "category": "频率/频次列检查", "rule_to_check": "频率/频次列检查"},
        {"id": "6", "category": "表头关键信息检查", "rule_to_check": "表头关键信息检查"},
    ],
}
