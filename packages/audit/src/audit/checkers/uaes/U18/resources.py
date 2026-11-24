"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U18",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.v1.traceability:UaesTraceabilityAgentR3",
        ".xls": "audit.checker_lib.uaes.v1.traceability:UaesTraceabilityAgentR3",
        ".xlsm": "audit.checker_lib.uaes.v1.traceability:UaesTraceabilityAgentR3",
        ".xlsx": "audit.checker_lib.uaes.v1.traceability:UaesTraceabilityAgentR3",
        ".docx": "audit.checker_lib.uaes.v1.traceability:UaesTraceabilityDocxAgentR3",
    },
    "methods": {"TRACE:1": "trace_1"},
}
