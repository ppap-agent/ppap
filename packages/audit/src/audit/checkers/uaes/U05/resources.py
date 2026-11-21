"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U05",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.msa:UaesMsaAgent",
        ".pdf": "audit.checker_lib.uaes.msa:UaesMsaAgent",
        ".xls": "audit.checker_lib.uaes.msa:UaesMsaAgent",
        ".xlsm": "audit.checker_lib.uaes.msa:UaesMsaAgent",
        ".xlsx": "audit.checker_lib.uaes.msa:UaesMsaAgent",
    },
    "methods": {},
}
