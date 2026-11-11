"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U15",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.records:UaesTrialAgent",
        ".pdf": "audit.checker_lib.uaes.records:UaesTrialAgent",
        ".xls": "audit.checker_lib.uaes.records:UaesTrialAgent",
        ".xlsm": "audit.checker_lib.uaes.records:UaesTrialAgent",
        ".xlsx": "audit.checker_lib.uaes.records:UaesTrialAgent",
    },
    "methods": {"U15:1": "rule_1"},
}
