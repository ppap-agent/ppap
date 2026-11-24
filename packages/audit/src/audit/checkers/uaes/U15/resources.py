"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U15",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.v1.trial:UaesTrialAgentR3",
        ".xls": "audit.checker_lib.uaes.v1.trial:UaesTrialAgentR3",
        ".xlsm": "audit.checker_lib.uaes.v1.trial:UaesTrialAgentR3",
        ".xlsx": "audit.checker_lib.uaes.v1.trial:UaesTrialAgentR3",
        ".docx": "audit.checker_lib.uaes.v1.trial:UaesTrialDocxAgentR3",
    },
    "methods": {"TA:1": "ta_1"},
}
