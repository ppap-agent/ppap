"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3)."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U12",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.special_characteristics:UaesSpecialCharacteristicsAgent",
        ".xls": "audit.checker_lib.uaes.special_characteristics:UaesSpecialCharacteristicsAgent",
        ".xlsm": "audit.checker_lib.uaes.special_characteristics:UaesSpecialCharacteristicsAgent",
        ".xlsx": "audit.checker_lib.uaes.special_characteristics:UaesSpecialCharacteristicsAgent",
    },
    "methods": {"SCL:1": "rule_1", "SCL:2": "rule_2", "SCL:3": "rule_3"},
}
