"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3, CEA:1)."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U19",
    "carriers": {
        ".pdf": "checker:ApprovalR3",
        ".xls": "checker:ApprovalR3",
        ".xlsm": "checker:ApprovalR3",
        ".xlsx": "checker:ApprovalR3",
    },
    "methods": {"CEA:1": "rule_1"},
}
