"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3)."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U07",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.fmea:UaesPfmeaAgent",
        ".xls": "audit.checker_lib.uaes.fmea:UaesPfmeaAgent",
        ".xlsm": "audit.checker_lib.uaes.fmea:UaesPfmeaAgent",
        ".xlsx": "audit.checker_lib.uaes.fmea:UaesPfmeaAgent",
    },
    "methods": {"PFMEA:1": "rule_1", "PFMEA:2": "rule_2", "PFMEA:3": "rule_3", "PFMEA:4": "rule_4", "PFMEA:5": "rule_5"},
}
