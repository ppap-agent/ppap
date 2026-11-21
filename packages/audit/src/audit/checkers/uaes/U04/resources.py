"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U04",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.materials:UaesMaterialsAgent",
        ".xls": "audit.checker_lib.uaes.materials:UaesMaterialsAgent",
        ".xlsm": "audit.checker_lib.uaes.materials:UaesMaterialsAgent",
        ".xlsx": "audit.checker_lib.uaes.materials:UaesMaterialsAgent",
    },
    "methods": {"MSL:3": "rule_3"},
}
