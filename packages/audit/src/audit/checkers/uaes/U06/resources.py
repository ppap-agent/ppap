"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U06",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.engineering:UaesCapabilityAgent",
        ".xls": "audit.checker_lib.uaes.engineering:UaesCapabilityAgent",
        ".xlsm": "audit.checker_lib.uaes.engineering:UaesCapabilityAgent",
        ".xlsx": "audit.checker_lib.uaes.engineering:UaesCapabilityAgent",
    },
    "methods": {"U06:1": "rule_1", "U06:2": "rule_2", "U06:3": "rule_3", "U06:4": "rule_4", "U06:5": "rule_5"},
}
