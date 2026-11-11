"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N02",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.pfmea_agent:PfmeaAgent",
        ".xls": "audit.checker_lib.nio.pfmea_agent:PfmeaAgent",
        ".xlsm": "audit.checker_lib.nio.pfmea_agent:PfmeaAgent",
        ".pdf": "audit.checker_lib.nio.pdf_excel_delegator_agent:PfmeaPdfExcelDelegatorAgent",
    },
    "methods": {
        "N02:1": "rule_1_tablehead_keywords",
        "N02:2": "rule_2_pfmea_start_revision_date",
        "N02:3": "rule_3_process_item_function_dimensions",
        "N02:4": "rule_4_failure_effect_dimensions_severity",
        "N02:5": "rule_5_current_detection_controls_detection",
        "N02:6": "rule_6_process_steps_consistency",
        "N02:7": "rule_7_special_characteristics_consistency",
    },
    "implementation_rules": [
        {"id": "1", "category": "表头字段完整性审核", "rule_to_check": "表头字段完整性审核"},
        {"id": "2", "category": "PFMEA与变更履历一致性审核", "rule_to_check": "PFMEA与变更履历一致性审核"},
        {"id": "3", "category": "过程项功能维度审核", "rule_to_check": "过程项功能维度审核"},
        {"id": "4", "category": "失效影响及严重度审核", "rule_to_check": "失效影响及严重度审核"},
        {"id": "5", "category": "现行探测控制及探测度审核", "rule_to_check": "现行探测控制及探测度审核"},
        {"id": "6", "category": "流程一致性审核", "rule_to_check": "流程一致性审核"},
        {"id": "7", "category": "特性一致性审核", "rule_to_check": "特性一致性审核"},
    ],
}
