"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U10",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.v1.material_spec:UaesControlledDocumentAgentR3",
        ".pdf": "audit.checker_lib.uaes.v1.material_spec:UaesControlledDocumentAgentR3",
        ".xls": "audit.checker_lib.uaes.v1.material_spec:UaesControlledDocumentAgentR3",
        ".xlsm": "audit.checker_lib.uaes.v1.material_spec:UaesControlledDocumentAgentR3",
        ".xlsx": "audit.checker_lib.uaes.v1.material_spec:UaesControlledDocumentAgentR3",
    },
    "methods": {"MSPEC:1": "mspec_1"},
}
