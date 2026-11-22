"""Implementation and clause methods this leaf runs."""

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
    "methods": {"U10:1": "rule_1"},
}
