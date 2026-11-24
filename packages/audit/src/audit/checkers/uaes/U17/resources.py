"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U17",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.v1.warrant:WarrantR3",
        ".pdf": "audit.checker_lib.uaes.v1.warrant:WarrantR3",
        ".xls": "audit.checker_lib.uaes.v1.warrant:WarrantR3",
        ".xlsm": "audit.checker_lib.uaes.v1.warrant:WarrantR3",
        ".xlsx": "audit.checker_lib.uaes.v1.warrant:WarrantR3",
    },
    "methods": {"PSW:1": "rule_1", "PSW:2": "rule_2", "PSW:3": "rule_3", "PSW:4": "rule_4", "PSW:5": "rule_5", "PSW:6": "rule_6", "PSW:7": "rule_7"},
}
