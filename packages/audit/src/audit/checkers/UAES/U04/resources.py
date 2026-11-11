"""Explicit source readers and clause bindings for U04."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U04",
    "supported_suffixes": (".xlsx", ".xls", ".xlsm", ".docx", ".pdf"),
    "clauses": {"U04:1": {"method": "rule_1"}, "U04:2": {"method": "rule_2"}, "U04:3": {"method": "rule_3"}},
}
