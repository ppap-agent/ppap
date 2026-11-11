"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L06",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.initial_process_capacity_agent:InitialProcessCapacityAgent",
        ".xls": "audit.checker_lib.li.initial_process_capacity_agent:InitialProcessCapacityAgent",
        ".xlsm": "audit.checker_lib.li.initial_process_capacity_agent:InitialProcessCapacityAgent",
        ".pdf": "audit.checker_lib.li.pdf_excel_delegator_agent:InitialProcessCapacityPdfExcelDelegatorAgent",
    },
    "methods": {"L06:1": "rule_1", "L06:2": "rule_2", "L06:3": "rule_3"},
    "implementation_rules": [
        {"id": "1", "category": "P阶段Cpk结果列内容检查", "rule_to_check": "P阶段Cpk结果列内容检查"},
        {"id": "2", "category": "P阶段结果判定列内容检查", "rule_to_check": "P阶段结果判定列内容检查"},
        {"id": "3", "category": "控制图短期过程能力检查", "rule_to_check": "控制图短期过程能力检查"},
    ],
}
