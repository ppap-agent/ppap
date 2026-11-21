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
    "methods": {},
}
