"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U19",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.v1.engineering_approval:ApprovalR3",
        ".xls": "audit.checker_lib.uaes.v1.engineering_approval:ApprovalR3",
        ".xlsm": "audit.checker_lib.uaes.v1.engineering_approval:ApprovalR3",
        ".xlsx": "audit.checker_lib.uaes.v1.engineering_approval:ApprovalR3",
    },
    "methods": {"CEA:1": "rule_1"},
}
