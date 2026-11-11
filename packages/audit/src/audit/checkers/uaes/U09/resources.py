"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U09",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.records:UaesPackagingAgent",
        ".pdf": "audit.checker_lib.uaes.records:UaesPackagingAgent",
        ".xls": "audit.checker_lib.uaes.records:UaesPackagingAgent",
        ".xlsm": "audit.checker_lib.uaes.records:UaesPackagingAgent",
        ".xlsx": "audit.checker_lib.uaes.records:UaesPackagingAgent",
    },
    "methods": {"U09:1": "rule_1"},
}
