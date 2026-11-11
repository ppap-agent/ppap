"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U17",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.warrant:UaesWarrantAgent",
        ".pdf": "audit.checker_lib.uaes.warrant:UaesWarrantAgent",
        ".xls": "audit.checker_lib.uaes.warrant:UaesWarrantAgent",
        ".xlsm": "audit.checker_lib.uaes.warrant:UaesWarrantAgent",
        ".xlsx": "audit.checker_lib.uaes.warrant:UaesWarrantAgent",
    },
    "methods": {"U17:1": "rule_1", "U17:2": "rule_2", "U17:3": "rule_3", "U17:4": "rule_4", "U17:5": "rule_5"},
}
