"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N10",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.initial_process_capacity_agent:InitialProcessCapacityAgent",
        ".xls": "audit.checker_lib.nio.initial_process_capacity_agent:InitialProcessCapacityAgent",
        ".xlsm": "audit.checker_lib.nio.initial_process_capacity_agent:InitialProcessCapacityAgent",
        ".pdf": "audit.checker_lib.nio.pdf_excel_delegator_agent:InitialProcessCapacityPdfExcelDelegatorAgent",
    },
    "methods": {"N10:1": "rule_1", "N10:2": "rule_2", "N10:3": "rule_3"},
    "implementation_rules": [
        {"id": "1", "category": "P阶段Cpk结果列审核", "rule_to_check": "P阶段Cpk结果列审核"},
        {"id": "2", "category": "P阶段结果判定列审核", "rule_to_check": "P阶段结果判定列审核"},
        {"id": "3", "category": "控制图及短期过程能力审核", "rule_to_check": "控制图及短期过程能力审核"},
    ],
}
