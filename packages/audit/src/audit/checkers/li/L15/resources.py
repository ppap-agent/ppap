"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L15",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.xir_assembly_issue_agent:XirAssemblyIssueAgent",
        ".xls": "audit.checker_lib.li.xir_assembly_issue_agent:XirAssemblyIssueAgent",
        ".xlsm": "audit.checker_lib.li.xir_assembly_issue_agent:XirAssemblyIssueAgent",
        ".pdf": "audit.checker_lib.li.pdf_excel_delegator_agent:XirAssemblyIssuePdfExcelDelegatorAgent",
    },
    "methods": {"L15:1": "rule_1", "L15:2": "rule_2", "L15:3": "rule_3", "L15:4": "rule_4"},
    "implementation_rules": [
        {"id": "1", "category": "关键字段完整性检查", "rule_to_check": "关键字段完整性检查"},
        {"id": "2", "category": "问题描述完整性检查", "rule_to_check": "问题描述完整性检查"},
        {"id": "3", "category": "对应措施完整性检查", "rule_to_check": "对应措施完整性检查"},
        {"id": "4", "category": "整车/总成功/性能性、SOP+3产能风险、安全性、法律法规风险完整性检查", "rule_to_check": "整车/总成功/性能性、SOP+3产能风险、安全性、法律法规风险完整性检查"},
    ],
}
