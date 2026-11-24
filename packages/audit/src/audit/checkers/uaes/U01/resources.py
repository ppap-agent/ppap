"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U01",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.v1.inspection:InspectionR3",
        ".xls": "audit.checker_lib.uaes.v1.inspection:InspectionR3",
        ".xlsm": "audit.checker_lib.uaes.v1.inspection:InspectionR3",
        ".xlsx": "audit.checker_lib.uaes.v1.inspection:InspectionR3",
    },
    "methods": {"IR:1": "rule_1", "IR:2": "rule_2", "IR:3": "rule_3", "IR:4": "rule_4"},
}
