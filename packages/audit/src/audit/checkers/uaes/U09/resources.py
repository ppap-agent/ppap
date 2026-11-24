"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U09",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.v1.packaging:UaesPackagingAgentR3",
        ".xls": "audit.checker_lib.uaes.v1.packaging:UaesPackagingAgentR3",
        ".xlsm": "audit.checker_lib.uaes.v1.packaging:UaesPackagingAgentR3",
        ".xlsx": "audit.checker_lib.uaes.v1.packaging:UaesPackagingAgentR3",
        ".docx": "audit.checker_lib.uaes.v1.packaging:UaesPackagingDocxAgentR3",
    },
    "methods": {"PKG:1": "pkg_1"},
}
