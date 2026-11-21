"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U01",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.inspection:UaesInspectionAgent",
        ".xls": "audit.checker_lib.uaes.inspection:UaesInspectionAgent",
        ".xlsm": "audit.checker_lib.uaes.inspection:UaesInspectionAgent",
        ".xlsx": "audit.checker_lib.uaes.inspection:UaesInspectionAgent",
    },
    "methods": {},
}
