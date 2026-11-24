"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U04",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.v1.materials:UaesMaterialsAgentR3",
        ".xls": "audit.checker_lib.uaes.v1.materials:UaesMaterialsAgentR3",
        ".xlsm": "audit.checker_lib.uaes.v1.materials:UaesMaterialsAgentR3",
        ".xlsx": "audit.checker_lib.uaes.v1.materials:UaesMaterialsAgentR3",
    },
    "methods": {"MSL:1": "msl_1", "MSL:2": "msl_2", "MSL:3": "msl_3", "MSL:4": "msl_4"},
}
