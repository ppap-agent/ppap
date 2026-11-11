"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L10",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.tooling_approval_agent:ToolingApprovalAgent",
        ".xls": "audit.checker_lib.li.tooling_approval_agent:ToolingApprovalAgent",
        ".xlsm": "audit.checker_lib.li.tooling_approval_agent:ToolingApprovalAgent",
        ".pdf": "audit.checker_lib.li.pdf_excel_delegator_agent:ToolingApprovalPdfExcelDelegatorAgent",
    },
    "methods": {"L10:1": "rule_1", "L10:2": "rule_2", "L10:3": "rule_3", "L10:4": "rule_4", "L10:5": "rule_5", "L10:6": "rule_6"},
    "implementation_rules": [
        {"id": "1", "category": "名称和日期检查", "rule_to_check": "名称和日期检查"},
        {"id": "2", "category": "工艺类型及相关字段检查", "rule_to_check": "工艺类型及相关字段检查"},
        {"id": "3", "category": "模具设计尺寸格式检查", "rule_to_check": "模具设计尺寸格式检查"},
        {"id": "4", "category": "实物照片检查", "rule_to_check": "实物照片检查"},
        {"id": "5", "category": "编制和审核字段检查", "rule_to_check": "编制和审核字段检查"},
        {"id": "6", "category": "实物确认检查", "rule_to_check": "实物确认检查"},
    ],
}
