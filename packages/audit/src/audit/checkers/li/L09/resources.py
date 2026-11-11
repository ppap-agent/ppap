"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L09",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.checking_fixture_approval_excel_agent:CheckingFixtureApprovalExcelAgent",
        ".xls": "audit.checker_lib.li.checking_fixture_approval_excel_agent:CheckingFixtureApprovalExcelAgent",
        ".xlsm": "audit.checker_lib.li.checking_fixture_approval_excel_agent:CheckingFixtureApprovalExcelAgent",
        ".pdf": "audit.checker_lib.li.checking_fixture_approval_agent:CheckingFixtureApprovalAgent",
    },
    "methods": {"L09:1": "rule_1", "L09:2": "rule_2", "L09:3": "rule_3"},
    "implementation_rules": [
        {"id": "1", "category": "文件完整性检查 (Completeness Check)", "rule_to_check": "文件完整性检查 (Completeness Check)"},
        {"id": "2", "category": "前缀检查 (Prefix Check)", "rule_to_check": "前缀检查 (Prefix Check)"},
        {"id": "3", "category": "不合格项与结论的关联性检查 (Correlation Check)", "rule_to_check": "不合格项与结论的关联性检查 (Correlation Check)"},
    ],
}
