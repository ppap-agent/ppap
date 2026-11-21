"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U16",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.engineering:UaesProcessFlowAgent",
        ".xls": "audit.checker_lib.uaes.engineering:UaesProcessFlowAgent",
        ".xlsm": "audit.checker_lib.uaes.engineering:UaesProcessFlowAgent",
        ".xlsx": "audit.checker_lib.uaes.engineering:UaesProcessFlowAgent",
    },
    "methods": {"U16:1": "rule_1", "U16:2": "rule_2", "U16:3": "rule_3"},
}
