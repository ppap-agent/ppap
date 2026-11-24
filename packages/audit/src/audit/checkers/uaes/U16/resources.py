"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U16",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.v1.process_flow:UaesProcessFlowRevision3",
        ".xls": "audit.checker_lib.uaes.v1.process_flow:UaesProcessFlowRevision3",
        ".xlsm": "audit.checker_lib.uaes.v1.process_flow:UaesProcessFlowRevision3",
        ".xlsx": "audit.checker_lib.uaes.v1.process_flow:UaesProcessFlowRevision3",
    },
    "methods": {"PFD:1": "rule_1", "PFD:2": "rule_2", "PFD:3": "rule_3", "PFD:4": "rule_4"},
}
