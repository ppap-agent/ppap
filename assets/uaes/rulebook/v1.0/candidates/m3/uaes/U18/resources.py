"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3, TRACE:1).

The carriers are the UAES traceability readers; checker.py extends them at run time with the revision 3 clause method.
"""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U18",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.records:UaesTraceabilityDocxAgent",
        ".pdf": "audit.checker_lib.uaes.records:UaesTraceabilityAgent",
        ".xls": "audit.checker_lib.uaes.records:UaesTraceabilityAgent",
        ".xlsm": "audit.checker_lib.uaes.records:UaesTraceabilityAgent",
        ".xlsx": "audit.checker_lib.uaes.records:UaesTraceabilityAgent",
    },
    "methods": {"TRACE:1": "trace_1"},
}
