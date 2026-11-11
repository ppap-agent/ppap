"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L05",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.msa_report_agent:MsaReportAgent",
        ".xls": "audit.checker_lib.li.msa_report_agent:MsaReportAgent",
        ".xlsm": "audit.checker_lib.li.msa_report_agent:MsaReportAgent",
        ".pdf": "audit.checker_lib.li.pdf_excel_delegator_agent:MsaReportPdfExcelDelegatorAgent",
    },
    "methods": {"L05:1": "rule_1", "L05:2": "rule_2", "L05:3": "rule_3", "L05:4": "rule_4"},
    "implementation_rules": [
        {"id": "1", "category": '检查"任务项一"和"任务项二"的状态', "rule_to_check": '检查"任务项一"和"任务项二"的状态'},
        {"id": "2", "category": '检查"测量系统分析报告(R&R)"的填写情况', "rule_to_check": '检查"测量系统分析报告(R&R)"的填写情况'},
        {"id": "3", "category": "检查R&R分析报告的数据完整性", "rule_to_check": "检查R&R分析报告的数据完整性"},
        {"id": "4", "category": "检查R&R分析的结论", "rule_to_check": "检查R&R分析的结论"},
    ],
}
