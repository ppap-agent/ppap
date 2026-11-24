"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U06",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.v1.capability_r4:UaesCapabilityAgentR4",
        ".xls": "audit.checker_lib.uaes.v1.capability_r4:UaesCapabilityAgentR4",
        ".xlsm": "audit.checker_lib.uaes.v1.capability_r4:UaesCapabilityAgentR4",
        ".xlsx": "audit.checker_lib.uaes.v1.capability_r4:UaesCapabilityAgentR4",
    },
    "methods": {"PCE:1": "rule_1", "PCE:2": "rule_2", "PCE:3": "rule_3", "PCE:4": "rule_4", "PCE:5": "rule_5", "PCE:6": "rule_6"},
}
