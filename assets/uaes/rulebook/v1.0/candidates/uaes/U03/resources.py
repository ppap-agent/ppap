"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3, LAB:1).

The carriers are the UAES laboratory certificate readers; checker.py extends them at run time with the revision 3 clause method.
"""

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
    "methods": {"LAB:1": "lab_1"},
}
