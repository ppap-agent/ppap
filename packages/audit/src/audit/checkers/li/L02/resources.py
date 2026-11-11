"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L02",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.dvp_r_excel_agent:DvpRExcelAgent",
        ".xls": "audit.checker_lib.li.dvp_r_excel_agent:DvpRExcelAgent",
        ".xlsm": "audit.checker_lib.li.dvp_r_excel_agent:DvpRExcelAgent",
        ".pdf": "audit.checker_lib.li.pdf_excel_delegator_agent:DvpRPdfExcelDelegatorAgent",
    },
    "methods": {"L02:1": "rule_1", "L02:2": "rule_2", "L02:3": "rule_3", "L02:4": "rule_4", "L02:5": "rule_5", "L02:6": "rule_6", "L02:7": "rule_7"},
    "implementation_rules": [
        {"id": "1", "category": "检查版本号、修订日期等字段是否存在且不为空", "rule_to_check": "检查版本号、修订日期等字段是否存在且不为空"},
        {"id": "2", "category": "检查试验项目等字段是否存在且不为空", "rule_to_check": "检查试验项目等字段是否存在且不为空"},
        {"id": "3", "category": "检查试验类型是否为PV且状态为硬模件", "rule_to_check": "检查试验类型是否为PV且状态为硬模件"},
        {"id": "4", "category": "检查计划开始和结束日期", "rule_to_check": "检查计划开始和结束日期"},
        {"id": "5", "category": "检查实际开始和结束日期", "rule_to_check": "检查实际开始和结束日期"},
        {"id": "6", "category": "检查实际试验结果", "rule_to_check": "检查实际试验结果"},
        {"id": "7", "category": "检查试验报告编号", "rule_to_check": "检查试验报告编号"},
    ],
}
