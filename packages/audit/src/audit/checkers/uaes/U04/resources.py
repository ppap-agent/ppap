"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U04",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.materials:UaesMaterialsAgent",
        ".pdf": "audit.checker_lib.uaes.materials:UaesMaterialsAgent",
        ".xls": "audit.checker_lib.uaes.materials:UaesMaterialsAgent",
        ".xlsm": "audit.checker_lib.uaes.materials:UaesMaterialsAgent",
        ".xlsx": "audit.checker_lib.uaes.materials:UaesMaterialsAgent",
    },
    "methods": {"U04:1": "rule_1", "U04:2": "rule_2", "U04:3": "rule_3"},
}
