"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U08",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.control_plan:UaesControlPlanAgent",
        ".pdf": "audit.checker_lib.uaes.control_plan:UaesControlPlanAgent",
        ".xls": "audit.checker_lib.uaes.control_plan:UaesControlPlanAgent",
        ".xlsm": "audit.checker_lib.uaes.control_plan:UaesControlPlanAgent",
        ".xlsx": "audit.checker_lib.uaes.control_plan:UaesControlPlanAgent",
    },
    "methods": {
        "U08:1": "rule_1",
        "U08:2": "rule_2",
        "U08:3": "rule_3",
        "U08:4": "rule_4",
        "U08:6": "rule_5",
        "U08:7": "rule_6",
        "U08:8": "rule_7",
        "U08:9": "rule_8",
        "U08:10": "rule_9",
        "U08:11": "rule_10",
        "U08:12": "rule_11",
        "U08:13": "rule_12",
    },
}
