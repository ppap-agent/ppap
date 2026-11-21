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
    "methods": {},
}
