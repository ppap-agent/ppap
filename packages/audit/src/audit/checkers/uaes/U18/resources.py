"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U18",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.records:UaesTraceabilityDocxAgent",
        ".pdf": "audit.checker_lib.uaes.records:UaesTraceabilityAgent",
        ".xls": "audit.checker_lib.uaes.records:UaesTraceabilityAgent",
        ".xlsm": "audit.checker_lib.uaes.records:UaesTraceabilityAgent",
        ".xlsx": "audit.checker_lib.uaes.records:UaesTraceabilityAgent",
    },
    "methods": {"U18:1": "rule_1"},
}
