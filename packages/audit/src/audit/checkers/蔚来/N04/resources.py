"""N04 source-owned evidence readers; neither method authenticates a drawing."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N04",
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".pdf"),
    "clauses": {"N04:1": {"method": "rule_1"}, "N04:2": {"method": "rule_2"}},
    "known_gaps": {},
}
