"""Explicit source-clause methods; no inferred ordinal binding."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U16",
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".pdf", ".docx"),
    "clauses": {"U16:1": {"method": "rule_1"}, "U16:2": {"method": "rule_2"}, "U16:3": {"method": "rule_3"}},
}
