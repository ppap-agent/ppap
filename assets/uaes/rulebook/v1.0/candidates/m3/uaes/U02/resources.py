"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3, MCMP:1).

The carriers are the UAES measurement comparison readers; checker.py extends them at run time with the revision 3 clause method.
"""

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
    "methods": {"MCMP:1": "mcmp_1"},
}
