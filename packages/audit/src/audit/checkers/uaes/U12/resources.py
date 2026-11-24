"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U12",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.v1.special_characteristics:UaesSpecialCharacteristicsRevision3",
        ".xls": "audit.checker_lib.uaes.v1.special_characteristics:UaesSpecialCharacteristicsRevision3",
        ".xlsm": "audit.checker_lib.uaes.v1.special_characteristics:UaesSpecialCharacteristicsRevision3",
        ".xlsx": "audit.checker_lib.uaes.v1.special_characteristics:UaesSpecialCharacteristicsRevision3",
    },
    "methods": {"SCL:1": "rule_1", "SCL:2": "rule_2", "SCL:3": "rule_3"},
}
