"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U12",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.special_characteristics:UaesSpecialCharacteristicsAgent",
        ".pdf": "audit.checker_lib.uaes.special_characteristics:UaesSpecialCharacteristicsAgent",
        ".xls": "audit.checker_lib.uaes.special_characteristics:UaesSpecialCharacteristicsAgent",
        ".xlsm": "audit.checker_lib.uaes.special_characteristics:UaesSpecialCharacteristicsAgent",
        ".xlsx": "audit.checker_lib.uaes.special_characteristics:UaesSpecialCharacteristicsAgent",
    },
    "methods": {"U12:1": "rule_1", "U12:2": "rule_2"},
}
