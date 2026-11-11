"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N12",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.tooling_approval_agent:ToolingApprovalAgent",
        ".xls": "audit.checker_lib.nio.tooling_approval_agent:ToolingApprovalAgent",
        ".xlsm": "audit.checker_lib.nio.tooling_approval_agent:ToolingApprovalAgent",
        ".pdf": "audit.checker_lib.nio.pdf_excel_delegator_agent:ToolingApprovalPdfExcelDelegatorAgent",
    },
    "methods": {"N12:1": "rule_1", "N12:2": "rule_2", "N12:3": "rule_3", "N12:4": "rule_4", "N12:5": "rule_5", "N12:6": "rule_6"},
    "implementation_rules": [
        {"id": "1", "category": "零件供应商名称与提交日期字段检查", "rule_to_check": "零件供应商名称与提交日期字段检查"},
        {"id": "2", "category": "关键列完整性与内容非空检查", "rule_to_check": "关键列完整性与内容非空检查"},
        {"id": "3", "category": "模具设计尺寸格式检查", "rule_to_check": "模具设计尺寸格式检查"},
        {"id": "4", "category": "实物照片列检查", "rule_to_check": "实物照片列检查"},
        {"id": "5", "category": "编制与审核字段检查", "rule_to_check": "编制与审核字段检查"},
        {"id": "6", "category": "实物确认列内容检查", "rule_to_check": "实物确认列内容检查"},
    ],
}
