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
    "methods": {},
}
