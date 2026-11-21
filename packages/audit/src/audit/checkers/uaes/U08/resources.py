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
    "methods": {"CP:3": "rule_3", "CP:11": "rule_11", "CP:12": "rule_12"},
}
