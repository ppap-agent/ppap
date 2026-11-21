"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U07",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.fmea:UaesPfmeaAgent",
        ".xls": "audit.checker_lib.uaes.fmea:UaesPfmeaAgent",
        ".xlsm": "audit.checker_lib.uaes.fmea:UaesPfmeaAgent",
        ".xlsx": "audit.checker_lib.uaes.fmea:UaesPfmeaAgent",
    },
    "methods": {"U07:1": "rule_1", "U07:2": "rule_2", "U07:3": "rule_3", "U07:4": "rule_4"},
}
