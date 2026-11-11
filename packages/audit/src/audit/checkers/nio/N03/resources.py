"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N03",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.control_plan_agent:ControlPlanAgent",
        ".xls": "audit.checker_lib.nio.control_plan_agent:ControlPlanAgent",
        ".xlsm": "audit.checker_lib.nio.control_plan_agent:ControlPlanAgent",
        ".pdf": "audit.checker_lib.nio.pdf_excel_delegator_agent:ControlPlanPdfExcelDelegatorAgent",
    },
    "methods": {
        "N03:1": "rule_1",
        "N03:2": "rule_4",
        "N03:3": "rule_5",
        "N03:4": "rule_6",
        "N03:5": "rule_7",
        "N03:6": "rule_8",
        "N03:7": "rule_3",
        "N03:8": "rule_9",
        "N03:9": "rule_10",
        "N03:10": "rule_11",
    },
    "implementation_rules": [
        {"id": "1", "category": "文件类型勾选审核", "rule_to_check": "文件类型勾选审核"},
        {"id": "2", "category": "过程流程图与控制计划列项及一致性审核", "rule_to_check": "过程流程图与控制计划列项及一致性审核"},
        {"id": "3", "category": "PFMEA与控制计划特殊特性一致性审核", "rule_to_check": "PFMEA与控制计划特殊特性一致性审核"},
        {"id": "4", "category": "特殊特性分类与产品/过程列存在性审核", "rule_to_check": "特殊特性分类与产品/过程列存在性审核"},
        {"id": "5", "category": "特殊特性分类与控制方法关联审核", "rule_to_check": "特殊特性分类与控制方法关联审核"},
        {"id": "6", "category": "频率或频次列完整性审核", "rule_to_check": "频率或频次列完整性审核"},
        {"id": "7", "category": "控制计划表头字段完整性及必填项审核", "rule_to_check": "控制计划表头字段完整性及必填项审核"},
        {"id": "8", "category": "流程一致性审核", "rule_to_check": "流程一致性审核"},
        {"id": "9", "category": "特性一致性审核", "rule_to_check": "特性一致性审核"},
        {"id": "10", "category": "措施一致性审核（PFMEA与CP）", "rule_to_check": "措施一致性审核（PFMEA与CP）"},
        {"id": "11", "category": "措施一致性审核（特殊特性清单与CP）", "rule_to_check": "措施一致性审核（特殊特性清单与CP）"},
    ],
}
