"""Explicit source readers for this UAES rule group."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U17",
    "supported_suffixes": (".xlsx", ".xls", ".xlsm", ".docx", ".pdf"),
    "clauses": {"U17:1": {"method": "rule_1"}, "U17:2": {"method": "rule_2"}, "U17:3": {"method": "rule_3"}, "U17:4": {"method": "rule_4"}, "U17:5": {"method": "rule_5"}},
}
