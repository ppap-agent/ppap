"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U01",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.inspection:UaesInspectionAgent",
        ".pdf": "audit.checker_lib.uaes.inspection:UaesInspectionAgent",
        ".xls": "audit.checker_lib.uaes.inspection:UaesInspectionAgent",
        ".xlsm": "audit.checker_lib.uaes.inspection:UaesInspectionAgent",
        ".xlsx": "audit.checker_lib.uaes.inspection:UaesInspectionAgent",
    },
    "methods": {"U01:1-1": "rule_1", "U01:2": "rule_2", "U01:3": "rule_3", "U01:1-2": "rule_4"},
}
