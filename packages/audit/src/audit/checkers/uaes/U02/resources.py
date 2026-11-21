"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U02",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.records:UaesMeasurementComparisonDocxAgent",
        ".pdf": "audit.checker_lib.uaes.records:UaesMeasurementComparisonAgent",
        ".xls": "audit.checker_lib.uaes.records:UaesMeasurementComparisonAgent",
        ".xlsm": "audit.checker_lib.uaes.records:UaesMeasurementComparisonAgent",
        ".xlsx": "audit.checker_lib.uaes.records:UaesMeasurementComparisonAgent",
    },
    "methods": {"U02:1": "rule_1"},
}
