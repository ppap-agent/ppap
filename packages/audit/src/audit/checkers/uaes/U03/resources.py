"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U03",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.records:UaesLabQualificationDocxAgent",
        ".pdf": "audit.checker_lib.uaes.records:UaesLabQualificationAgent",
        ".xls": "audit.checker_lib.uaes.records:UaesLabQualificationAgent",
        ".xlsm": "audit.checker_lib.uaes.records:UaesLabQualificationAgent",
        ".xlsx": "audit.checker_lib.uaes.records:UaesLabQualificationAgent",
    },
    "methods": {"U03:1": "rule_1"},
}
