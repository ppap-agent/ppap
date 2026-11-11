"""Explicit source readers and clause bindings for U05."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U05",
    "supported_suffixes": (".xlsx", ".xls", ".xlsm", ".docx", ".pdf"),
    "clauses": {"U05:1-1": {"method": "rule_1"}, "U05:1-2": {"method": "rule_2"}, "U05:2": {"method": "rule_3"}, "U05:3": {"method": "rule_4"}},
}
