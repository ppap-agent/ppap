"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3, TA:1).

The carriers are the UAES trial form readers; checker.py extends them at run time with the revision 3 clause method.
"""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U15",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.records:UaesTrialDocxAgent",
        ".pdf": "audit.checker_lib.uaes.records:UaesTrialAgent",
        ".xls": "audit.checker_lib.uaes.records:UaesTrialAgent",
        ".xlsm": "audit.checker_lib.uaes.records:UaesTrialAgent",
        ".xlsx": "audit.checker_lib.uaes.records:UaesTrialAgent",
    },
    "methods": {"TA:1": "ta_1"},
}
