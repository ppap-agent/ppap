"""Explicit source readers and clause bindings for U01."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U01",
    "supported_suffixes": (".xlsx", ".xls", ".xlsm", ".docx", ".pdf"),
    "clauses": {"U01:1-1": {"method": "rule_1"}, "U01:1-2": {"method": "rule_4"}, "U01:2": {"method": "rule_2"}, "U01:3": {"method": "rule_3"}},
}
