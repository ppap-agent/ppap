"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U07",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.v1.fmea:UaesPfmeaRevision3",
        ".xls": "audit.checker_lib.uaes.v1.fmea:UaesPfmeaRevision3",
        ".xlsm": "audit.checker_lib.uaes.v1.fmea:UaesPfmeaRevision3",
        ".xlsx": "audit.checker_lib.uaes.v1.fmea:UaesPfmeaRevision3",
    },
    "methods": {"PFMEA:1": "rule_1", "PFMEA:2": "rule_2", "PFMEA:3": "rule_3", "PFMEA:4": "rule_4", "PFMEA:5": "rule_5"},
}
