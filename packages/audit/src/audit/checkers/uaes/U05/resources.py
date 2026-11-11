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
    "methods": {"U05:1-1": "rule_1", "U05:1-2": "rule_2", "U05:2": "rule_3", "U05:3": "rule_4"},
}
