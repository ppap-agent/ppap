"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N09",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.msa_report_agent:MsaReportAgent",
        ".xls": "audit.checker_lib.nio.msa_report_agent:MsaReportAgent",
        ".xlsm": "audit.checker_lib.nio.msa_report_agent:MsaReportAgent",
        ".pdf": "audit.checker_lib.nio.pdf_excel_delegator_agent:MsaReportPdfExcelDelegatorAgent",
    },
    "methods": {"N09:1": "rule_1", "N09:2": "rule_2", "N09:3": "rule_3", "N09:4": "rule_4"},
    "implementation_rules": [
        {"id": "1", "category": "任务项字段识别与状态审核", "rule_to_check": "任务项字段识别与状态审核"},
        {"id": "2", "category": "测量系统分析计划表完整性审核", "rule_to_check": "测量系统分析计划表完整性审核"},
        {"id": "3", "category": "量具重复性和再现性样本数量审核", "rule_to_check": "量具重复性和再现性样本数量审核"},
        {"id": "4", "category": "量具重复性和再现性结论判定审核", "rule_to_check": "量具重复性和再现性结论判定审核"},
    ],
}
