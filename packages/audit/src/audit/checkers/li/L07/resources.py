"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L07",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.pfmea_agent:PfmeaAgent",
        ".xls": "audit.checker_lib.li.pfmea_agent:PfmeaAgent",
        ".xlsm": "audit.checker_lib.li.pfmea_agent:PfmeaAgent",
        ".pdf": "audit.checker_lib.li.pdf_excel_delegator_agent:PfmeaPdfExcelDelegatorAgent",
    },
    "methods": {"L07:1": "rule_1", "L07:2": "rule_2", "L07:3": "rule_3", "L07:4": "rule_4", "L07:5": "rule_5", "L07:6": "rule_6", "L07:7": "rule_7"},
    "implementation_rules": [
        {"id": "1", "category": "表头字段完整性检查", "rule_to_check": "表头字段完整性检查"},
        {"id": "2", "category": "PFMEA开始/修订日期与变更履历一致性检查", "rule_to_check": "PFMEA开始/修订日期与变更履历一致性检查"},
        {"id": "3", "category": "过程步骤与工序名称一致性检查", "rule_to_check": "过程步骤与工序名称一致性检查"},
        {"id": "4", "category": "过程项功能维度检查", "rule_to_check": "过程项功能维度检查"},
        {"id": "5", "category": "特殊特性一致性检查", "rule_to_check": "特殊特性一致性检查"},
        {"id": "6", "category": "失效模式、失效影响、失效严重度维度检查", "rule_to_check": "失效模式、失效影响、失效严重度维度检查"},
        {"id": "7", "category": "现行探测控制及探测度维度检查", "rule_to_check": "现行探测控制及探测度维度检查"},
    ],
}
