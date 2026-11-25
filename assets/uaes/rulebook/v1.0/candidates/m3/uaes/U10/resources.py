"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3, MSPEC:1).

The carrier is the UAES controlled-document reader; checker.py extends it at run time with the revision 3 clause method.
"""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U10",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.records:UaesControlledDocumentAgent",
        ".pdf": "audit.checker_lib.uaes.records:UaesControlledDocumentAgent",
        ".xls": "audit.checker_lib.uaes.records:UaesControlledDocumentAgent",
        ".xlsm": "audit.checker_lib.uaes.records:UaesControlledDocumentAgent",
        ".xlsx": "audit.checker_lib.uaes.records:UaesControlledDocumentAgent",
    },
    "methods": {"MSPEC:1": "mspec_1"},
}
