"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3, SSH:1-SSH:2)."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U11",
    "carriers": {
        ".docx": "checker:SampleHistoryR3",
        ".pdf": "checker:SampleHistoryR3",
        ".xls": "checker:SampleHistoryR3",
        ".xlsm": "checker:SampleHistoryR3",
        ".xlsx": "checker:SampleHistoryR3",
    },
    "methods": {"SSH:1": "rule_1", "SSH:2": "rule_2"},
}
