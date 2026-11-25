"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3)."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U16",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.engineering:UaesProcessFlowAgent",
        ".xls": "audit.checker_lib.uaes.engineering:UaesProcessFlowAgent",
        ".xlsm": "audit.checker_lib.uaes.engineering:UaesProcessFlowAgent",
        ".xlsx": "audit.checker_lib.uaes.engineering:UaesProcessFlowAgent",
    },
    "methods": {"PFD:1": "rule_1", "PFD:2": "rule_2", "PFD:3": "rule_3", "PFD:4": "rule_4"},
}
