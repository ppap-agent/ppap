"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N06",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.dvp_r_excel_agent:DvpRExcelAgent",
        ".xls": "audit.checker_lib.nio.dvp_r_excel_agent:DvpRExcelAgent",
        ".xlsm": "audit.checker_lib.nio.dvp_r_excel_agent:DvpRExcelAgent",
        ".pdf": "audit.checker_lib.nio.pdf_excel_delegator_agent:DvpRPdfExcelDelegatorAgent",
    },
    "methods": {"N06:1": "rule_1", "N06:2": "rule_2", "N06:3": "rule_3", "N06:4": "rule_4", "N06:5": "rule_5", "N06:6": "rule_6", "N06:7": "rule_7"},
    "implementation_rules": [
        {"id": "1", "category": "基础信息字段完整性审核", "rule_to_check": "基础信息字段完整性审核"},
        {"id": "2", "category": "试验计划列完整性审核", "rule_to_check": "试验计划列完整性审核"},
        {"id": "3", "category": "PV试验样本状态一致性审核", "rule_to_check": "PV试验样本状态一致性审核"},
        {"id": "4", "category": "计划时间逻辑审核", "rule_to_check": "计划时间逻辑审核"},
        {"id": "5", "category": "实际试验时间逻辑审核", "rule_to_check": "实际试验时间逻辑审核"},
        {"id": "6", "category": "试验结果有效性审核", "rule_to_check": "试验结果有效性审核"},
        {"id": "7", "category": "试验报告编号完整性审核", "rule_to_check": "试验报告编号完整性审核"},
    ],
}
