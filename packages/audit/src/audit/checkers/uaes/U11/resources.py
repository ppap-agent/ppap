"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U11",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.records:UaesEngineeringHistoryAgent",
        ".pdf": "audit.checker_lib.uaes.records:UaesEngineeringHistoryAgent",
        ".xls": "audit.checker_lib.uaes.records:UaesEngineeringHistoryAgent",
        ".xlsm": "audit.checker_lib.uaes.records:UaesEngineeringHistoryAgent",
        ".xlsx": "audit.checker_lib.uaes.records:UaesEngineeringHistoryAgent",
    },
    "methods": {"U11:1": "rule_1"},
}
