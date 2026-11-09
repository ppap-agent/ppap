"""Explicit source readers and clause bindings for U06."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U06",
    "supported_suffixes": (".xlsx", ".xls", ".xlsm", ".docx", ".pdf"),
    "clauses": {"U06:1": {"method": "rule_1"}, "U06:2": {"method": "rule_2"}, "U06:3": {"method": "rule_3"}, "U06:4": {"method": "rule_4"}, "U06:5": {"method": "rule_5"}},
}
