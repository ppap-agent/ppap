"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N11",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.checking_fixture_approval_excel_agent:CheckingFixtureApprovalExcelAgent",
        ".xls": "audit.checker_lib.nio.checking_fixture_approval_excel_agent:CheckingFixtureApprovalExcelAgent",
        ".xlsm": "audit.checker_lib.nio.checking_fixture_approval_excel_agent:CheckingFixtureApprovalExcelAgent",
        ".pdf": "audit.checker_lib.nio.checking_fixture_approval_agent:CheckingFixtureApprovalAgent",
    },
    "methods": {"N11:1": "rule_1", "N11:2": "rule_2", "N11:3": "rule_3"},
    "implementation_rules": [
        {"id": "1", "category": "检具清单与认可报告完整性审核", "rule_to_check": "检具清单与认可报告完整性审核"},
        {"id": "2", "category": "检具清单标题规范性审核", "rule_to_check": "检具清单标题规范性审核"},
        {"id": "3", "category": "所附文件与结论一致性审核", "rule_to_check": "所附文件与结论一致性审核"},
    ],
}
