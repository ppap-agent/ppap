"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N15",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.xir_assembly_issue_agent:XirAssemblyIssueAgent",
        ".xls": "audit.checker_lib.nio.xir_assembly_issue_agent:XirAssemblyIssueAgent",
        ".xlsm": "audit.checker_lib.nio.xir_assembly_issue_agent:XirAssemblyIssueAgent",
        ".pdf": "audit.checker_lib.nio.pdf_excel_delegator_agent:XirAssemblyIssuePdfExcelDelegatorAgent",
    },
    "methods": {"N15:1": "rule_1", "N15:2": "rule_2", "N15:3": "rule_3", "N15:4": "rule_4"},
    "implementation_rules": [
        {"id": "1", "category": "基础字段完整性审核", "rule_to_check": "基础字段完整性审核"},
        {"id": "2", "category": "问题关闭状态与描述关联审核", "rule_to_check": "问题关闭状态与描述关联审核"},
        {"id": "3", "category": "整改措施要素完整性及时间有效性审核", "rule_to_check": "整改措施要素完整性及时间有效性审核"},
        {"id": "4", "category": "风险分类标识审核", "rule_to_check": "风险分类标识审核"},
    ],
}
