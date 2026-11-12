"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U19",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.approval:UaesApprovalAgent",
        ".xls": "audit.checker_lib.uaes.approval:UaesApprovalAgent",
        ".xlsm": "audit.checker_lib.uaes.approval:UaesApprovalAgent",
        ".xlsx": "audit.checker_lib.uaes.approval:UaesApprovalAgent",
    },
    "methods": {"U19:1": "rule_1"},
}
