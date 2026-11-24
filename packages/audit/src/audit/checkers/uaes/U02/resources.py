"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U02",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.v1.measurement_comparison:UaesMeasurementComparisonAgentR3",
        ".xls": "audit.checker_lib.uaes.v1.measurement_comparison:UaesMeasurementComparisonAgentR3",
        ".xlsm": "audit.checker_lib.uaes.v1.measurement_comparison:UaesMeasurementComparisonAgentR3",
        ".xlsx": "audit.checker_lib.uaes.v1.measurement_comparison:UaesMeasurementComparisonAgentR3",
        ".docx": "audit.checker_lib.uaes.v1.measurement_comparison:UaesMeasurementComparisonDocxAgentR3",
    },
    "methods": {"MCMP:1": "mcmp_1"},
}
