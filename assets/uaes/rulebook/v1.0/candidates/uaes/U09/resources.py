"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3, PKG:1).

The carriers are the UAES packaging readers; checker.py extends them at run time with the revision 3 clause method.
"""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U09",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.records:UaesPackagingDocxAgent",
        ".pdf": "audit.checker_lib.uaes.records:UaesPackagingAgent",
        ".xls": "audit.checker_lib.uaes.records:UaesPackagingAgent",
        ".xlsm": "audit.checker_lib.uaes.records:UaesPackagingAgent",
        ".xlsx": "audit.checker_lib.uaes.records:UaesPackagingAgent",
    },
    "methods": {"PKG:1": "pkg_1"},
}
